from src.utility.config_loader import global_cfg
from src.utility.file_tool import file_view, file_create, file_str_replace
from src.llm_tool.cmd_bash import tool_bash

import re


def _quick_balance_check(text: str) -> int:
    """快速检查文本的结构平衡性。

    返回正数表示内容可能不完整（有未闭合的括号/引号）。
    用于判断一个候选闭标签是否在内容内部（应跳过）还是真正的闭标签。

    检查：
    - 括号/花括号/方括号的平衡
    - 字符串字面量是否闭合（包括三引号）
    - 若仍在字符串内，判定内容不完整
    """
    bracket_balance = 0
    in_string = False
    string_char = None  # '"', "'", '"""', "'''"
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == '\\':
                i += 2
                continue
            if string_char in ('"""', "'''"):
                if text[i:i + 3] == string_char:
                    in_string = False
                    i += 3
                    continue
            elif ch == string_char:
                in_string = False
                i += 1
                continue
            i += 1
            continue
        # 检查三引号开标签
        if text[i:i + 3] in ('"""', "'''"):
            in_string = True
            string_char = text[i:i + 3]
            i += 3
            continue
        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            i += 1
            continue
        if ch in ('{', '[', '('):
            bracket_balance += 1
        elif ch in ('}', ']', ')'):
            bracket_balance -= 1
        i += 1
    if in_string:
        return bracket_balance + 1  # 未闭合字符串，内容不完整
    return bracket_balance


def _find_container_end(response: str, content_start: int,
                        open_tag_prefix: str, close_tag: str) -> int:
    """嵌套感知的容器闭合标签查找器。

    从 content_start 位置开始，跟踪同名标签的嵌套深度，
    找到与当前开标签匹配的闭标签位置。

    解决两大核心问题：
    1. 当容器内容中包含同名的开/闭标签对时，深度跟踪正确处理。
    2. 当容器内容中包含孤立的同名闭标签（如字符串中的 "</create>"），
       通过结构平衡检查 + 行首启发式判断来跳过假闭标签。

    验证策略（仅在存在多个候选闭标签时激活，避免误伤）：
    - 结构平衡检查：内容若存在未闭合的字符串/括号，闭标签在内容内部，跳过。
    - 行首启发式：真正的闭标签通常独占一行；若闭标签前有非空白内容，跳过。
    """
    depth = 1
    search_pos = content_start

    while depth > 0 and search_pos < len(response):
        next_open = response.find(open_tag_prefix, search_pos)
        next_close = response.find(close_tag, search_pos)

        if next_close == -1:
            return -1

        if next_open != -1 and next_open < next_close:
            # 发现一个开标签前缀——检查是否为真正的开标签（而非子串）
            after_open = next_open + len(open_tag_prefix)
            if after_open < len(response) and response[after_open] in (' ', '>', '/', '\n', '\t', '\r'):
                depth += 1
            search_pos = after_open
        else:
            # 发现一个闭标签
            depth -= 1
            if depth == 0:
                after_close = next_close + len(close_tag)

                # 检查后面是否还有同名的闭标签
                next_close_after = response.find(close_tag, after_close)

                if next_close_after != -1:
                    # 存在更多候选闭标签，需验证当前闭标签是否为真正的闭标签

                    # 验证1：结构平衡检查
                    # 如果内容结构不完整（未闭合的字符串/括号），
                    # 说明此闭标签在内容内部（如字符串字面量），跳过
                    content_so_far = response[content_start:next_close]
                    balance = _quick_balance_check(content_so_far)

                    if balance != 0:
                        # 内容不完整，此闭标签极可能是内容的一部分
                        search_pos = after_close
                        depth = 1  # 恢复深度，继续搜索
                        continue

                    # 验证2：行首启发式
                    # 真正的闭标签通常独占一行，前面只有空白
                    line_start = response.rfind('\n', content_start, next_close)
                    if line_start == -1:
                        before_on_line = response[content_start:next_close]
                    else:
                        before_on_line = response[line_start + 1:next_close]

                    if before_on_line.strip():
                        # 闭标签前有非空白内容，可能是嵌入在内容中的假闭标签
                        search_pos = after_close
                        depth = 1
                        continue

                # 通过所有验证，或只有一个候选闭标签（信任它）
                return after_close
            search_pos = next_close + len(close_tag)

    return -1


