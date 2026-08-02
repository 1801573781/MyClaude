import hashlib
import json
import re
from pathlib import Path
from datetime import datetime

from src.utility.config_loader import global_cfg
from src.utility.file_tool import file_append


class SessionLog:

    def __init__(self):
        self.log_root = global_cfg.base_path.logs_root
        # MD 日志输出到 raw_session_log 目录（供记忆/Bug 提取使用）
        self.md_log_root = str(Path(global_cfg.base_path.project_root) / "memory_storage" / "memory_ex" / "raw_session_log")
        Path(self.md_log_root).mkdir(parents=True, exist_ok=True)
        # 安全读取日志格式配置，默认 md
        log_cfg = getattr(global_cfg, 'log', None)
        self.format = getattr(log_cfg, 'format', 'md') if log_cfg else 'md'
        self.session_file_name = ""
        self.req_tokens = 0
        self.rsp_tokens = 0
        self._turn_buffer = []
        self._current_turn = None
        self._has_turn_content = False
        # 用于去重：记录上一轮 api_messages 中各消息内容的哈希值
        self._prev_msg_hashes = set()
        # Session 初始化守护（只执行一次）
        self._session_inited = False
        # Query 级日志缓冲
        self._query_counter = 0
        self._current_query = None
        self._query_buffer = []
        # CLI 命令日志缓冲（命令 + 结果合并为一个条目）
        self._cli_command = None
        self._cli_result = None
        # session 初始化前的 CLI 命令缓存（init_session 后补写）
        self._pending_cli_entries = []

        # 并行 MD 日志文件名（与 HTML 同名但 .md 扩展名，不含 LLM 思考）
        self.md_session_file_name = ""
        # 记录最近一次 file_view 的 path，供下一轮 tool_result 格式化使用
        self._last_file_view_path = None


    def init_session(self):
        if self._session_inited:
            return
        now = datetime.now()
        ext = "html" if self.format == "html" else "md"
        self.session_file_name = f"MyClaude_{now.strftime('%Y-%m-%d_%H-%M-%S')}.{ext}"

        save_session = [
            {"time": now.strftime("%Y-%m-%d %H : %M : %S")},
            {"file name": self.session_file_name}
        ]

        # 先标记已初始化，再调用 _save_session_log，避免递归调用 init_session
        self._session_inited = True
        self._save_session_log(save_session)

        # 并行初始化 MD 日志文件（与 HTML 同名但 .md 扩展名）
        # MD 日志输出到 raw_session_log 目录（供记忆/Bug 提取使用）
        self.md_session_file_name = f"MyClaude_{now.strftime('%Y-%m-%d_%H-%M-%S')}.md"
        md_header = (
            f"# MyClaude Session Log (MD)\n\n"
            f"**文件:** {self.md_session_file_name}\n\n"
            f"**时间:** {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"***************\n\n"
        )
        file_append(self.md_log_root, self.md_session_file_name, md_header)

        # 补写 session 初始化前缓存的 CLI 命令条目
        if self._pending_cli_entries:
            for cli_now, cli_cmd, cli_res in self._pending_cli_entries:
                if self.format == "html":
                    self._flush_cli_html(cli_now, cli_cmd, cli_res)
                else:
                    md_items = [{"time": cli_now}]
                    if cli_cmd:
                        md_items.append({"role": "user", "content": f"[CLI_COMMAND] {cli_cmd}"})
                    if cli_res:
                        md_items.append({"role": "user", "content": f"[CLI_RESULT] {cli_res}"})
                    self._save_session_log(md_items)
                # 并行写入 MD 日志
                self._append_md_cli(cli_now, cli_cmd, cli_res)
            self._pending_cli_entries = []


    def get_tokens(self):
        return self.req_tokens, self.rsp_tokens


    def start_query(self, query_index: int, user_input: str):
        """开始一个新 Query 节点，立即写入 Query 头到日志文件。"""
        # 如果上一个 Query 未关闭，先关闭
        if self._query_buffer:
            self.end_query()
        # 确保 session 已初始化（防止 Query 1 标题丢失）
        if not self._session_inited:
            self.init_session()
        self._query_counter = query_index
        self._current_query = query_index
        self._query_buffer = [
            {"query": query_index, "user_input": user_input}
        ]
        # 立即写入 Query 头到文件（HTML banner + MD 标题）
        self._write_query_header(query_index, user_input)


    def end_query(self):
        """结束当前 Query 节点，flush 最后一个 Turn 并写入分隔符。"""
        if not self._query_buffer:
            return
        # flush 最后一个 Turn（会立即写入文件）
        if self._has_turn_content:
            self.flush_turn()
        # 写入 Query 分隔符
        if self.md_session_file_name:
            file_append(self.md_log_root, self.md_session_file_name, "\n***************\n\n")
        if self.session_file_name and self.format == "html":
            self._append_html_to_file("<hr/>\n")
        self._query_buffer = []
        self._current_query = None


    def _write_query_header(self, query_index: int, user_input: str):
        """Query 开始时立即写入标题头到 HTML 和 MD 文件。"""
        query_label = f"📋 Query {query_index}: {user_input}" if user_input else f"📋 Query {query_index}"

        # MD：写入 Query 标题
        if self.md_session_file_name:
            file_append(self.md_log_root, self.md_session_file_name, f"\n{query_label}\n\n")

        # HTML：写入 Query 分隔横幅（icon span 已含 📋，label 不再重复）
        if self.session_file_name and self.format == "html":
            html_label = f"Query {query_index}: {user_input}" if user_input else f"Query {query_index}"
            banner_html = (
                f'<div class="query-banner">\n'
                f'<span class="query-icon">📋</span>\n'
                f'<span class="query-label"><strong>{html_label}</strong></span>\n'
                f'</div>\n'
            )
            self._append_html_to_file(banner_html)


    def _write_turn_html(self):
        """将当前 Turn 缓冲立即写入 HTML 文件，Turn 内按内容类型多级折叠。"""
        buffer = self._turn_buffer
        if not buffer:
            return

        # 跳过 turn 标记条目
        start_idx = 0
        for i, item in enumerate(buffer):
            if isinstance(item, dict) and "turn" in item:
                start_idx = i + 1
                break

        # 从缓冲中提取首个时间戳
        turn_time = ""
        for item in buffer[start_idx:]:
            if isinstance(item, dict) and "time" in item:
                turn_time = item["time"]
                break

        sections = self._parse_buffer_sections(buffer[start_idx:])

        section_titles = {
            "system": "⚙️ 系统宪法",
            "system_notice": "⚙️ 系统提示",
            "installed_skills": "📦 系统技能",
            "project_context": "📋 项目章程",
            "directory_tree": "🗂️ 项目目录",
            "memory_context": "🧠 记忆召回",
            "user": "👤 用户输入",
            "reasoning": "💭 LLM 思考",
            "assistant": "🤖 LLM 应答",
            "tool": "🔧 工具调用",
            "tool_result": "👤 用户输入",
        }

        html_skip_sections = {"system", "installed_skills", "project_context", "directory_tree", "tool"}

        section_html_parts = []
        for section_name, items in sections:
            if section_name in html_skip_sections:
                continue
            if section_name == "reasoning":
                reasoning_text = ""
                for item in items:
                    if isinstance(item, dict) and "reasoning" in item:
                        reasoning_text = item["reasoning"]
                        break
                reasoning_escaped = reasoning_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                section_content = f'<pre>{reasoning_escaped}</pre>'
            elif section_name == "memory_context":
                section_content = self._build_memory_section_html(items)
            else:
                md_chunks = []
                for item in items:
                    md_chunks.append(self._format_log_item(item))
                md_content = "\n\n".join(md_chunks)
                section_content = f'<pre>{self._process_code_blocks(md_content)}</pre>'

            title = section_titles.get(section_name, section_name)
            section_html = (
                f'<details class="section-fold">\n'
                f'<summary class="section-summary">{title}</summary>\n'
                + section_content +
                f'\n</details>'
            )
            section_html_parts.append(section_html)

        all_sections_html = "\n".join(section_html_parts)

        if self._current_turn is not None and self._current_turn < 0:
            turn_label = f"🔄 追问 {abs(self._current_turn)}"
        else:
            turn_label = f"🔄 Turn {self._current_turn}" if self._current_turn is not None else "Log Entry"
        if turn_time:
            turn_label += f"&nbsp;&nbsp;&nbsp;<span style='font-weight:normal; color:#999; font-size:0.9em;'>🕐 {turn_time}</span>"
        entry_id = f"turn-{self._current_turn or 0}-{datetime.now().strftime('%H%M%S%f')}"

        entry_html = (
            f'<div class="entry">\n'
            f'<div class="entry-header" onclick="toggleEntry(\'{entry_id}\')">\n'
            f'<span class="toggle-icon" id="icon-{entry_id}">&#9662;</span>\n'
            f'<span><strong>{turn_label}</strong></span>\n'
            f'</div>\n'
            f'<div class="entry-content" id="content-{entry_id}">\n'
            + all_sections_html +
            f'\n</div>\n'
            f'</div>'
        )

        self._append_html_to_file(entry_html)


    def _write_turn_md(self):
        """将当前 Turn 缓冲立即写入 MD 文件。
        按小节分节、添加小节标题，不含 LLM 思考（reasoning）。"""
        if not self.md_session_file_name:
            return

        buffer = self._turn_buffer
        if not buffer:
            return

        section_titles = {
            "system": "⚙️ 系统宪法",
            "system_notice": "⚙️ 系统提示",
            "installed_skills": "📦 系统技能",
            "project_context": "📋 项目章程",
            "directory_tree": "🗂️ 项目目录",
            "memory_context": "🧠 记忆召回",
            "user": "👤 用户输入",
            "assistant": "🤖 LLM 应答",
            "tool": "🔧 工具调用",
            "tool_result": "👤 用户输入",
        }

        skip_sections = {"system", "installed_skills", "project_context", "directory_tree", "tool"}

        # 跳过 turn 标记条目
        start_idx = 0
        for i, item in enumerate(buffer):
            if isinstance(item, dict) and "turn" in item:
                start_idx = i + 1
                break

        turn_num = self._current_turn or 0
        if turn_num < 0:
            turn_label = f"### 🔄 追问 {abs(turn_num)}"
        else:
            turn_label = f"### 🔄 Turn {turn_num}"

        sections = self._parse_buffer_sections(buffer[start_idx:])

        md_parts = [turn_label]
        for section_name, items in sections:
            if section_name == "reasoning":
                continue
            if section_name in skip_sections:
                continue

            title = section_titles.get(section_name, section_name)
            md_parts.append(f"#### {title}")
            section_content = self._format_md_section_content(items, section_name)
            if section_content:
                md_parts.append(section_content)

        md_content = "\n\n".join(md_parts) + "\n\n"
        file_append(self.md_log_root, self.md_session_file_name, md_content)


    def _append_html_to_file(self, html_content: str):
        """将 HTML 内容追加到当前 session 的 HTML 日志文件。"""
        if not self.session_file_name:
            return

        full_path = Path(self.log_root) / self.session_file_name

        if full_path.exists():
            old_html = full_path.read_text(encoding="utf-8")
            body_end = old_html.rfind("</body>")
            if body_end != -1:
                old_html = old_html[:body_end]
            else:
                old_html = old_html.rstrip() + "\n\n"
            new_html = old_html + html_content + "\n</body>\n</html>"
        else:
            new_html = self._html_template(html_content)

        full_path.write_text(new_html, encoding="utf-8")


    def _format_md_section_content(self, items, section_name: str) -> str:
        """将小节中的条目格式化为纯 Markdown 文本（不含 role 头）。
        根据小节类型采用不同的格式化策略。"""
        parts = []
        for item in items:
            if not isinstance(item, dict):
                continue
            # 跳过纯时间戳条目
            if "time" in item and len(item) == 1:
                continue

            if "role" in item:
                content = item.get("content", "")
                role = item["role"]
                # file_view 工具执行结果截断，保留 path 参数
                if role == "system" and isinstance(content, str) and content.startswith("[file_view] 工具执行结果"):
                    path_str = f'\npath = "{self._last_file_view_path}"\n' if self._last_file_view_path else ""
                    content = f"[file_view] {path_str}工具执行结果：略。"
                # assistant 消息优先使用 md_content（已压缩的工具标签）
                if role == "assistant" and "md_content" in item:
                    content = item["md_content"]
                parts.append(content)
            elif "tool_name" in item:
                tool = item["tool_name"]
                tool_parts = [f"**工具:** `{tool}`"]
                if "tool_paras" in item:
                    paras = item["tool_paras"]
                    if isinstance(paras, dict):
                        para_lines = []
                        for k, v in paras.items():
                            if k == "content":
                                v_str = str(v)
                                if len(v_str) > 100:
                                    v_str = v_str[:100] + "…[已截断]"
                                para_lines.append(f"  - {k}: `{v_str}`")
                            else:
                                para_lines.append(f"  - {k}: `{v}`")
                        tool_parts.append("\n".join(para_lines))
                    else:
                        tool_parts.append(f"**参数:** `{paras}`")
                if "exec_result" in item:
                    result = item["exec_result"]
                    if isinstance(result, dict):
                        if tool == "file_view":
                            tool_parts.append("**结果:** 文件内容略")
                        else:
                            tool_parts.append(f"**结果:**\n{result.get('content', str(result))}")
                    else:
                        tool_parts.append(f"**结果:** {str(result)}")
                parts.append("\n".join(tool_parts))
            elif "todo_snapshot" in item:
                parts.append(item["todo_snapshot"])

        return "\n\n".join(parts)


    def _format_md_item(self, item) -> str:
        """将单个日志条目格式化为纯 Markdown 文本。
        跳过 reasoning 内容，其余与 _format_log_item 逻辑一致但去除 HTML。"""
        if isinstance(item, list):
            parts = [self._format_md_item(sub) for sub in item]
            return "\n\n".join(parts)

        if not isinstance(item, dict):
            return f"```\n{str(item)}\n```"

        lines = []

        if "time" in item:
            lines.append(f"**🕐 {item['time']}**")

        if "turn" in item:
            if item["turn"] < 0:
                lines.append(f"### 🔄 追问 {abs(item['turn'])}")
            else:
                lines.append(f"### 🔄 Turn {item['turn']}")

        if "query" in item:
            lines.append(f"## 📋 Query {item['query']}: {item.get('user_input', '')}")

        if "file name" in item:
            lines.append(f"> 📄 Session: `{item['file name']}`")

        # 跳过 reasoning 内容
        if "reasoning" in item:
            return "\n".join(lines) if lines else ""

        if "role" in item:
            role = item["role"]
            content = item.get("content", "")
            if role == "system" and isinstance(content, str) and content.startswith("[file_view] 工具执行结果"):
                path_str = f'\npath = "{self._last_file_view_path}"\n' if self._last_file_view_path else ""
                content = f"[file_view] {path_str}工具执行结果：略。"
            # assistant 消息优先使用 md_content（已压缩的工具标签）
            if role == "assistant" and "md_content" in item:
                content = item["md_content"]
            emoji = {"system": "⚙️", "user": "👤", "assistant": "🤖"}.get(role, "📝")
            lines.append(f"### {emoji} {role.upper()}")
            lines.append("")
            lines.append(content)

        if "tool_name" in item:
            tool = item["tool_name"]
            lines.append(f"### 🔧 Tool: `{tool}`")
            lines.append("")
            if "tool_paras" in item:
                paras = item["tool_paras"]
                if isinstance(paras, dict):
                    lines.append("**参数:**")
                    for k, v in paras.items():
                        if k == "content":
                            v_str = str(v)
                            if len(v_str) > 100:
                                v_str = v_str[:100] + "…[已截断]"
                            lines.append(f"- {k}: `{v_str}`")
                        else:
                            lines.append(f"- {k}: `{v}`")
                else:
                    lines.append(f"**参数:** `{paras}`")
            if "exec_result" in item:
                result = item["exec_result"]
                if isinstance(result, dict):
                    if tool == "file_view":
                        lines.append("**结果:** 文件内容略")
                    else:
                        lines.append("**结果:**")
                        lines.append(result.get("content", str(result)))
                else:
                    lines.append(f"**结果:** {str(result)}")

        if "todo_snapshot" in item:
            lines.append("### 📋 Todo 快照")
            lines.append("")
            lines.append(item["todo_snapshot"])

        return "\n".join(lines)


    def _append_md_cli(self, now: str, command: str, result: str):
        """将 CLI 命令和结果以 Markdown 格式追加写入 MD 文件。
        CLI 命令带标题头（与 HTML 一致），内部拆分为 CLI_COMMAND 和 CLI_RESULT 两个子节。"""
        if not self.md_session_file_name:
            return
        # 标题：命令文本截断到 60 字符
        title_cmd = command[:60] + ("…" if len(command) > 60 else "")
        header = f"## ⌨️ CLI: {title_cmd}" if command else "## ⌨️ CLI"
        lines = [header, f"**🕐 {now}**"]
        if command:
            lines.append(f"### ⌨️ CLI_COMMAND")
            lines.append(command)
        if result:
            lines.append(f"### 📋 CLI_RESULT")
            lines.append(result)
        md_content = "\n\n".join(lines) + "\n\n***************\n\n"
        file_append(self.md_log_root, self.md_session_file_name, md_content)


    def log_turn(self, turn):
        # 先 flush 上一 Turn（如果有）
        if self._has_turn_content:
            self.flush_turn()
        self._current_turn = turn
        self._turn_buffer = [{"turn": turn}]
        self._has_turn_content = True


    def log_llm_req(self, req_dict):
        # 去重：只记录该 Turn 新增的 api_messages（与上一 Turn 的 diff）
        deduped = self._deduplicate_msg_list(req_dict)
        self.log_dict_info(deduped)

        # 统计给LLM发送请求的tokens（近似计算）
        json_str = json.dumps(req_dict, ensure_ascii=False)
        byte_size = len(json_str.encode('utf-8'))
        self.req_tokens += byte_size // 2  # 粗略估计, 2个字节，一个token


    def log_llm_rsp(self, llm_rsp, md_content=None):
        dict_info = {"role": "assistant", "content": llm_rsp}
        if md_content is not None:
            dict_info["md_content"] = md_content
        self.log_dict_info(dict_info)

        # 统计LLM输出的tokens（近似计算）
        self.rsp_tokens += len(llm_rsp) // 2  # 粗略估计, 2个字节，一个token


    def log_reasoning_content(self, reasoning_content: str):
        """
        记录 LLM 返回的推理内容。如果推理内容非空，以 <details> 折叠块形式记录。
        """
        if not reasoning_content:
            return
        dict_info = {"reasoning": reasoning_content}
        self.log_dict_info(dict_info)


    def log_tool_call(self, tool_name, tool_paras):
        # 记录 file_view 的 path，供下一轮 tool_result 格式化使用
        if tool_name == "file_view" and isinstance(tool_paras, dict):
            path = tool_paras.get("path", "")
            if path:
                self._last_file_view_path = path
        # 对 str_replace 工具，只保留 path 和 summary，不记录 old/new 完整内容
        if tool_name == "str_replace" and isinstance(tool_paras, dict):
            filtered_paras = {k: v for k, v in tool_paras.items() if k in ("path", "summary")}
        else:
            filtered_paras = tool_paras
        dict_info = {
            "tool_name": tool_name,
            "tool_paras": filtered_paras
        }
        self.log_dict_info(dict_info)


    def log_tool_result(self, tool_name, result):
        dict_info = {
            "tool_name": tool_name,
            "exec_result": result
        }
        self.log_dict_info(dict_info)

    def log_todo_snapshot(self, todo_list):
        """记录当前 Turn 的 Todo 快照到会话日志。

        Args:
            todo_list: TodoList 对象（来自 src.query.todo_manager）
        """
        if todo_list is None or todo_list.is_empty():
            return
        items = todo_list.items
        total = len(items)
        completed = todo_list.completed_count()
        lines = [f"**Todo 状态**: {completed}/{total}"]
        for item in items:
            status_icon = {
                "completed": "✅",
                "in_progress": "▶",
                "pending": "○",
            }.get(item.status.value, "?")
            lines.append(f"- {status_icon} {item.content} ({item.status.value})")
        dict_info = {"todo_snapshot": "\n".join(lines)}
        self.log_dict_info(dict_info)


    def log_cli_command(self, command: str):
        """缓存 CLI 命令，等待结果到达后一起写入日志。"""
        # 如果上一次 CLI 命令没有结果就来了新命令，先 flush 旧的
        if self._cli_command is not None and self._cli_result is None:
            self._flush_cli_entry()
        self._cli_command = command

    def log_cli_result(self, result_summary: str):
        """缓存 CLI 结果，与命令配对后一起写入日志。"""
        self._cli_result = result_summary
        self._flush_cli_entry()

    def _flush_cli_entry(self):
        """将 CLI 命令和结果合并为一个日志条目写入文件。
        HTML 格式下生成带 title 的折叠节点，内部含 CLI_COMMAND 和 CLI_RESULT 两个子节。
        MD 格式下作为连续段落写入。"""
        if self._cli_command is None and self._cli_result is None:
            return

        now = datetime.now().strftime("%Y-%m-%d %H : %M : %S")
        command = self._cli_command or ""
        result = self._cli_result or ""

        # 重置缓冲
        self._cli_command = None
        self._cli_result = None

        # session 尚未初始化时，先初始化 session（确保 CLI 命令能立即写入日志文件）
        if not self.session_file_name:
            self.init_session()

        if self.format == "html":
            self._flush_cli_html(now, command, result)
        else:
            md_items = [{"time": now}]
            if command:
                md_items.append({"role": "user", "content": f"[CLI_COMMAND] {command}"})
            if result:
                md_items.append({"role": "user", "content": f"[CLI_RESULT] {result}"})
            self._save_session_log(md_items)

        # 并行写入 MD 日志
        self._append_md_cli(now, command, result)

    def _flush_cli_html(self, now: str, command: str, result: str):
        """将 CLI 命令+结果以 HTML 折叠节点形式写入文件。
        title 包含命令文本，内部拆分为 CLI_COMMAND 和 CLI_RESULT 两个子节。"""

        # 防御性检查
        if not self.session_file_name:
            return

        # 标题：命令文本截断到 60 字符
        title_cmd = command[:60] + ("…" if len(command) > 60 else "")
        entry_label = f"⌨️ CLI: {title_cmd}" if command else "⌨️ CLI"
        entry_id = f"cli-{datetime.now().strftime('%H%M%S%f')}"

        def _escape(s: str) -> str:
            return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        # 子节 HTML
        section_html_parts = []
        if command:
            cmd_escaped = _escape(command)
            section_html_parts.append(
                f'<details class="section-fold">\n'
                f'<summary class="section-summary">⌨️ CLI_COMMAND</summary>\n'
                f'<pre>{cmd_escaped}</pre>\n'
                f'</details>'
            )
        if result:
            result_escaped = _escape(result)
            section_html_parts.append(
                f'<details class="section-fold">\n'
                f'<summary class="section-summary">📋 CLI_RESULT</summary>\n'
                f'<pre>{result_escaped}</pre>\n'
                f'</details>'
            )

        all_sections_html = "\n".join(section_html_parts)

        cli_html = (
            f'<div class="entry">\n'
            f'<div class="entry-header" onclick="toggleEntry(\'{entry_id}\')">\n'
            f'<span class="toggle-icon" id="icon-{entry_id}">&#9662;</span>\n'
            f'<span><strong>{entry_label}</strong>&nbsp;&nbsp;&nbsp;'
            f'<span style="font-weight:normal; color:#999; font-size:0.9em;">🕐 {now}</span></span>\n'
            f'</div>\n'
            f'<div class="entry-content" id="content-{entry_id}">\n'
            + all_sections_html +
            f'\n</div>\n'
            f'</div>'
        )

        full_path = Path(self.log_root) / self.session_file_name

        if full_path.exists():
            old_html = full_path.read_text(encoding="utf-8")
            body_end = old_html.rfind("</body>")
            if body_end != -1:
                old_html = old_html[:body_end]
            else:
                old_html = old_html.rstrip() + "\n\n"
            separator = '<hr/>\n'
            new_html = old_html + separator + cli_html + "\n</body>\n</html>"
        else:
            new_html = self._html_template(cli_html)

        full_path.write_text(new_html, encoding="utf-8")

    def log_dict_info(self, dict_info):
        timestamp = datetime.now().strftime("%Y-%m-%d %H : %M : %S")
        self._turn_buffer.append({"time": timestamp})
        self._turn_buffer.append(dict_info)


    def flush_turn(self):
        """将当前 Turn 缓冲立即写入 HTML 和 MD 文件。"""
        if not self._has_turn_content:
            return

        # 确保文件已初始化
        if not self.session_file_name:
            self._turn_buffer = []
            self._has_turn_content = False
            self._current_turn = None
            return

        # HTML 即时写入
        if self.format == "html":
            self._write_turn_html()

        # MD 即时写入
        if self.md_session_file_name:
            self._write_turn_md()

        # 保留到 Query 缓冲（end_query 时只需写分隔符，不再重新写入内容）
        self._query_buffer.extend(self._turn_buffer)
        self._turn_buffer = []
        self._has_turn_content = False
        self._current_turn = None


    def _deduplicate_msg_list(self, req_dict):
        """对 api_messages 列表去重，仅保留本次新增的消息条目。
        通过比较每条消息的序列化哈希值来判断是否已在上次 log_llm_req 中出现过。
        系统宪法/项目宪法/目录树等固定内容只会在第一个 Turn 完整记录。"""
        if not isinstance(req_dict, list):
            return req_dict

        new_items = []
        current_hashes = set()
        for msg in req_dict:
            # 计算该消息的哈希值
            msg_copy = dict(msg)
            # 移除截断前缀（如 "[系统提醒] 以下仅展示与当前任务最相关的历史记忆"），
            # 因为同一段记忆内容前缀可能变化，导致哈希误判为新消息
            content = msg_copy.get("content", "")
            if isinstance(content, str) and content.startswith("[系统提醒] 以下仅展示与当前任务最相关的历史记忆"):
                msg_copy["content"] = content  # 保留原文不截断，确保与上一轮的同一消息哈希一致
            msg_hash = hashlib.md5(
                json.dumps(msg_copy, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            current_hashes.add(msg_hash)

            if msg_hash not in self._prev_msg_hashes:
                new_items.append(msg)

        # 更新上一轮哈希集合
        self._prev_msg_hashes = current_hashes
        return new_items

    def update_msg_hashes(self, api_messages: list):
        """外部更新哈希集合（在 query_loop 中追加记忆消息后调用）。

        防止下轮 log_llm_req 时，已注入的记忆消息因哈希不匹配而重复记录。
        """
        current_hashes = set()
        for msg in api_messages:
            msg_copy = dict(msg)
            msg_hash = hashlib.md5(
                json.dumps(msg_copy, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            current_hashes.add(msg_hash)
        self._prev_msg_hashes = current_hashes


    """持久化 session 会话的历史"""


    def _save_session_log(self, save_session):
        if not save_session:
            return

        # 确保会话已初始化（防止 CLI 命令在首次 Query 前触发写入）
        if not self._session_inited:
            self.init_session()

        # 防御性检查：如果 session_file_name 仍为空，跳过写入避免 PermissionError
        if not self.session_file_name:
            return

        if self.format == "html":
            self._save_html(save_session)
        else:
            self._save_md(save_session)


    def _parse_buffer_sections(self, items):
        """将 Turn 缓冲条目按内容类型分组为逻辑节，用于多级折叠。
        细分 user 消息为：项目上下文、项目目录树、用户输入。
        将 system 消息中的 Installed Skills 拆分为独立 section。
        忽略纯时间戳条目（None section），合并连续同类型 section。"""
        sections = []
        current_section = None
        current_items = []

        # 识别 user 消息的子类型
        def _classify_user(content: str) -> str:
            if not isinstance(content, str):
                return "user"
            if content.startswith("[项目上下文]"):
                return "project_context"
            if content.startswith("[项目目录树]"):
                return "directory_tree"
            if content.startswith("[系统提醒] 以下是与当前任务相关的历史记忆") or \
               content.startswith("[系统提醒] 以下是与你当前任务可能相关的历史记忆"):
                return "memory_context"
            # 系统提醒（非记忆相关，如追问提示等）→ 系统提示
            if content.startswith("[系统提醒]"):
                return "system_notice"
            # 工具执行结果（role="user" 的工具结果）
            _tool_prefixes = ("[file_view]", "[create]", "[str_replace]", "[bash]",
                              "[use_skill]", "[excel_view]", "[AskUserQuestion]",
                              "[done]", "[todowrite]")
            if content.startswith(_tool_prefixes):
                return "tool_result"
            # CLI 命令及结果已由 _flush_cli_entry 独立记录，此处跳过避免重复展示
            if content.startswith("[CLI_COMMAND]") or content.startswith("[CLI_RESULT]"):
                return "cli_command"
            return "user"

        def _flush_section():
            nonlocal current_section, current_items
            if not current_items or current_section is None:
                current_items = []
                return
            # 合并：如果新 section 和上一个 section 类型相同，则合并到上一个
            if sections and sections[-1][0] == current_section:
                sections[-1][1].extend(current_items)
            else:
                sections.append((current_section, current_items))
            current_items = []

        # 标记是否正在处理 api_messages 历史（需过滤 assistant）
        _in_api_history = [False]

        def process_item(item):
            nonlocal current_section, current_items
            if isinstance(item, list):
                _in_api_history[0] = True
                for sub in item:
                    process_item(sub)
                _in_api_history[0] = False
                return
            if not isinstance(item, dict):
                return
            if "time" in item:
                # 时间戳条目仅在跟随有实际内容时保留，否则忽略
                current_items.append(item)
                return
            if "role" in item:
                role = item["role"]
                # 跳过 api_messages 历史中的 assistant 消息（已在之前 Turn 中展示过）
                if _in_api_history[0] and role == "assistant":
                    return
                if role == "user":
                    # 细分 user 消息
                    new_section = _classify_user(item.get("content", ""))
                    # CLI 命令/结果已独立记录，跳过不创建 section
                    if new_section == "cli_command":
                        return
                elif role == "assistant":
                    new_section = "assistant"
                elif role == "system":
                    content = item.get("content", "")
                    # 工具执行结果（role="system" 的工具结果）→ tool_result
                    _tool_result_prefixes = (
                        "[file_view] 工具执行结果",
                        "[create] 工具执行结果",
                        "[str_replace] 工具执行结果",
                        "[bash] 工具执行结果",
                        "[use_skill] 工具执行结果",
                        "[excel_view] 工具执行结果",
                        "[AskUserQuestion] 工具执行结果",
                    )
                    if isinstance(content, str) and content.startswith(_tool_result_prefixes):
                        new_section = "tool_result"
                        if current_section != new_section:
                            _flush_section()
                            current_section = new_section
                            current_items = []
                        current_items.append(item)
                        return
                    # query_loop 兜底消息（LLM 未输出 done 时自动结束）归属"系统提示"
                    if "LLM 未调用 done 工具" in str(content) or "未调用 done" in str(content):
                        new_section = "system_notice"
                        if current_section != "system_notice":
                            _flush_section()
                            current_section = "system_notice"
                            current_items = []
                        current_items.append(item)
                        return
                    # 记忆注入内容（role="system" 的 MEMORY_INJECTION）→ memory_context
                    if isinstance(content, str) and content.startswith("[MEMORY_INJECTION_v1]"):
                        new_section = "memory_context"
                        if current_section != new_section:
                            _flush_section()
                            current_section = new_section
                            current_items = []
                        current_items.append(item)
                        return
                    # 项目上下文（role="system" 的 [项目上下文]）→ project_context
                    if isinstance(content, str) and content.startswith("[项目上下文]"):
                        new_section = "project_context"
                        if current_section != new_section:
                            _flush_section()
                            current_section = new_section
                            current_items = []
                        current_items.append(item)
                        return
                    # 项目目录树（role="system" 的 [项目目录树]）→ directory_tree
                    if isinstance(content, str) and content.startswith("[项目目录树]"):
                        new_section = "directory_tree"
                        if current_section != new_section:
                            _flush_section()
                            current_section = new_section
                            current_items = []
                        current_items.append(item)
                        return
                    # CLI 命令/结果已由 _flush_cli_entry 独立记录，跳过不创建 section
                    if isinstance(content, str) and (content.startswith("[CLI_COMMAND]") or content.startswith("[CLI_RESULT]")):
                        return
                    # 检查是否需要将 Installed Skills 独立拆分
                    if "## Installed Skills" in str(content):
                        # 找到 ## Installed Skills 的位置，拆分为 system 和 installed_skills 两部分
                        idx = content.index("## Installed Skills")
                        sys_part = content[:idx]
                        skill_part = content[idx:]
                        # 先 flush 当前 system section
                        if current_section != "system":
                            _flush_section()
                            current_section = "system"
                            current_items = []
                        # 放入 system 部分（去除尾部空白以保持整洁）
                        if sys_part.strip():
                            system_item = {"role": "system", "content": sys_part}
                            current_items.append(system_item)
                        _flush_section()
                        # 再创建 installed_skills section
                        current_section = "installed_skills"
                        current_items = [{"role": "system", "content": skill_part}]
                        _flush_section()
                        current_section = None
                        current_items = []
                        return
                    new_section = "system"
                else:
                    return
                if current_section != new_section:
                    _flush_section()
                    current_section = new_section
                    current_items = []
                current_items.append(item)
            elif "reasoning" in item:
                if current_section != "reasoning":
                    _flush_section()
                    current_section = "reasoning"
                    current_items = []
                current_items.append(item)
            elif "tool_name" in item:
                if current_section != "tool":
                    _flush_section()
                    current_section = "tool"
                    current_items = []
                current_items.append(item)
            elif "todo_snapshot" in item:
                if current_section != "todo":
                    _flush_section()
                    current_section = "todo"
                    current_items = []
                current_items.append(item)

        for item in items:
            process_item(item)

        _flush_section()

        return sections


    def _build_memory_section_html(self, items) -> str:
        """为记忆召唤小节构建 HTML。
        将系统提醒前缀与各条记忆拆分，每条记忆生成带 📌 父节点的独立折叠块。"""
        # 提取记忆内容
        content = ""
        for item in items:
            if isinstance(item, dict) and "content" in item:
                content = item["content"]
                break

        if not content:
            return "<pre>（无记忆内容）</pre>"

        # 移除 "[记忆上下文 - 由 Memory 模块自动生成]" 行
        content = re.sub(r'\n?\[记忆上下文 - 由 Memory 模块自动生成\]\n?', '\n', content).strip()

        # 占位文本：明确的"没有记忆"标记
        if content.strip() == "没有召回到相关记忆":
            escaped = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            return f'<pre style="color:#75715E; font-style:italic;">{escaped}</pre>'

        # ---- 前缀/正文分离（不使用正则，直接用字符串查找） ----
        # 查找第一个 "\n- [id=" 或 "\n- [Turn " 或 "\n- [(" 的位置
        # 注意：注入器输出中，前缀与正文之间可能有一个或两个换行符
        prefix = content
        body = ""

        # 先尝试找 "\n- [id="
        idx = content.find("\n- [id=")
        if idx == -1:
            idx = content.find("\n- [Turn ")
        if idx == -1:
            idx = content.find("\n- [(")

        if idx >= 0:
            prefix = content[:idx].strip()
            body = content[idx:].strip()

        # ---- 在 body 中拆分各条记忆 ----
        # 每条记忆以 "\n- [id=" 或 "\n- [Turn " 或 "\n- [(" 开头
        # 先收集所有分割点
        split_positions = []
        for marker in ["\n- [id=", "\n- [Turn ", "\n- [("]:
            pos = 0
            while True:
                pos = body.find(marker, pos)
                if pos == -1:
                    break
                # 分割点是 marker 前面的那个 \n
                split_positions.append(pos)
                pos += 1  # 继续向后查找

        split_positions = sorted(set(split_positions))

        # 如果没找到分割点（只有一条记忆），整个 body 就是一条
        if not split_positions:
            memories = [body] if body else []
        else:
            memories = []
            prev_pos = 0
            for pos in split_positions:
                if pos > prev_pos:
                    mem = body[prev_pos:pos].strip()
                    if mem:
                        memories.append(mem)
                prev_pos = pos
            # 最后一条
            last = body[prev_pos:].strip()
            if last:
                memories.append(last)

        # 节标题模式（排除纯节标题行，不作为记忆条目）
        section_header_re = re.compile(
            r'^\[(?:相关历史记忆|检索结果\s*[-–—].*?|长期记忆|记忆搜索.*?)\]$'
        )
        memories = [m for m in memories if not section_header_re.match(m)]

        # ---- 构建 HTML ----
        def _escape(s: str) -> str:
            return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        def _extract_score(mem: str):
            m = re.search(r'\(相关性:\s*([\d.]+)\)', mem)
            return m.group(1) if m else None

        def _clean_mem_body(mem: str) -> str:
            return re.sub(r'\s*\(相关性:\s*[\d.]+\)\s*$', '', mem).strip()

        prefix_html = _escape(prefix) if prefix else ""

        if not memories:
            # 没有记忆条目 → 整个内容作为前缀展示
            return f'<pre style="color:#6b7280;">{prefix_html}</pre>'

        sub_html_parts = []
        if prefix_html:
            sub_html_parts.append(
                f'<div style="padding:8px 12px; color:#6b7280; font-size:14px; '
                f'border-bottom:1px solid #e0e0e0;">{prefix_html}</div>'
            )

        for i, mem in enumerate(memories):
            is_working = bool(re.match(r'- \[Turn\s+\d+\]', mem))
            score = _extract_score(mem)
            mem_clean = _clean_mem_body(mem)

            if is_working:
                summary = f"📌 - 工作记忆{i + 1}"
            elif score is not None:
                summary = f"📌 - 记忆{i + 1}（相关性: {float(score):.2f}）"
            else:
                summary = f"📌 - 记忆{i + 1}（相关性: ?）"

            mem_html = _escape(mem_clean)
            summary_html = _escape(summary)

            sub_html_parts.append(
                f'<details class="memory-fold" style="margin:4px 0;">\n'
                f'<summary class="memory-summary">{summary_html}</summary>\n'
                f'<pre style="margin:4px 12px; font-size:15px;">{mem_html}</pre>\n'
                f'</details>'
            )

        return '\n'.join(sub_html_parts)


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
        # 判断是否为初始会话条目（含 "file name" 键）
        is_init_session = any(isinstance(item, dict) and "file name" in item for item in save_session)

        if is_init_session:
            # 初始条目：提取时间戳和文件名，生成简洁醒目的标题栏
            session_time = ""
            session_file = ""
            for item in save_session:
                if isinstance(item, dict):
                    if "time" in item:
                        session_time = item["time"]
                    if "file name" in item:
                        session_file = item["file name"]

            init_html = (
                f'<div class="session-banner">\n'
                f'<div class="session-title">📄 {session_file}</div>\n'
                f'<div class="session-time">🕐 {session_time}</div>\n'
                f'</div>'
            )

            full_path = Path(self.log_root) / self.session_file_name
            if full_path.exists():
                old_html = full_path.read_text(encoding="utf-8")
                body_end = old_html.rfind("</body>")
                if body_end != -1:
                    old_html = old_html[:body_end]
                else:
                    old_html = old_html.rstrip() + "\n\n"
                new_html = old_html + '<hr/>\n' + init_html + "\n</body>\n</html>"
            else:
                new_html = self._html_template(init_html)

            full_path.write_text(new_html, encoding="utf-8")
            return

        md_chunks = []
        for item in save_session:
            md_chunks.append(self._format_log_item(item))
        new_content = "\n\n".join(md_chunks)

        # 提取首个时间戳作为折叠标题
        header_time = "Entry"
        if save_session and isinstance(save_session[0], dict) and "time" in save_session[0]:
            header_time = save_session[0]["time"]

        entry_id = f"entry-{datetime.now().strftime('%H%M%S%f')}"

        # 提取并移除 reasoning <details> 块（避免嵌套在 <pre> 中）
        reasoning_blocks = []
        reasoning_pattern = re.compile(
            r'<details>\s*<summary>展开查看推理过程</summary>\s*(.*?)\s*</details>',
            re.DOTALL
        )
        def extract_reasoning(m):
            reasoning_blocks.append(m.group(1).strip())
            return ''

        new_content_no_reasoning = reasoning_pattern.sub(extract_reasoning, new_content)

        # 对剩余内容进行语法高亮
        processed_content = self._process_code_blocks(new_content_no_reasoning)

        # 构建 reasoning 区块 HTML（在 <pre> 外部）
        reasoning_html = ""
        for i, r_content in enumerate(reasoning_blocks):
            r_content_escaped = r_content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            reasoning_html += (
                f'<details style="margin: 8px 0; padding: 8px; background: #f8f9fa; border: 1px solid #e0e0e0; border-radius: 6px;">\n'
                f'<summary style="cursor: pointer; font-weight: bold; color: #6b7280;">展开查看推理过程</summary>\n'
                f'<pre style="margin-top: 8px; white-space: pre-wrap;">{r_content_escaped}</pre>\n'
                f'</details>\n'
            )

        # 使用字符串拼接避免 f-string 与代码中的 {} 冲突
        entry_html = (
            f'<div class="entry">\n'
            f'<div class="entry-header" onclick="toggleEntry(\'{entry_id}\')">\n'
            f'<span class="toggle-icon" id="icon-{entry_id}">&#9662;</span>\n'
            f'<span>{header_time}</span>\n'
            f'</div>\n'
            f'<div class="entry-content" id="content-{entry_id}">\n'
            + reasoning_html +
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
            '    width: 24px;\n'
            '    text-align: center;\n'
            '    transition: transform 0.2s;\n'
            '    color: #666;\n'
            '    font-size: 36px;\n'
            '}\n'
            '.toggle-icon.collapsed { transform: rotate(-90deg); }\n'
            'h1, h2, h3 { color: #7c3aed; }\n'
            'strong { color: #10b981; }\n'
            'code { \n'
            '    background: #f0f0f0; \n'
            '    padding: 1px 5px; \n'
            '    border-radius: 3px; \n'
            '    color: #d63384; \n'
            '    font-family: "SF Mono", "Menlo", "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace; \n'
            '    font-size: 0.9em; \n'
            '}\n'
            '.md-header { color: #7c3aed; font-weight: 600; }\n'
            '.md-code { background: #f0f0f0; padding: 1px 5px; border-radius: 3px; color: #d63384; font-family: "SF Mono", "Menlo", "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace; font-size: 0.9em; }\n'
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
            '.section-fold {\n'
            '    margin: 6px 0;\n'
            '    border: 1px solid #e8e8e8;\n'
            '    border-radius: 6px;\n'
            '    overflow: hidden;\n'
            '}\n'
            '.section-summary {\n'
            '    padding: 8px 12px;\n'
            '    background: #fafafa;\n'
            '    cursor: pointer;\n'
            '    font-weight: 600;\n'
            '    font-size: 16px;\n'
            '    color: #4a5568;\n'
            '    user-select: none;\n'
            '    transition: background 0.15s;\n'
            '}\n'
            '.section-summary:hover { background: #f0f0f0; }\n'
            '.session-banner {\n'
            '    text-align: center;\n'
            '    padding: 24px 20px;\n'
            '    background: linear-gradient(135deg, #f5f3ff 0%, #ede9fe 100%);\n'
            '    border-radius: 10px;\n'
            '    margin-bottom: 8px;\n'
            '    border: 1px solid #d8d0f0;\n'
            '}\n'
            '.session-title {\n'
            '    font-size: 22px;\n'
            '    font-weight: 700;\n'
            '    color: #5b21b6;\n'
            '    margin-bottom: 6px;\n'
            '}\n'
            '.session-time {\n'
            '    font-size: 15px;\n'
            '    color: #8b8ba7;\n'
            '    font-weight: 400;\n'
            '}\n'
            '.query-banner {\n'
            '    padding: 12px 16px;\n'
            '    background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);\n'
            '    border-radius: 8px;\n'
            '    margin: 12px 0;\n'
            '    border: 1px solid #bbf7d0;\n'
            '    display: flex;\n'
            '    align-items: center;\n'
            '    gap: 8px;\n'
            '    font-size: 18px;\n'
            '}\n'
            '.query-icon {\n'
            '    font-size: 20px;\n'
            '}\n'
            '.query-label {\n'
            '    color: #166534;\n'
            '}\n'
            '.memory-fold {\n'
            '    margin: 4px 0 4px 16px;\n'
            '    border: 1px solid #e8e8e8;\n'
            '    border-radius: 4px;\n'
            '    overflow: hidden;\n'
            '}\n'
            '.memory-summary {\n'
            '    padding: 6px 10px;\n'
            '    background: #fafafa;\n'
            '    cursor: pointer;\n'
            '    font-weight: 600;\n'
            '    font-size: 14px;\n'
            '    color: #6b7280;\n'
            '    user-select: none;\n'
            '    transition: background 0.15s;\n'
            '}\n'
            '.memory-summary:hover { background: #f0f0f0; }\n'
            '.memory-pre {\n'
            '    margin: 0;\n'
            '    padding: 8px 12px;\n'
            '    font-size: 14px;\n'
            '    border: none;\n'
            '    border-top: 1px solid #f0f0f0;\n'
            '    border-radius: 0;\n'
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
            'document.addEventListener("DOMContentLoaded", function() {\n'
            '    var entries = document.querySelectorAll(".entry");\n'
            '    if (entries.length > 1) {\n'
            '        for (var i = 0; i < entries.length - 1; i++) {\n'
            '            var content = entries[i].querySelector(".entry-content");\n'
            '            var icon = entries[i].querySelector(".toggle-icon");\n'
            '            if (content && icon) {\n'
            '                content.classList.add("collapsed");\n'
            '                icon.classList.add("collapsed");\n'
            '                icon.innerHTML = "&#9656;";\n'
            '            }\n'
            '        }\n'
            '    }\n'
            '});\n'
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
            lines.append(f"### 🔄 Turn {item['turn']}")

        # Query 级标记
        if "query" in item:
            lines.append(f"## 📋 Query {item['query']}: {item.get('user_input', '')}")

        # 会话文件名（初始化时）
        if "file name" in item:
            lines.append(f"> 📄 Session: `{item['file name']}`")

        # 推理内容（折叠块）
        if "reasoning" in item and item["reasoning"].strip():
            lines.append("<details>")
            lines.append("<summary>展开查看推理过程</summary>")
            lines.append("")
            lines.append(item["reasoning"])
            lines.append("")
            lines.append("</details>")

        # LLM 消息（system / user / assistant）
        if "role" in item:
            role = item["role"]
            content = item.get("content", "")
            # 对 file_view 工具执行结果进行截断，避免日志过大，保留 path 参数
            if role == "system" and isinstance(content, str) and content.startswith("[file_view] 工具执行结果"):
                path_str = f'\npath = "{self._last_file_view_path}"\n' if self._last_file_view_path else ""
                content = f"[file_view] {path_str}工具执行结果：略。"
            # assistant 消息优先使用 md_content（已压缩的工具标签）
            if role == "assistant" and "md_content" in item:
                content = item["md_content"]
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
                paras = item["tool_paras"]
                if isinstance(paras, dict):
                    lines.append("**参数:**")
                    for k, v in paras.items():
                        if k == "content":
                            # content 可能很长（代码内容），截断显示
                            v_str = str(v)
                            if len(v_str) > 100:
                                v_str = v_str[:100] + "…[已截断]"
                            lines.append(f"- {k}: `{v_str}`")
                        elif isinstance(v, bool) or v is None:
                            lines.append(f"- {k}: `{v}`")
                        elif isinstance(v, (int, float)):
                            lines.append(f"- {k}: `{v}`")
                        else:
                            lines.append(f"- {k}: `{v}`")
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

        # Todo 快照
        if "todo_snapshot" in item:
            lines.append(f"### 📋 Todo 快照")
            lines.append("")
            lines.append(item["todo_snapshot"])

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
        """对文本中未被 Markdown 代码块包裹的内容进行渲染。
        如果检测到 Python 代码，进行语法高亮；否则进行 Markdown 风格彩色渲染。"""
        if '\n' not in text:
            return self._enhance_markdown_text(text)

        # 快速启发式检测：包含多行且至少有一行以 Python 关键字开头
        if re.search(r'(?:^|\n)[ \t]*(?:def|class|import|from)\b', text):
            # 如果看起来像 Python 代码，进行高亮（XML 标签会被保护）
            return self._highlight_python(text)

        # 非 Python 代码，进行 Markdown 风格渲染
        return self._enhance_markdown_text(text)


    def _enhance_markdown_text(self, text: str) -> str:
        """对非代码块的文本进行 Markdown 风格的彩色渲染。
        保护已有 HTML 标签，对 Markdown 语法添加颜色。"""
        if not text or not text.strip():
            return text

        # 保护已有的 HTML 标签（仅限已知标签，避免误保护 LLM 输出中的 <create> 等）
        html_placeholders = []

        def protect_html(m):
            idx = len(html_placeholders)
            html_placeholders.append(m.group(0))
            return f"__MDHTML_{idx}__"

        text = re.sub(r'</?(?:details|summary)>', protect_html, text)

        # HTML 转义剩余文本
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        # Markdown 标题着色（### 或 ## 开头的行）
        text = re.sub(
            r'^(#{1,6}\s.+)$',
            r'<span style="color:#7c3aed;font-weight:600">\1</span>',
            text,
            flags=re.MULTILINE
        )

        # 行内代码 `code`
        text = re.sub(
            r'`([^`]+)`',
            r'<span style="background:#f0f0f0;padding:1px 5px;border-radius:3px;color:#d63384;font-family:Consolas,monospace;font-size:0.9em">\1</span>',
            text
        )

        # 粗体 **text**
        text = re.sub(
            r'\*\*(.+?)\*\*',
            r'<strong style="color:#059669">\1</strong>',
            text
        )

        # 列表项标记着色
        text = re.sub(
            r'^(\s*)([-*])\s',
            r'\1<span style="color:#10b981">\2</span> ',
            text,
            flags=re.MULTILINE
        )

        # 恢复 HTML 标签
        def restore_html(m):
            idx = int(m.group(1))
            return html_placeholders[idx]

        text = re.sub(r'__MDHTML_(\d+)__', restore_html, text)

        return text


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

        # 6. 应用高亮（PyCharm Light 浅色主题配色）
        text = re.sub(r'(@[\w_]+(?:\.[\w_]+)*)', r'<span style="color:#0086B3">\1</span>', text)
        text = re.sub(
            r'\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield|True|False|None)\b',
            lambda m: f'<span style="color:#0033B3">{m.group(0)}</span>',
            text
        )
        text = re.sub(
            r'\b(?:\d+\.\d+|\d+\.|\.\d+|\d+)(?:[eE][+-]?\d+)?[jJ]?\b',
            lambda m: f'<span style="color:#1750EB">{m.group(0)}</span>',
            text
        )
        text = re.sub(
            r'\b(?:print|len|range|str|int|float|list|dict|set|tuple|open|enumerate|zip|map|filter|sum|min|max|abs|round|type|isinstance|getattr|hasattr|super|object|id|hex|bin|oct|chr|ord|repr|sorted|reversed|any|all|next|iter|input|format|eval|exec|compile|vars|locals|globals|dir|help|memoryview|bytearray|bytes|frozenset|property|staticmethod|classmethod|slice)\b',
            lambda m: f'<span style="color:#00627A">{m.group(0)}</span>',
            text
        )

        # 7. 恢复被保护的内容，并上色 + HTML 转义
        def restore(match):
            idx = int(match.group(1))
            for stored_idx, ptype, original in placeholders:
                if stored_idx == idx:
                    safe = original.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    if ptype == 'string':
                        return f'<span style="color:#067D17">{safe}</span>'
                    elif ptype == 'comment':
                        return f'<span style="color:#8C8C8C;font-style:italic">{safe}</span>'
                    elif ptype == 'tag':
                        # 即使看起来像 XML 标签，在 <pre> 内也必须转义，否则破坏 HTML 结构
                        return original.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    return safe
            return match.group(0)

        text = re.sub(r'__MYCLAUDEHL_(\d+)__', restore, text)
        return text
