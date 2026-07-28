"""BugExtractor — Bug库提取层。

在对话结束/session 结束时，扫描对话内容，识别 Bug 相关信息，
调用 LLM 将 Bug 结构化为 BugRecord。
"""

import json
import re
from datetime import datetime
from pathlib import Path

from .bug_store import BugRecord, BugStore


class BugExtractor:
    """从对话历史中提取 Bug，结构化为Bug记录。"""

    PROMPT_DIR = Path(__file__).parent / "prompts"

    def __init__(self, llm_client, store: BugStore):
        """初始化提取器。

        Args:
            llm_client: LLM 客户端，需提供 stream_chat(messages) -> str 接口。
            store: BugStore 实例。
        """
        self.llm = llm_client
        self.store = store
        self.extraction_prompt = self._load_prompt()

    def extract_from_session(
        self, api_messages: list[dict], session_id: str
    ) -> list[str]:
        """从对话历史中提取 Bug，存入Bug库。

        Args:
            api_messages: 当前 session 的对话消息列表。
            session_id: 当前 session ID。

        Returns:
            新增的 Bug ID 列表。
        """
        # 1. 拼接对话文本
        dialog_text = self._format_dialog(api_messages)
        if not dialog_text.strip():
            return []

        # 2. 构造 LLM 请求
        prompt = self.extraction_prompt.replace("{dialog}", dialog_text)
        messages = [
            {"role": "user", "content": prompt}
        ]

        # 3. 调用 LLM 提取
        response = self._call_llm(messages)
        if not response:
            return []

        # 4. 解析 LLM 返回
        raw_records = self._parse_llm_response(response)
        if not raw_records:
            return []

        # 5. 构造 BugRecord 并存储
        created_ids = []
        now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        for raw in raw_records:
            affected_files = raw.get("affected_files", [])
            if not affected_files:
                continue

            module = self.store._resolve_module(affected_files[0])

            # 计算 file_hashes
            file_hashes = {}
            for fp in affected_files:
                h = self.store._compute_file_hash(fp)
                if h:
                    file_hashes[fp] = h

            record_id = self.store.generate_id()
            record = BugRecord(
                id=record_id,
                title=raw.get("title", "未命名问题"),
                module=module,
                affected_files=affected_files,
                affected_functions=raw.get("affected_functions", []),
                root_cause=raw.get("root_cause", ""),
                symptoms=raw.get("symptoms", ""),
                fix_pattern=raw.get("fix_pattern", ""),
                caution=raw.get("caution", ""),
                generalization=raw.get("generalization", ""),
                status="open",
                file_hashes=file_hashes,
                created_at=now_str,
                source_session=session_id,
                memory_linked=None,
            )

            self.store.add(record)
            created_ids.append(record_id)

        return created_ids

    def extract_from_raw_entries(self) -> dict:
        """从 Memory 系统 Layer 0 原始记忆中提取 Bug。

        读取 raw_memory.jsonl，过滤掉已提取过的条目，
        调用 LLM 提取 Bug，存入 Bug库。

        Returns:
            统计信息字典，包含:
            - processed: 处理的原始条目数
            - extracted: 提取出的 Bug 数
            - skipped: 跳过的条目数（已提取或无内容）
            - details: 逐条明细
        """
        import json as _json
        from pathlib import Path

        # 1. 定位 raw_memory.jsonl
        # bug_base 目录的父目录就是 memory_storage/memory_ex/
        memory_ex_dir = self.store.base_dir.parent
        raw_jsonl_path = memory_ex_dir / "raw_memory.jsonl"

        if not raw_jsonl_path.exists():
            return {
                "processed": 0,
                "extracted": 0,
                "skipped": 0,
                "details": [],
                "error": "raw_memory.jsonl 不存在",
            }

        # 2. 读取所有原始条目
        all_entries = []
        for line in raw_jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                all_entries.append(entry)
            except _json.JSONDecodeError:
                continue

        if not all_entries:
            return {
                "processed": 0,
                "extracted": 0,
                "skipped": 0,
                "details": [],
            }

        # 3. 过滤已提取过的条目
        extracted_ids = self.store.get_extracted_entry_ids()
        pending_entries = []
        skipped_count = 0
        for entry in all_entries:
            eid = entry.get("id", "")
            if eid and eid in extracted_ids:
                skipped_count += 1
                continue
            # 跳过没有内容的条目
            content = entry.get("content", "")
            if not content or not content.strip():
                skipped_count += 1
                continue
            pending_entries.append(entry)

        if not pending_entries:
            return {
                "processed": 0,
                "extracted": 0,
                "skipped": skipped_count,
                "details": [],
            }

        # 4. 按 query_id 分组（同 /mem ext 的策略）
        query_groups: dict[int, list[dict]] = {}
        for entry in pending_entries:
            qid = entry.get("query_id", 0)
            query_groups.setdefault(qid, []).append(entry)

        # 5. 逐组调用 LLM 提取
        total_extracted = 0
        total_processed = 0
        details = []
        now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        for qid, entries in query_groups.items():
            # 拼接对话文本
            raw_parts = []
            for entry in entries:
                raw_parts.append(entry.get("content", ""))
            dialog_text = "\n---\n".join(raw_parts)

            if not dialog_text.strip():
                for entry in entries:
                    self.store.mark_entry_extracted(entry.get("id", ""))
                skipped_count += len(entries)
                continue

            # 输入长度截断保护
            MAX_INPUT_CHARS = 12000
            if len(dialog_text) > MAX_INPUT_CHARS:
                dialog_text = dialog_text[:MAX_INPUT_CHARS]
                dialog_text += "\n\n[注意：原始记录过长，已截断]"

            # 构造 LLM 请求
            prompt = self.extraction_prompt.replace("{dialog}", dialog_text)
            messages = [{"role": "user", "content": prompt}]

            # 调用 LLM
            response = self._call_llm(messages)
            if not response:
                # LLM 失败，不标记为已提取，下次再试
                for entry in entries:
                    details.append({
                        "entry_id": entry.get("id", ""),
                        "query_id": qid,
                        "action": "LLM调用失败，保留待下次提取",
                    })
                continue

            # 解析 LLM 返回
            raw_records = self._parse_llm_response(response)
            if not raw_records:
                # 无 Bug，标记为已提取
                for entry in entries:
                    self.store.mark_entry_extracted(entry.get("id", ""))
                    details.append({
                        "entry_id": entry.get("id", ""),
                        "query_id": qid,
                        "action": "LLM判定无Bug，标记已提取",
                    })
                total_processed += len(entries)
                continue

            # 构造 BugRecord 并存储
            for raw in raw_records:
                affected_files = raw.get("affected_files", [])
                if not affected_files:
                    continue

                module = self.store._resolve_module(affected_files[0])

                file_hashes = {}
                for fp in affected_files:
                    h = self.store._compute_file_hash(fp)
                    if h:
                        file_hashes[fp] = h

                record_id = self.store.generate_id()
                record = BugRecord(
                    id=record_id,
                    title=raw.get("title", "未命名问题"),
                    module=module,
                    affected_files=affected_files,
                    affected_functions=raw.get("affected_functions", []),
                    root_cause=raw.get("root_cause", ""),
                    symptoms=raw.get("symptoms", ""),
                    fix_pattern=raw.get("fix_pattern", ""),
                    caution=raw.get("caution", ""),
                    generalization=raw.get("generalization", ""),
                    status="open",
                    file_hashes=file_hashes,
                    created_at=now_str,
                    source_session="raw_extraction",
                    memory_linked=None,
                )

                self.store.add(record)
                total_extracted += 1
                details.append({
                    "entry_id": "",
                    "query_id": qid,
                    "action": f"提取出Bug: {record_id} ({record.title})",
                })

            # 标记原始条目为已提取
            for entry in entries:
                self.store.mark_entry_extracted(entry.get("id", ""))
            total_processed += len(entries)

        return {
            "processed": total_processed,
            "extracted": total_extracted,
            "skipped": skipped_count,
            "details": details,
        }

    def _load_prompt(self) -> str:
        """加载 prompts/extraction_prompt.txt。"""
        prompt_path = self.PROMPT_DIR / "extraction_prompt.txt"
        if not prompt_path.exists():
            return "请从以下对话中提取所有 Bug/问题/错误。\n\n对话内容：\n{dialog}"
        return prompt_path.read_text(encoding="utf-8")

    def _format_dialog(self, api_messages: list[dict]) -> str:
        """将 api_messages 格式化为对话文本。"""
        lines = []
        for msg in api_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content:
                continue
            role_label = {"user": "用户", "assistant": "AI助手", "system": "系统"}.get(role, role)
            lines.append(f"### {role_label}：\n{content}")
        return "\n\n".join(lines)

    def _call_llm(self, messages: list[dict]) -> str:
        """调用 LLM 并返回完整文本。"""
        try:
            # 兼容不同 LLM 客户端接口
            if hasattr(self.llm, "stream_chat"):
                result = self.llm.stream_chat(messages)
                if isinstance(result, tuple):
                    return result[0]
                return result
            elif hasattr(self.llm, "chat"):
                result = self.llm.chat(messages)
                if isinstance(result, tuple):
                    return result[0]
                return result
            else:
                return ""
        except Exception:
            return ""

    def _parse_llm_response(self, response: str) -> list[dict]:
        """解析 LLM 返回的 JSON 列表为Bug记录字典。

        支持多种格式：
        - 纯 JSON 数组
        - ```json ... ``` 代码块包裹
        - 包含多余文本但内有 JSON 数组
        """
        response = response.strip()

        # 尝试提取 ```json ... ``` 代码块
        code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)```", response, re.DOTALL)
        if code_block_match:
            response = code_block_match.group(1).strip()

        # 尝试直接解析
        try:
            data = json.loads(response)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "bugs" in data:
                return data["bugs"]
        except json.JSONDecodeError:
            pass

        # 尝试提取最外层的 [ ... ]
        bracket_match = re.search(r"\[.*\]", response, re.DOTALL)
        if bracket_match:
            try:
                data = json.loads(bracket_match.group(0))
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        return []

    def _resolve_module(self, file_path: str) -> str:
        """根据文件路径推断模块（委托给 store 的同名方法）。"""
        return self.store._resolve_module(file_path)