def _extract_subtag_content(block: str, tag_name: str):
    """从块文本中提取子标签内容，支持嵌套感知。

    例如从 str_replace 块中提取 old 和 new 子标签的内容。
    当子标签内容中包含同名的闭标签时，仍能正确提取。

    返回 (content, end_pos)，未找到时返回 (None, -1)。
    """
    open_tag = f'<{tag_name}>'
    close_tag_str = f'</{tag_name}>'
    open_prefix = f'<{tag_name}'

    start = block.find(open_tag)
    if start == -1:
        return None, -1

    content_start = start + len(open_tag)
    end_pos = _find_container_end(block, content_start, open_prefix, close_tag_str)

    if end_pos == -1:
        # 未闭合的子标签，取到块末尾
        return block[content_start:], len(block)

    content = block[content_start:end_pos - len(close_tag_str)]
    return content, end_pos


def parse_tools(response: str):
    """
    按顺序解析 AI 响应中的 XML 工具调用。
    返回: (剩余普通文本, 工具列表)

    所有容器工具（create / str_replace / bash / done）均使用嵌套感知解析器，
    正确处理内容中包含同名闭标签的情况。
    非容器工具（file_view / use_skill）为自闭合标签，使用正则匹配。
    """
    all_matches = []

    # === 非容器工具：正则匹配（自闭合标签，不存在嵌套问题） ===
    non_container_patterns = [
        ("file_view", re.compile(r'<file_view\s+path="([^"]*)"[^>]*/>')),
        ("use_skill", re.compile(r'<use_skill\s+name="([^"]*)"\s*/>')),
    ]
    for tool_name, pattern in non_container_patterns:
        for m in pattern.finditer(response):
            all_matches.append((m.start(), m.end(), tool_name, m))

    # === 容器工具：嵌套感知解析 ===
    # 所有含开/闭标签对的工具都使用 _find_container_end，
    # 确保内容中包含同名闭标签时不会截断。
    container_tool_names = ["create", "str_replace", "bash", "done"]

    container_open_patterns = {
        "create": re.compile(r'<create\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>'),
        "str_replace": re.compile(r'<str_replace\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>'),
        "bash": re.compile(r'<bash>'),
        "done": re.compile(r'<done>'),
    }

    for tool_name in container_tool_names:
        open_pattern = container_open_patterns[tool_name]
        close_tag = f'</{tool_name}>'
        open_prefix = f'<{tool_name}'

        for m in open_pattern.finditer(response):
            content_start = m.end()
            end_pos = _find_container_end(response, content_start, open_prefix, close_tag)

            if end_pos == -1:
                # 未闭合的容器标签（LLM 响应被截断或 done 省略闭合）
                content = response[content_start:]
                match_end = len(response)
                is_unclosed = True
            else:
                content = response[content_start:end_pos - len(close_tag)]
                match_end = end_pos
                is_unclosed = False

            all_matches.append((m.start(), match_end, tool_name, {
                "match": m,
                "content": content,
                "is_unclosed": is_unclosed,
            }))

    # 按位置排序
    all_matches.sort(key=lambda x: x[0])

    # 识别容器块范围（所有容器工具，不仅仅是 create/str_replace）
    container_ranges = []
    for start, end, tool_name, _m in all_matches:
        if tool_name in container_tool_names:
            container_ranges.append((start, end))


    def _is_inside_container(pos: int) -> bool:
        for cs, ce in container_ranges:
            if cs < pos < ce:
                return True
        return False


    tools = []
    remaining_parts = []
    last_end = 0

    for start, end, tool_name, m in all_matches:
        # 忽略嵌套在容器块内的工具调用（其标签是容器内容的一部分，不是真正的工具调用）
        if _is_inside_container(start):
            # 但容器本身不能排除自己（start == container_start 的情况）
            # 这里 start 一定在某个容器内部（cs < start < ce），
            # 所以如果当前工具就是那个容器，start == cs，不满足 cs < start，不会被排除
            continue

        if start > last_end:
            remaining_parts.append(response[last_end:start])

        if tool_name == "file_view":
            params = {"path": m.group(1)}
            limit_match = re.search(r'limit="(\d+)"', m.group(0))
            offset_match = re.search(r'offset="(\d+)"', m.group(0))
            if limit_match:
                params["limit"] = int(limit_match.group(1))
            if offset_match:
                params["offset"] = int(offset_match.group(1))
            tools.append({"llm_tool": "file_view", "params": params})

        elif tool_name == "create":
            info = m  # dict: {match, content, is_unclosed}
            content = info["content"]
            if content.startswith('\n'):
                content = content[1:]
            if content.endswith('\n'):
                content = content[:-1]
            tools.append({
                "llm_tool": "create",
                "params": {
                    "path": info["match"].group(1),
                    "content": content,
                    "summary": info["match"].group(2) or "",
                    "_is_unclosed": info["is_unclosed"],
                }
            })

        elif tool_name == "str_replace":
            info = m  # dict: {match, content, is_unclosed}
            # 重构完整块文本，交给 _parse_str_replace_block 解析
            if info["is_unclosed"]:
                block = info["match"].group(0) + info["content"]
            else:
                block = info["match"].group(0) + info["content"] + f'</{tool_name}>'
            tool = _parse_str_replace_block(block)
            if tool:
                if info["is_unclosed"]:
                    tool["params"]["_is_unclosed"] = True
                tools.append(tool)

        elif tool_name == "bash":
            info = m  # dict: {match, content, is_unclosed}
            content = info["content"].strip()
            tools.append({"llm_tool": "bash", "params": {"command": content, "_is_unclosed": info["is_unclosed"]}})

        elif tool_name == "done":
            info = m  # dict: {match, content, is_unclosed}
            content = info["content"].strip()
            tools.append({"llm_tool": "done", "params": {"message": content, "_is_unclosed": info["is_unclosed"]}})

        elif tool_name == "use_skill":
            tools.append({"llm_tool": "use_skill", "params": {"name": m.group(1)}})

        last_end = end

    if last_end < len(response):
        remaining_parts.append(response[last_end:])

    remaining = "".join(remaining_parts).strip()
    remaining = re.sub(r'\n{3,}', '\n\n', remaining)

    return remaining, tools


