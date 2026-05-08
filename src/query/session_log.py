import json
import re
from pathlib import Path
from datetime import datetime

from utility.config_loader import global_cfg
from utility.file_tool import file_append


class SessionLog:

    def __init__(self):
        self.log_root = global_cfg.base_path.logs_root
        # 安全读取日志格式配置，默认 md
        log_cfg = getattr(global_cfg, 'log', None)
        self.format = getattr(log_cfg, 'format', 'md') if log_cfg else 'md'
        self.session_file_name = ""
        self.req_tokens = 0
        self.rsp_tokens = 0


    def init_session(self):
        now = datetime.now()
        ext = "html" if self.format == "html" else "md"
        self.session_file_name = f"MyClaude {now.strftime('%Y-%m-%d %H-%M-%S')}.{ext}"

        save_session = [
            {"time": now.strftime("%Y-%m-%d %H : %M : %S")},
            {"file name": self.session_file_name}
        ]

        self._save_session_log(save_session)


    def get_tokens(self):
        return self.req_tokens, self.rsp_tokens


    def log_turn(self, turn):
        dict_info = {"turn": turn}
        self.log_dict_info(dict_info)


    def log_llm_req(self, req_dict):
        # req_dict，直接就是个 dict
        self.log_dict_info(req_dict)

        # 统计给LLM发送请求的tokens（近似计算）
        json_str = json.dumps(req_dict, ensure_ascii=False)
        byte_size = len(json_str.encode('utf-8'))
        self.req_tokens += byte_size // 2  # 粗略估计, 2个字节，一个token


    def log_llm_rsp(self, llm_rsp):
        dict_info = {"role": "assistant", "content": llm_rsp}
        self.log_dict_info(dict_info)

        # 统计LLM输出的tokens（近似计算）
        self.rsp_tokens += len(llm_rsp) // 2  # 粗略估计, 2个字节，一个token


    def log_tool_call(self, tool_name, tool_paras):
        dict_info = {
            "tool_name": tool_name,
            "tool_paras": tool_paras
        }
        self.log_dict_info(dict_info)


    def log_tool_result(self, tool_name, result):
        dict_info = {
            "tool_name": tool_name,
            "exec_result": result
        }
        self.log_dict_info(dict_info)


    def log_dict_info(self, dict_info):
        save_session = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H : %M : %S")

        save_session.append({"time": timestamp})
        save_session.append(dict_info)

        self._save_session_log(save_session)


    """持久化 session 会话的历史"""


    def _save_session_log(self, save_session):
        if not save_session:
            return

        if self.format == "html":
            self._save_html(save_session)
        else:
            self._save_md(save_session)


    def _save_md(self, save_session):
        # 把未保存的条目格式化为 Markdown
        md_chunks = []
        for item in save_session:
            md_chunks.append(self._format_log_item(item))

        # chunk 之间只换行，末尾加分隔符作为批次分隔
        content = "\n\n".join(
            md_chunks) + "\n\n════════════════════════════════════════════════════════════════════════════════════\n\n"

        file_append(self.log_root, self.session_file_name, content)


    def _save_html(self, save_session):
        md_chunks = []
        for item in save_session:
            md_chunks.append(self._format_log_item(item))
        new_content = "\n\n".join(md_chunks)

        # 提取首个时间戳作为折叠标题
        header_time = "Entry"
        if save_session and isinstance(save_session[0], dict) and "time" in save_session[0]:
            header_time = save_session[0]["time"]

        entry_id = f"entry-{datetime.now().strftime('%H%M%S%f')}"

        # 对 Markdown 代码块进行语法高亮处理
        processed_content = self._process_code_blocks(new_content)

        entry_html = f'''<div class="entry">
<div class="entry-header" onclick="toggleEntry('{entry_id}')">
<span class="toggle-icon" id="icon-{entry_id}">&#9662;</span>
<span>{header_time}</span>
</div>
<div class="entry-content" id="content-{entry_id}">
<div class="log-body">
{processed_content}
</div>
</div>
</div>'''

        full_path = Path(self.log_root) / self.session_file_name

        if full_path.exists():
            old_html = full_path.read_text(encoding="utf-8")
            # 去掉 </body></html>，追加新内容，再加回
            body_end = old_html.rfind("</body>")
            if body_end != -1:
                old_html = old_html[:body_end]
            else:
                old_html = old_html.rstrip() + "\n\n"

            separator = '<hr/>\n'
            new_html = old_html + separator + entry_html + "\n</body>\n</html>"
        else:
            new_html = self._html_template(entry_html)

        full_path.write_text(new_html, encoding="utf-8")


    @staticmethod
    def _html_template(body_content: str) -> str:
        return f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>MyClaude Session Log</title>
