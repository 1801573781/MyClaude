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


    def log_llm_rsp(self, llm_rsp):
        dict_info = {"role": "assistant", "content": llm_rsp}
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
        timestamp = datetime.now().strftime("%Y-%m-%d %H : %M : %S")
        self._turn_buffer.append({"time": timestamp})
        self._turn_buffer.append(dict_info)


    def flush_turn(self):
        """将当前 Turn 缓冲的所有条目一次性写入文件。"""
        if not self._has_turn_content:
            return
        if self.format == "html":
            self._flush_turn_html()
        else:
            self._save_session_log(self._turn_buffer)
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


    """持久化 session 会话的历史"""


    def _save_session_log(self, save_session):
        if not save_session:
            return

        if self.format == "html":
            self._save_html(save_session)
        else:
            self._save_md(save_session)


    def _flush_turn_html(self):
        """将当前 Turn 缓冲一次性写入 HTML 文件，Turn 内按内容类型进行多级折叠。"""
        buffer = self._turn_buffer
        if not buffer:
            return

        # 跳过 turn 标记条目
        start_idx = 0
        for i, item in enumerate(buffer):
            if isinstance(item, dict) and "turn" in item:
                start_idx = i + 1
                break

        # 从缓冲中提取首个时间戳，用于 Turn 标题后缀
        turn_time = ""
        for item in buffer[start_idx:]:
            if isinstance(item, dict) and "time" in item:
                turn_time = item["time"]
                break

        # 将缓冲条目按类型分组为逻辑节
        sections = self._parse_buffer_sections(buffer[start_idx:])

        # 为每个节构建子折叠 HTML
        section_html_parts = []
        section_titles = {
            "system": "⚙️ 系统宪法",
            "system_notice": "⚙️ 系统提示",
            "installed_skills": "📦 系统技能",
            "project_context": "📋 项目宪法",
            "directory_tree": "🗂️ 项目目录",
            "memory_context": "🧠 记忆召唤",
            "user": "👤 用户输入",
            "reasoning": "💭 LLM 思考",
            "assistant": "🤖 LLM 应答",
            "tool": "🔧 工具执行",
        }

        # 检查是否有记忆召唤 section，如果没有则添加占位
        # 记忆召唤应放在"用户输入"之前、"项目目录树"等固定上下文之后
        has_memory_section = any(sec_name == "memory_context" for sec_name, _ in sections)
        if not has_memory_section:
            # 找到"用户输入" section 的位置，插在它前面
            insert_idx = next((i for i, (name, _) in enumerate(sections) if name == "user"), len(sections))
            sections.insert(insert_idx, ("memory_context", [{"role": "user", "content": "没有召唤到相关记忆"}]))

        for section_name, items in sections:
            if section_name == "reasoning":
                reasoning_text = ""
                for item in items:
                    if isinstance(item, dict) and "reasoning" in item:
                        reasoning_text = item["reasoning"]
                        break
                reasoning_escaped = reasoning_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                section_content = f'<pre>{reasoning_escaped}</pre>'
            elif section_name == "memory_context":
                # 对记忆召唤小节特殊处理：如果有多条记忆，拆分为子折叠块
                section_content = self._build_memory_section_html(items)
            else:
                md_chunks = []
                for item in items:
                    md_chunks.append(self._format_log_item(item))
                md_content = "\n\n".join(md_chunks)
                section_content = f'<pre>{self._process_code_blocks(md_content)}</pre>'

            title = section_titles.get(section_name, section_name)
            # 所有子节默认折叠
            open_attr = ""
            section_html = (
                f'<details class="section-fold"{open_attr}>\n'
                f'<summary class="section-summary">{title}</summary>\n'
                + section_content +
                f'\n</details>'
            )
            section_html_parts.append(section_html)

        all_sections_html = "\n".join(section_html_parts)

        # Turn 层折叠：标题包含轮次和时间戳
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

        full_path = Path(self.log_root) / self.session_file_name

        if full_path.exists():
            old_html = full_path.read_text(encoding="utf-8")
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
            if content.startswith("[系统提醒] 以下是与当前任务相关的历史记忆"):
                return "memory_context"
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
                elif role == "assistant":
                    new_section = "assistant"
                elif role == "system":
                    content = item.get("content", "")
                    # query_loop 兜底消息（LLM 未输出 done 时自动结束）归属"系统提示"
                    if "LLM 未调用 done 工具" in str(content) or "未调用 done" in str(content):
                        new_section = "system_notice"
                        if current_section != "system_notice":
                            _flush_section()
                            current_section = "system_notice"
                            current_items = []
                        current_items.append(item)
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

        for item in items:
            process_item(item)

        _flush_section()

        return sections


    def _build_memory_section_html(self, items) -> str:
        """为记忆召唤小节构建 HTML。
        如果只有一条记忆（或"没有召唤到相关记忆"），直接以 <pre> 显示；
        如果有多条记忆，每条拆分为独立的子折叠块。"""
        # 提取记忆内容
        content = ""
        for item in items:
            if isinstance(item, dict) and "content" in item:
                content = item["content"]
                break

        if not content:
            return "<pre>（无记忆内容）</pre>"

        # 转义 HTML
        escaped = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        # 如果只有一条记忆或"没有召唤到相关记忆"，直接展示
        if "没有召唤到相关记忆" in content:
            return f'<pre style="color:#999; font-style:italic;">{escaped}</pre>'

        # 先定位 "[相关历史记忆]" 作为前缀/正文分割点
        # 搜索最后一个 "[相关历史记忆]" 标题行（紧跟记忆条目）
        rel_mem_marker = re.search(r'\n\[相关历史记忆\]\s*\n', content)
        if rel_mem_marker:
            prefix = content[:rel_mem_marker.end()].strip()
            body = content[rel_mem_marker.end():].strip()
        else:
            # 回退：尝试用 "[记忆上下文" 分割
            ctx_marker = re.search(r'\[记忆上下文[^\]]*\]', content)
            if ctx_marker:
                prefix = content[:ctx_marker.end()].strip()
                body = content[ctx_marker.end():].strip()
            else:
                prefix = ""
                body = content.strip()

        # 按记忆条目开头拆分（可能以 "[id=" 或以 "- [Turn" 开头）
        parts = re.split(r'(\n(?=- \[(?:id=|Turn\s)))', body)
        memories = []
        if parts:
            # parts[0] 可能是空白或"纯标题"行（如残留的 "[相关历史记忆]"）
            first = parts[0].strip()
            # 过滤掉仅包含 "[相关历史记忆]" 或空白的伪条目
            if first and not re.match(r'^\[相关历史记忆\]$', first):
                memories.append(first)
            for i in range(1, len(parts)):
                part = parts[i].strip()
                if part:
                    memories.append(part)

        if len(memories) <= 1:
            # 单条记忆，直接显示
            return f'<pre>{escaped}</pre>'

        # 多条记忆：每条构建为子折叠块
        prefix_escaped = prefix.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        sub_html_parts = []
        if prefix_escaped:
            sub_html_parts.append(
                f'<div style="padding:8px 12px; color:#666; font-size:14px; '
                f'border-bottom:1px solid #e8e8e8;">{prefix_escaped}</div>'
            )

        for idx, mem in enumerate(memories):
            # 提取 Turn 编号和相关性得分，生成摘要
            turn_match = re.search(r'\[Turn\s*(\d+)\]', mem)
            score_match = re.search(r'\(相关性:\s*([\d.]+)\)', mem)
            turn_num = turn_match.group(1) if turn_match else str(idx + 1)
            score = score_match.group(1) if score_match else "?"
            summary_text = f"📌 - 记忆{idx + 1}（相关性: {score}）"

            # 移除记忆内容末尾的 "(相关性: X.XX)" 标注
            mem_clean = re.sub(r'\s*\(相关性:\s*[\d.]+\)\s*$', '', mem)

            mem_escaped = mem_clean.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            summary_escaped = summary_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            sub_html_parts.append(
                f'<details class="memory-fold" style="margin:4px 0;">\n'
                f'<summary class="memory-summary">{summary_escaped}</summary>\n'
                f'<pre style="margin:4px 12px; font-size:15px;">{mem_escaped}</pre>\n'
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
                f'<details style="margin: 8px 0; padding: 8px; background: #f0f4ff; border: 1px solid #c0d0f0; border-radius: 6px;">\n'
                f'<summary style="cursor: pointer; font-weight: bold; color: #4a6da7;">展开查看推理过程</summary>\n'
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
            lines.append(f"## 🔄 Turn {item['turn']}")

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
                        # 即使看起来像 XML 标签，在 <pre> 内也必须转义，否则破坏 HTML 结构
                        return original.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    return safe
            return match.group(0)

        text = re.sub(r'__MYCLAUDEHL_(\d+)__', restore, text)
        return text