def _parse_str_replace_block(block: str):
    """
    容错解析单个 str_replace 块，使用嵌套感知的子标签提取。

    当 old/new 子标签内容中包含同名闭标签时，仍能正确提取。
    """
    # 1. 提取 path 和 summary 属性
    header = re.search(r'<str_replace\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>', block)
    if not header:
        return None
    path = header.group(1)
    summary = header.group(2) or ""

    # 2. 使用嵌套感知的子标签提取
    old_content, _ = _extract_subtag_content(block, "old")
    new_content, _ = _extract_subtag_content(block, "new")

    if old_content is None and new_content is None:
        return None

    # 3. 容错：如果嵌套感知提取失败，回退到简单正则
    if old_content is None:
        old_match = re.search(r'<old>(.*?)</old>', block, re.DOTALL)
        old_content = old_match.group(1) if old_match else ""

    if new_content is None:
        new_match = re.search(r'<new>(.*?)</new>', block, re.DOTALL)
        if new_match:
            new_content = new_match.group(1)
        else:
            # 最后手段：<new> 到块末尾
            new_start = block.find('<new>')
            if new_start != -1:
                new_content = block[new_start + len('<new>'):]
            else:
                return None

    return {
        "llm_tool": "str_replace",
        "params": {
            "path": path,
            "summary": summary,
            "old": old_content,
            "new": new_content,
        }
    }


code_output_root = global_cfg.base_path.code_output_root
spec_root = global_cfg.base_path.spec_root


