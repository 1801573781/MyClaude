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

        # 对 Markdown 代码块及散落的多行 Python 代码进行语法高亮
        processed_content = self._process_code_blocks(new_content)

        # 使用字符串拼接避免 f-string 与代码中的 {} 冲突
        entry_html = (
            f'<div class="entry">\n'
            f'<div class="entry-header" onclick="toggleEntry(\'{entry_id}\')">\n'
            f'<span class="toggle-icon" id="icon-{entry_id}">&#9662;</span>\n'
            f'<span>{header_time}</span>\n'
            f'</div>\n'
            f'<div class="entry-content" id="content-{entry_id}">\n'
            f'<pre>\n'
            + processed_content +
            f'\n</pre>\n'
            f'</div>\n'
            f'</div>'
        )

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
        # 使用普通字符串拼接，避免 CSS 中的 {} 与 f-string 冲突
        return (
            '<!DOCTYPE html>\n'
            '<html>\n'
            '<head>\n'
            '<meta charset="utf-8">\n'
            '<title>MyClaude Session Log</title>\n'
            '<style>\n'
            'body { \n'
            '    background: #ffffff; \n'
            '    color: #333333; \n'
            '    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; \n'
            '    padding: 20px; \n'
            '    line-height: 1.6; \n'
            '    font-size: 18px;\n'
            '}\n'
            'pre { \n'
            '    margin: 0; \n'
            '    padding: 12px; \n'
            '    background: #f8f9fa; \n'
            '    border: 1px solid #e0e0e0; \n'
            '    border-radius: 6px; \n'
            '    overflow-x: auto; \n'
            '    white-space: pre-wrap; \n'
            '    word-wrap: break-word; \n'
            '    color: #333333; \n'
            '    font-family: "SF Mono", "Menlo", "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace;\n'
            '    font-size: 17px;\n'
            '    line-height: 1.5;\n'
            '}\n'
            'hr { border: none; border-top: 1px solid #e0e0e0; margin: 20px 0; }\n'
            '.entry { \n'
            '    margin-bottom: 16px; \n'
            '    border: 1px solid #e0e0e0; \n'
            '    border-radius: 8px; \n'
            '    overflow: hidden; \n'
            '    background: #ffffff;\n'
            '}\n'
            '.entry-header { \n'
            '    background: #f5f5f5; \n'
            '    padding: 10px 14px; \n'
            '    cursor: pointer; \n'
            '    user-select: none;\n'
            '    display: flex;\n'
            '    align-items: center;\n'
            '    gap: 8px;\n'
            '    font-weight: 500;\n'
            '    color: #555;\n'
            '    transition: background 0.2s;\n'
            '    font-size: 17px;\n'
            '}\n'
            '.entry-header:hover { background: #eeeeee; }\n'
            '.entry-content { \n'
            '    padding: 12px; \n'
            '    background: #ffffff;\n'
            '    transition: max-height 0.3s ease-out, opacity 0.3s ease-out, padding 0.3s ease-out;\n'
            '    max-height: 100000px;\n'
            '    opacity: 1;\n'
            '    overflow: hidden;\n'
            '}\n'
            '.entry-content.collapsed { \n'
            '    max-height: 0; \n'
            '    padding-top: 0;\n'
            '    padding-bottom: 0;\n'
            '    opacity: 0;\n'
            '}\n'
            '.toggle-icon { \n'
            '    display: inline-block;\n'
            '    width: 14px;\n'
            '    text-align: center;\n'
            '    transition: transform 0.2s;\n'
            '    color: #666;\n'
            '    font-size: 10px;\n'
            '}\n'
            '.toggle-icon.collapsed { transform: rotate(-90deg); }\n'
            'h1, h2, h3 { color: #7c3aed; }\n'
            'strong { color: #10b981; }\n'
            '.log-body {\n'
            '    white-space: pre-wrap;\n'
            '    word-wrap: break-word;\n'
            '    margin: 0;\n'
            '    padding: 12px;\n'
            '    background: #f8f9fa;\n'
            '    border-radius: 6px;\n'
            '    color: #333333;\n'
            '    font-family: "SF Mono", "Menlo", "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace;\n'
            '    font-size: 17px;\n'
            '    line-height: 1.5;\n'
            '}\n'
            '.code-block {\n'
            '    background: #ffffff;\n'
            '    padding: 10px 12px;\n'
            '    border: 1px solid #e0e0e0;\n'
            '    border-radius: 4px;\n'
            '    margin: 8px 0;\n'
            '    overflow-x: auto;\n'
            '    font-family: "SF Mono", "Menlo", "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace;\n'
            '    font-size: 15px;\n'
            '    line-height: 1.5;\n'
            '}\n'
            '</style>\n'
            '<script>\n'
            'function toggleEntry(id) {\n'
            '    var content = document.getElementById("content-" + id);\n'
            '    var icon = document.getElementById("icon-" + id);\n'
            '    if (content.classList.contains("collapsed")) {\n'
            '        content.classList.remove("collapsed");\n'
            '        icon.classList.remove("collapsed");\n'
            '        icon.innerHTML = "&#9662;";\n'
            '    } else {\n'
            '        content.classList.add("collapsed");\n'
            '        icon.classList.add("collapsed");\n'
            '        icon.innerHTML = "&#9656;";\n'
            '    }\n'
            '}\n'
            '</script>\n'
            '</head>\n'
            '<body>\n'
            + body_content +
            '\n</body>\n'
            '</html>'
        )


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
        """识别 Markdown 代码块，对 Python 代码进行语法高亮。
        同时，对文本中未被 Markdown 代码块包裹的多行 Python 代码也进行高亮。"""
        md_pattern = re.compile(r'(?s)```([a-zA-Z0-9_+-]*)\n(.*?)\n```')
        result = []
        last_end = 0

        for match in md_pattern.finditer(text):
            start, end = match.span()
            if start > last_end:
                before = text[last_end:start]
                result.append(self._highlight_inline_python(before))

            lang = match.group(1).strip().lower()
            code = match.group(2)

            if not lang or lang in ('python', 'py'):
                highlighted = self._highlight_python(code)
            else:
                highlighted = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            # 使用字符串拼接避免 f-string 与 highlighted 中的 {} 冲突
            result.append('<pre class="code-block">' + highlighted + '</pre>')
            last_end = end

        if last_end < len(text):
            result.append(self._highlight_inline_python(text[last_end:]))

        return ''.join(result)


    def _highlight_inline_python(self, text: str) -> str:
        """对文本中未被 Markdown 代码块包裹的多行 Python 代码进行高亮。
        通过检测是否包含 def/class/import/from 等关键字且为多行来判断。"""
        if '\n' not in text:
            return text

        # 快速启发式检测：包含多行且至少有一行以 Python 关键字开头
        if not re.search(r'(?:^|\n)[ \t]*(?:def|class|import|from)\b', text):
            return text

        # 如果看起来像 Python 代码，进行高亮（XML 标签会被保护）
        return self._highlight_python(text)


    def _highlight_python(self, code: str) -> str:
        """
        轻量级 Python 语法高亮，生成带 <span style="color:..."> 的 HTML。
        配色近似 PyCharm Light 默认主题。
        同时保护 XML 工具标签（如 <create>, <str_replace> 等），避免破坏 HTML 结构。
        """
        placeholders = []
        counter = [0]

        def protect(match, ptype):
            idx = counter[0]
            counter[0] += 1
            placeholders.append((idx, ptype, match.group(0)))
            return f"__MYCLAUDEHL_{idx}__"

        text = code

        # 1. 保护 XML/HTML 工具标签（如 <create path="...">, </create>, <old>, <new> 等）
        text = re.sub(
            r'(<[a-zA-Z_][a-zA-Z0-9_-]*(?:\s[^>]*)?>|</[a-zA-Z_][a-zA-Z0-9_-]*>)',
            lambda m: protect(m, 'tag'), text
        )

        # 2. 保护三引号字符串（优先，避免内部 # 被当作注释）
        text = re.sub(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\')', lambda m: protect(m, 'string'), text)

        # 3. 保护单引号字符串
        text = re.sub(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')', lambda m: protect(m, 'string'), text)

        # 4. 保护行注释
        text = re.sub(r'#[^\n]*', lambda m: protect(m, 'comment'), text)

        # 5. 对剩余文本进行 HTML 转义（防止 < > & 破坏 HTML 结构）
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        # 6. 应用高亮（优先级：装饰器 > 关键字 > 数字 > 内置函数）
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

        # 7. 恢复被保护的内容，并上色 + HTML 转义
        def restore(match):
            idx = int(match.group(1))
            for stored_idx, ptype, original in placeholders:
                if stored_idx == idx:
                    safe = original.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    if ptype == 'string':
                        return f'<span style="color:#008000">{safe}</span>'
                    elif ptype == 'comment':
                        return f'<span style="color:#808080">{safe}</span>'
                    elif ptype == 'tag':
                        return original  # XML 标签保持原样，不转义
                    return safe
            return match.group(0)

        text = re.sub(r'__MYCLAUDEHL_(\d+)__', restore, text)
        return text
