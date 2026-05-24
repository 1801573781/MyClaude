"""
h2m — HTML to Markdown 转换工具

将 MyClaude Session Log（HTML 格式）转换为纯净的 Markdown 文件。
支持按轮次（Turn）和小节（Section）筛选内容。

可作为模块被 mycli.py 调用，也可作为独立脚本运行。
"""

from html.parser import HTMLParser
from pathlib import Path
import html as html_module
import re
import shlex
import sys
from typing import Optional


class _SessionHTMLParser(HTMLParser):
    """
    解析 Session Log HTML，提取结构化数据。

    解析结果存储在 self.result 中，结构如下：
    {
        "session_title": str,
        "session_time": str,
        "turns": [
            {
                "turn_num": int,
                "sections": [
                    {"title": str, "content": str},
                    ...
                ]
            },
            ...
        ]
    }

    解析策略：
    - 使用 _section_depth 跟踪 section-fold 嵌套层次，只在第1层创建 section。
    - memory-summary / memory-fold 等子元素的 <summary> 不会创建新 section。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.result: dict = {"session_title": "", "session_time": "", "turns": []}
        self._current_turn: Optional[dict] = None
        self._current_section: Optional[dict] = None
        self._in_pre = False
        self._pre_content: list[str] = []
        # div 嵌套深度计数器：遇到 <div> 加1，遇到 </div> 减1
        self._div_depth = 0
        # 当前活跃的 section-fold 所在的 div 深度（0 表示不在任何 section-fold 内）
        self._section_fold_depth = 0
        # 标记当前是否在标题元素内
        self._in_session_title = False
        self._in_session_time = False
        self._in_entry_header = False
        self._in_section_summary = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")

        if tag == "div":
            self._div_depth += 1

        if tag == "pre":
            self._in_pre = True
            self._pre_content = []
        elif cls == "session-title":
            self._in_session_title = True
        elif cls == "session-time":
            self._in_session_time = True
        elif cls == "entry-header":
            self._in_entry_header = True
        elif cls == "section-fold":
            # 记录当前 section-fold 所在的 div 深度
            self._section_fold_depth = self._div_depth
        elif cls == "section-summary" and self._section_fold_depth > 0 and self._div_depth == self._section_fold_depth:
            # 只有第1层 section-fold 的 section-summary 才作为顶级 section 标题
            self._in_section_summary = True


    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self._in_pre = False
            pre_text = "".join(self._pre_content).strip()
            if self._current_section is not None:
                if self._current_section.get("content"):
                    self._current_section["content"] += "\n" + pre_text
                else:
                    self._current_section["content"] = pre_text
            self._pre_content = []
        elif tag == "summary":
            # summary 标签闭合时确保标志重置
            self._in_section_summary = False

        if tag == "div":
            # 当 div 深度回到 section_fold_depth 时，离开该 section-fold
            if self._section_fold_depth > 0 and self._div_depth == self._section_fold_depth:
                self._section_fold_depth = 0
            self._div_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_pre:
            self._pre_content.append(data)
            return

        if self._in_session_title:
            self.result["session_title"] = data.strip()
            self._in_session_title = False
        elif self._in_session_time:
            self.result["session_time"] = data.strip()
            self._in_session_time = False
        elif self._in_entry_header:
            # 提取 Turn 编号，格式如 "🔄 Turn 1" 或 "Turn 1"
            match = re.search(r"Turn\s*(\d+)", data, re.IGNORECASE)
            if match:
                turn_num = int(match.group(1))
                self._current_turn = {"turn_num": turn_num, "sections": []}
                self.result["turns"].append(self._current_turn)
                self._current_section = None
                self._in_entry_header = False
        elif self._in_section_summary:
            title = data.strip()
            if title and self._current_turn is not None:
                self._current_section = {"title": title, "content": ""}
                self._current_turn["sections"].append(self._current_section)
            # 只在有实质内容时退出，防止空白 data 导致过早重置
            if title:
                self._in_section_summary = False


def _resolve_path(filename: str, logs_root: str) -> Path:
    """
    解析文件路径。

    如果 filename 是纯文件名（不含路径分隔符），拼接到 logs_root；
    如果是全路径，直接使用。
    """
    p = Path(filename)
    if p.is_absolute():
        return p
    # 纯文件名，拼接到 logs_root
    return Path(logs_root) / filename


def _parse_turns(turns_str: str) -> list[int]:
    """解析 p3 参数，如 "t1,t2,t3" → [1, 2, 3]"""
    if not turns_str:
        return []
    result: list[int] = []
    for part in turns_str.split(","):
        part = part.strip()
        match = re.match(r"t(\d+)", part, re.IGNORECASE)
        if match:
            result.append(int(match.group(1)))
    return result


def _parse_sections(sections_str: str) -> list[str]:
    """解析 p4 参数，如 "用户输入,LLM 应答" → ["用户输入", "LLM 应答"]"""
    if not sections_str:
        return []
    return [s.strip() for s in sections_str.split(",") if s.strip()]


def _strip_emoji_prefix(text: str) -> str:
    """去除文本开头的 emoji/符号和空格，如 '🧠 记忆召回' → '记忆召回'"""
    return re.sub(r'^[^\w\u4e00-\u9fff]+\s*', '', text).strip()


def _format_memory_content(content: str) -> str:
    """
    对"记忆召回" section 的内容做后处理：
    1. 每条记忆条目添加父节点，格式如 `- 记忆1（相关性: 0.85）`
    2. 删除子条目（原 pre 文本内）中重复的 `(相关性: X.XX)`
    """
    # 确保第一条记忆前也有 "\n- [id=" 分隔标记，使 split 能匹配所有条目
    if not content.startswith("\n"):
        content = "\n" + content

    # 按记忆条目分割：以 "\n- [id=" 开头的行为分隔
    entries = re.split(r"(\n- \[id=)", content)
    if len(entries) <= 3:
        return content.strip()  # 无记忆条目，原样返回

    result_lines: list[str] = []
    # entries[0] 是前置文本（空或系统提醒），跳过

    # 后续每两个元素构成一个完整条目
    idx = 1
    memory_num = 0
    while idx < len(entries):
        memory_num += 1
        raw_entry = entries[idx] + (entries[idx + 1] if idx + 1 < len(entries) else "")
        match = re.search(r"\(相关性:\s*([\d.]+)\)", raw_entry)
        relevance = match.group(1) if match else "?"
        # 删除子条目中的 (相关性: X.XX)，保留缩进
        clean_entry = re.sub(r"\s*\(相关性:\s*[\d.]+\)", "", raw_entry)
        # 添加父节点
        result_lines.append(f"\n- 记忆{memory_num}（相关性: {relevance}）")
        # 添加子条目内容（保持原有缩进）
        for line in clean_entry.split("\n"):
            if line.strip():
                result_lines.append(line)
        idx += 2

    return "\n".join(result_lines)


def _build_markdown(
    data: dict,
    turns: list[int],
    sections: list[str],
) -> str:
    """
    根据筛选条件，从结构化数据生成 Markdown。
    """
    lines: list[str] = []

    # 标题
    title = data.get("session_title", "")
    if title:
        clean_title = re.sub(r"^[^\w#]+", "", title).strip()
        lines.append(f"# {clean_title}")
    else:
        lines.append("# Untitled")

    # 时间
    time_str = data.get("session_time", "")
    if time_str:
        lines.append(f"\n{time_str}")

    all_turns: list[dict] = data.get("turns", [])

    # 如果没有 turns 数据，直接返回
    if not all_turns:
        lines.append("\n*（无内容）*")
        return "\n".join(lines)

    # 筛选 turns
    if turns:
        existing_turn_nums = {t["turn_num"] for t in all_turns}
        specified_exist = any(tn in existing_turn_nums for tn in turns)
        if not specified_exist:
            target_turns = all_turns
        else:
            target_turns = [t for t in all_turns if t["turn_num"] in turns]
    else:
        target_turns = all_turns

    for turn in target_turns:
        lines.append(f"\n## Turn {turn['turn_num']}")

        turn_sections: list[dict] = turn.get("sections", [])

        if not turn_sections:
            lines.append("\n*（无内容）*")
            continue

        # 筛选 sections（去 emoji 后比较）
        if sections:
            stripped_map = {s["title"]: _strip_emoji_prefix(s["title"]) for s in turn_sections}
            existing_stripped = set(stripped_map.values())
            specified_exist = any(st in existing_stripped for st in sections)
            if not specified_exist:
                target_sections = turn_sections
            else:
                target_sections = [s for s in turn_sections if stripped_map[s["title"]] in sections]
        else:
            target_sections = turn_sections

        for sec in target_sections:
            lines.append(f"\n### {sec['title']}")

            content = sec.get("content", "")
            if content:
                stripped_title = _strip_emoji_prefix(sec['title'])
                if stripped_title == "记忆召回":
                    content = _format_memory_content(content)
                lines.append(f"\n```\n{content}\n```")
            else:
                lines.append("\n*（无内容）*")

    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def convert_html_to_markdown(
    source: str,
    dest: str,
    turns_str: Optional[str] = None,
    sections_str: Optional[str] = None,
    logs_root: Optional[str] = None,
) -> str:
    """
    将 Session Log HTML 文件转换为 Markdown 文件。

    Args:
        source: 源 HTML 文件名或全路径。
        dest: 目标 Markdown 文件名或全路径。
        turns_str: p3 参数，如 "t1,t2"。
        sections_str: p4 参数，如 "用户输入"。
        logs_root: 日志目录路径，None 则从配置读取。

    Returns:
        结果消息字符串。
    """
    if logs_root is None:
        try:
            from src.utility.config_loader import load_config
            cfg = load_config()
            logs_root = cfg.base_path.logs_root
        except Exception:
            logs_root = "D:/AI/MyClaude/log"

    turns = _parse_turns(turns_str or "")
    sections = _parse_sections(sections_str or "")

    source_path = _resolve_path(source, logs_root)
    dest_path = _resolve_path(dest, logs_root)

    if not source_path.exists():
        return f"[ERROR] 源文件不存在: {source_path}"

    try:
        html_content = source_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"[ERROR] 读取源文件失败: {e}"

    parser = _SessionHTMLParser()
    try:
        parser.feed(html_content)
    except Exception as e:
        return f"[ERROR] HTML 解析失败: {e}"

    md_content = _build_markdown(parser.result, turns, sections)

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(md_content, encoding="utf-8")
    except Exception as e:
        return f"[ERROR] 写入目标文件失败: {e}"

    return f"✅ 转换完成: {source_path} → {dest_path}"


def main() -> None:
    """独立命令行运行入口。

    用法:
        python -m src.cli.h2m <p1> <p2> [<p3>] [<p4>]

    参数:
        p1: 源 HTML 文件
        p2: 目标 Markdown 文件
        p3: 轮次筛选（如 t1,t2）
        p4: 小节筛选（如 用户输入）

    参数值如果包含空格，用双引号或单引号包裹。

    示例:
        python -m src.cli.h2m session.html output.md
        python -m src.cli.h2m "MyClaude 2026-05-24 09-52-32.html" output.md t1
        python -m src.cli.h2m session.html output.md t1 "用户输入,LLM 应答"
    """
    args = sys.argv[1:]
    if len(args) < 2:
        print("用法: python -m src.cli.h2m <p1> <p2> [<p3>] [<p4>]")
        print('示例: python -m src.cli.h2m session.html output.md t1 "用户输入"')
        sys.exit(1)

    p1 = args[0]
    p2 = args[1]
    p3 = args[2] if len(args) > 2 else None
    p4 = args[3] if len(args) > 3 else None

    result = convert_html_to_markdown(p1, p2, p3, p4)
    print(result)


if __name__ == "__main__":
    main()