def execute_code_tool(tool):
    """
    执行工具列表，返回 API 消息格式（供下一轮对话使用）。
    每个结果包装为 {"role": "user", "content": "[llm_tool] 结果..."}
    在系统提示词里，强制要求 LLM 输出绝对路径，所以 file_view(code_output_root, p["path"]) 中的 code_output_root，已经没有意义了
    """

    name = tool["llm_tool"]
    p = tool["params"]

    '''
    这里的根目录已经没意义了，因为已经要求 LLM 的回复，肯定是绝对路径
    如果 LLM 没有遵守指令，回复的是相对路径，出现错误，那就错吧
    '''
    if name == "file_view":
        # file_view 内部已经做了 _is_invalid_path 检测，这里再做一层目录截断
        raw_path = p.get("path", "")
        result = file_view(spec_root, raw_path,
                           limit=p.get("limit"),
                           offset=p.get("offset"))
        # 如果返回的是目录列表且行数过多，截断并附加警告，防止 LLM 因巨量上下文产生幻觉
        if result and not result.startswith("错误") and not result.startswith("[BLOCKED]") and not result.startswith(
                "[ERROR]"):
            lines = result.split("\n")
            if len(lines) > 30 and all(line.startswith("[DIR]") or line.startswith("[FILE]") for line in lines):
                result = "\n".join(
                    lines[:30]) + f"\n...（共 {len(lines)} 项，已截断前 30 项。请使用更精确的路径或 limit 参数缩小范围）"

    elif name == "create":
        # 写入文件
        result_detail = file_create(code_output_root, p["path"], p["content"])

        # 提取summary
        summary = p.get("summary", "")
        if summary and summary.strip():
            if len(summary) > 50:
                summary = summary[:47] + "..."
            result = f"文件已创建：{p['path']}，摘要：{summary}"
        else:
            match = re.search(r'\((\d+) 字符\)', result_detail)
            size = match.group(1) if match else str(len(p["content"]))
            result = f"已创建 {p['path']}（{size} 字符）"

    elif name == "str_replace":
        result_detail = file_str_replace(code_output_root, p["path"], p["old"], p["new"])
        summary = p.get("summary", "")
        if summary and summary.strip():
            if len(summary) > 50:
                summary = summary[:47] + "..."
            result = f"文件已修改：{p['path']}，摘要：{summary}"
        else:
            if result_detail.startswith("已修改"):
                old_len = len(p["old"])
                new_len = len(p["new"])
                result = f"文件已修改：{p['path']}，替换了 1 处（{old_len} → {new_len} 字符）"
            else:
                result = result_detail

    elif name == "bash":
        result = tool_bash(p["command"])

    elif name == "use_skill":
        skill_name = p["name"]
        # 动态导入避免循环依赖
        from src.utility.skill_loader import get_skill_loader
        loader = get_skill_loader()
        full_content = loader.load_full_skill(skill_name)
        if full_content is not None:
            result = f"已激活技能 '{skill_name}'，完整指令如下：\n\n{full_content}"
        else:
            # 获取可用技能名称列表
            available = [s["name"] for s in loader.get_metadata()]
            result = (
                f"[CRITICAL ERROR] 技能 '{skill_name}' 不存在或无法加载。\n"
                f"可用的技能列表：{available}\n"
                f"你必须立即输出 <done> 并报告错误，禁止继续执行任何其他工具。"
            )

    else:
        result = "unknown llm_tool"

    # 返回 dict，不是 list
    if name == "bash":
        tool_result = {
            "role": "user",
            "content": f"[{name}] 工具执行结果：\n{result}"
        }
    else:
        tool_result = {
            "role": "user",
            "content": f"[{name}] 工具执行结果：{result}"
        }

    return tool_result


def execute_tools(tools: list) -> list[dict]:
    """
    执行工具列表，返回 API 消息格式（供下一轮对话使用）。
    每个结果包装为 {"role": "user", "content": "[llm_tool] 结果..."}
    """
    results = []
    for t in tools:
        t_results = execute_code_tool(t)
        results.append(t_results)

    return results