<style>
body {{ 
    background: #ffffff; 
    color: #333333; 
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; 
    padding: 20px; 
    line-height: 1.6; 
    font-size: 18px;
}}
pre {{ 
    margin: 0; 
    padding: 12px; 
    background: #f8f9fa; 
    border: 1px solid #e0e0e0; 
    border-radius: 6px; 
    overflow-x: auto; 
    white-space: pre-wrap; 
    word-wrap: break-word; 
    color: #333333; 
    font-family: "SF Mono", "Menlo", "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace;
    font-size: 17px;
    line-height: 1.5;
}}
hr {{ border: none; border-top: 1px solid #e0e0e0; margin: 20px 0; }}
.entry {{ 
    margin-bottom: 16px; 
    border: 1px solid #e0e0e0; 
    border-radius: 8px; 
    overflow: hidden; 
    background: #ffffff;
}}
.entry-header {{ 
    background: #f5f5f5; 
    padding: 10px 14px; 
    cursor: pointer; 
    user-select: none;
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 500;
    color: #555;
    transition: background 0.2s;
    font-size: 17px;
}}
.entry-header:hover {{ background: #eeeeee; }}
.entry-content {{ 
    padding: 12px; 
    background: #ffffff;
    transition: max-height 0.3s ease-out, opacity 0.3s ease-out, padding 0.3s ease-out;
    max-height: 100000px;
    opacity: 1;
    overflow: hidden;
}}
.entry-content.collapsed {{ 
    max-height: 0; 
    padding-top: 0;
    padding-bottom: 0;
    opacity: 0;
}}
.toggle-icon {{ 
    display: inline-block;
    width: 14px;
    text-align: center;
    transition: transform 0.2s;
    color: #666;
    font-size: 10px;
}}
.toggle-icon.collapsed {{ transform: rotate(-90deg); }}
h1, h2, h3 {{ color: #7c3aed; }}
strong {{ color: #10b981; }}
.log-body {{
    white-space: pre-wrap;
    word-wrap: break-word;
    margin: 0;
    padding: 12px;
    background: #f8f9fa;
    border-radius: 6px;
    color: #333333;
    font-family: "SF Mono", "Menlo", "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace;
    font-size: 17px;
    line-height: 1.5;
}}
.code-block {{
    background: #ffffff;
    padding: 10px 12px;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    margin: 8px 0;
    overflow-x: auto;
    font-family: "SF Mono", "Menlo", "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace;
    font-size: 15px;
    line-height: 1.5;
}}
</style>
<script>
function toggleEntry(id) {{
    var content = document.getElementById('content-' + id);
    var icon = document.getElementById('icon-' + id);
    if (content.classList.contains('collapsed')) {{
        content.classList.remove('collapsed');
        icon.classList.remove('collapsed');
        icon.innerHTML = '&#9662;';
    }} else {{
        content.classList.add('collapsed');
        icon.classList.add('collapsed');
        icon.innerHTML = '&#9656;';
    }}
}}
</script>
</head>
<body>
{body_content}
</body>
</html>'''


    """把单个日志项（dict 或 list）格式化为 Markdown 字符串"""


    def _format_log_item(self, item) -> str:
        # 展平嵌套列表（比如 _log_llm_req 塞进来的 api_messages 列表）
        if isinstance(item, list):
            parts = [self._format_log_item(sub) for sub in item]
            return "\n\n".join(parts)

        if not isinstance(item, dict):
            return f"```\n{str(item)}\n```"

        lines = []

        # 时间戳
        if "time" in item:
            lines.append(f"**🕐 {item['time']}**")

        # 轮次标记
        if "turn" in item:
            lines.append(f"## 🔄 Turn {item['turn']}")

        # 会话文件名（初始化时）
        if "file name" in item:
            lines.append(f"> 📄 Session: `{item['file name']}`")

        # LLM 消息（system / user / assistant）
        if "role" in item:
            role = item["role"]
            content = item.get("content", "")
            emoji = {"system": "⚙️", "user": "👤", "assistant": "🤖"}.get(role, "📝")
            lines.append(f"### {emoji} {role.upper()}")
            lines.append("")
            # content 直接放入，保留换行，让它自然渲染 Markdown
            lines.append(content)

        # 工具调用记录
        if "tool_name" in item:
            tool = item["tool_name"]
            lines.append(f"### 🔧 Tool: `{tool}`")
            lines.append("")
            if "tool_paras" in item:
                # lines.append(f"**参数:** `{item['tool_paras']}`")
                paras = item["tool_paras"]
                if isinstance(paras, dict):
                    lines.append("**参数:**")
                    if "path" in paras:
                        lines.append(f"- 路径: `{paras['path']}`")
                    if "command" in paras:
                        lines.append(f"- 命令: `{paras['command']}`")
                    # content 不再重复打印，因为 assistant 消息里已经有了完整代码
                else:
                    lines.append(f"**参数:** `{paras}`")
            if "exec_result" in item:
                result = item["exec_result"]
                if isinstance(result, dict):
                    if "file_view" == tool:
                        lines.append(f"**结果:**，文件内容略")
                    else:
                        lines.append(f"**结果:**")
                        lines.append(result.get("content", str(result)))
                else:
                    lines.append(f"**结果:** {str(result)}")

        return "\n".join(lines)


    def _process_code_blocks(self, text: str) -> str:
        """识别 Markdown 代码块，对 Python 代码进行语法高亮，非 Python 代码块仅做 HTML 转义。"""
        pattern = re.compile(r'(?s)```([a-zA-Z0-9_+-]*)\n(.*?)\n```')
        result = []
        last_end = 0

        for match in pattern.finditer(text):
            start, end = match.span()
            if start > last_end:
                result.append(text[last_end:start])

            lang = match.group(1).strip().lower()
            code = match.group(2)

            # 空语言或 python/py 都按 Python 高亮（MyClaude 语境下绝大多数代码都是 Python）
            if not lang or lang in ('python', 'py'):
                highlighted = self._highlight_python(code)
            else:
                highlighted = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            result.append(
                f'<pre class="code-block">{highlighted}</pre>'
            )
            last_end = end

        if last_end < len(text):
            result.append(text[last_end:])

        return ''.join(result)


    def _highlight_python(self, code: str) -> str:
        """
        轻量级 Python 语法高亮，生成带 <span style="color:..."> 的 HTML。
        配色近似 PyCharm Light 默认主题。
        """
        placeholders = []
        counter = [0]

        def protect(match, ptype):
            idx = counter[0]
            counter[0] += 1
            placeholders.append((idx, ptype, match.group(0)))
            return f"__MYCLAUDEHL_{idx}__"

        text = code

        # 1. 保护三引号字符串（优先，避免内部 # 被当作注释）
        text = re.sub(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\')', lambda m: protect(m, 'string'), text)
        # 2. 保护单引号字符串
        text = re.sub(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')', lambda m: protect(m, 'string'), text)
        # 3. 保护行注释
        text = re.sub(r'#[^\n]*', lambda m: protect(m, 'comment'), text)

        # 4. 对剩余文本进行 HTML 转义（防止 < > & 破坏 HTML 结构）
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        # 5. 应用高亮（优先级：装饰器 > 关键字 > 数字 > 内置函数）
        text = re.sub(r'(@[\w_]+(?:\.[\w_]+)*)', r'<span style="color:#BBB529">\1</span>', text)
        text = re.sub(
            r'\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield|True|False|None)\b',
            lambda m: f'<span style="color:#0033B3">{m.group(0)}</span>',
            text
        )
        text = re.sub(
            r'\b(?:\d+\.\d+|\d+\.|\.\d+|\d+)(?:[eE][+-]?\d+)?[jJ]?\b',
            lambda m: f'<span style="color:#0000FF">{m.group(0)}</span>',
            text
        )
        text = re.sub(
            r'\b(?:print|len|range|str|int|float|list|dict|set|tuple|open|enumerate|zip|map|filter|sum|min|max|abs|round|type|isinstance|getattr|hasattr|super|object|id|hex|bin|oct|chr|ord|repr|sorted|reversed|any|all|next|iter|input|format|eval|exec|compile|vars|locals|globals|dir|help|memoryview|bytearray|bytes|frozenset|property|staticmethod|classmethod|slice)\b',
            lambda m: f'<span style="color:#7B0099">{m.group(0)}</span>',
            text
        )

        # 6. 恢复被保护的字符串和注释，并上色 + HTML 转义
        def restore(match):
            idx = int(match.group(1))
            for stored_idx, ptype, original in placeholders:
                if stored_idx == idx:
                    safe = original.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    if ptype == 'string':
                        return f'<span style="color:#008000">{safe}</span>'
                    elif ptype == 'comment':
                        return f'<span style="color:#808080">{safe}</span>'
                    return safe
            return match.group(0)

        text = re.sub(r'__MYCLAUDEHL_(\d+)__', restore, text)
        return text
