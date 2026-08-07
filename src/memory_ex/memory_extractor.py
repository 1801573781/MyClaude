"""记忆提取器。

职责：
- 从 MD 会话日志中批量提取结构化记忆
- 使用 LLM 从原始对话中提取 1~3 条结构化记忆
- 前置过滤降低 LLM 调用成本
- 维护倒排索引和实体规范化
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Prompt 模板路径
_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """加载 Prompt 模板文件。"""
    try:
        return (_PROMPT_DIR / filename).read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(f"Prompt 模板未找到: {filename}")
        return ""


class MemoryExtractor:
    """记忆提取器。

    从 MD 会话日志中提取结构化记忆，写入 Layer 1（MEMORY.md）。
    由 query_loop.py 或 CLI /mem extract 命令显式调用。
    """

    # 技术关键词列表（用于前置过滤判断）
    _TECH_KEYWORDS = {
        "py", "python", "bug", "error", "配置", "config", "架构", "数据库",
        "api", "工具", "路径", "文件", "代码", "函数", "类", "模块",
        "测试", "test", "部署", "docker", "git", "重构", "修复",
        "创建", "修改", "删除", "安装", "运行", "执行",
    }

    def __init__(self, mem_config: Any, store: Any):
        """初始化提取器。

        Args:
            mem_config: memory_ex.yaml 配置对象
            store: MemoryStore 实例
        """
        self._store = store
        ext_config = mem_config.extractor
        self._temperature = float(getattr(ext_config, "temperature", 0.2))
        self._max_tokens = int(getattr(ext_config, "max_tokens", 512))
        self._max_entries_per_query = int(getattr(ext_config, "max_entries_per_query", 3))
        self._timeout = int(getattr(ext_config, "timeout", 60))

        # LLM 调用函数（延迟注入）
        self._llm_chat_fn = None

        # 超时重试计数
        self._consecutive_timeout_count = 0

        # 进度回调函数
        self._progress_callback = None

        # 错题集实例（延迟注入，用于 caution 类型的双向关联）
        self._problem_base = None

        # 最近一次 LLM 调用的失败原因（timeout / empty_response / error / None）
        self._last_error_reason = None

    def set_llm_chat_fn(self, fn):
        """注入 LLM 调用函数。"""
        self._llm_chat_fn = fn

    def set_progress_callback(self, callback):
        """注入进度回调函数。

        Args:
            callback: 回调函数，签名 callback(completed: int, total: int, action: str)
        """
        self._progress_callback = callback

    def set_problem_base(self, problem_base):
        """注入错题集实例，用于 caution 类型的双向关联。

        Args:
            problem_base: ProblemBase 实例
        """
        self._problem_base = problem_base

    def _find_related_problem(self, memory: Dict) -> Optional[str]:
        """尝试为 caution 类型记忆匹配错题集中的记录。

        匹配策略（按优先级）：
        1. 通过 affected_files 路径匹配
        2. 通过内容关键词匹配（如相同的函数名、相同的文件路径片段）

        Args:
            memory: 提取出的记忆（含 type, tags, content）

        Returns:
            匹配的错题 ID，未匹配返回 None
        """
        if not self._problem_base:
            return None

        content = memory.get("content", "")

        # 从内容中提取文件路径片段
        file_paths = re.findall(r"src/[\w/]+\.\w+", content)
        for fp in file_paths:
            records = self._problem_base.get_by_file(fp)
            if records:
                return records[0].id

        # 从内容中提取函数名/类名关键词匹配
        open_records = self._problem_base.get_all_open()
        for record in open_records:
            for func_name in record.affected_functions:
                if func_name in content:
                    return record.id

        return None

    def _read_mem_ext_record(self) -> Dict[str, int]:
        """读取已提取记录（文件名 → 字节偏移量）。

        返回字典，key 为 MD 文件名，value 为上次提取时的文件字节大小。
        下次提取时，从该偏移量之后读取新增内容。
        """
        import json

        record_path = Path(self._store._base_dir) / "memory" / "mem_ext_record.json"
        if not record_path.exists():
            return self._migrate_old_record()
        try:
            data = json.loads(record_path.read_text(encoding="utf-8"))
            return {k: int(v) for k, v in data.items()}
        except Exception:
            return {}

    def _migrate_old_record(self) -> Dict[str, int]:
        """从旧格式 mem_ext_record.md 迁移到 JSON 格式。

        旧格式仅记录文件名，迁移时将每个文件当前大小作为偏移量，
        确保旧内容不会重复提取。
        """
        import os

        old_path = Path(self._store._base_dir) / "mem_ext_record.md"
        if not old_path.exists():
            return {}
        try:
            old_names = set(
                line.strip()
                for line in old_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            raw_memory_dir = Path(self._store._base_dir) / "raw_session_log"
            record: Dict[str, int] = {}
            for name in old_names:
                fp = raw_memory_dir / name
                if fp.exists():
                    record[name] = os.path.getsize(str(fp))
            self._save_mem_ext_record(record)
            old_path.unlink()
            logger.info(f"迁移旧提取记录: {len(record)} 条")
            return record
        except Exception as e:
            logger.warning(f"迁移旧提取记录失败: {e}")
            return {}

    def _save_mem_ext_record(self, record: Dict[str, int]):
        """保存已提取记录到 JSON 文件。"""
        import json

        record_path = Path(self._store._base_dir) / "memory" / "mem_ext_record.json"
        try:
            record_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"写入 mem_ext_record.json 失败: {e}")

    def _mark_file_extracted(self, filename: str, byte_offset: int):
        """标记 MD 文件已提取到指定字节偏移量。

        Args:
            filename: MD 日志文件名
            byte_offset: 提取完成时的文件字节大小
        """
        record = self._read_mem_ext_record()
        record[filename] = byte_offset
        self._save_mem_ext_record(record)

    def _read_incremental_content(self, file_path: Path, byte_offset: int) -> str:
        """从指定字节偏移读取文件内容（增量或全量）。

        处理偏移落在行中间或多字节字符中间的情况：
        跳到下一个完整行的起始，确保不截断 UTF-8 字符。

        Args:
            file_path: MD 文件路径
            byte_offset: 起始字节偏移（0 表示全量读取）

        Returns:
            读取到的文本内容
        """
        if byte_offset == 0:
            return file_path.read_text(encoding="utf-8")

        with open(file_path, "rb") as f:
            f.seek(byte_offset)
            raw_bytes = f.read()

        if not raw_bytes:
            return ""

        # 尝试直接解码
        try:
            content = raw_bytes.decode("utf-8")
            # 如果不是从行首开始，跳到下一行
            if not content.startswith("\n"):
                first_newline = content.find("\n")
                if first_newline >= 0:
                    content = content[first_newline + 1:]
        except UnicodeDecodeError:
            # 偏移落在多字节字符中间，跳到下一行边界
            first_newline = raw_bytes.find(b"\n")
            if first_newline >= 0:
                content = raw_bytes[first_newline + 1:].decode("utf-8", errors="replace")
            else:
                content = ""

        return content

    def extract_from_md_logs(self) -> dict:
        """从 MD 会话日志中提取结构化记忆。

        扫描 {raw_memory}/MyClaude_*.md 文件，
        跳过已提取的文件（记录在 mem_ext_record.md 中），
        调用 LLM 提取记忆，写入 MEMORY.md。
        """
        raw_memory_dir = Path(self._store._base_dir) / "raw_session_log"
        if not raw_memory_dir.exists():
            return {"skipped": True, "reason": "raw_session_log目录不存在", "processed": 0}

        md_files = sorted(raw_memory_dir.glob("MyClaude_*.md"))
        if not md_files:
            return {"skipped": True, "reason": "无MD会话日志", "processed": 0}

        extracted_files = self._read_mem_ext_record()
        pending_files = [f for f in md_files if f.name not in extracted_files]
        if not pending_files:
            return {"skipped": True, "reason": "所有MD日志已提取", "processed": 0}

        # 每轮提取开始时重置雪崩计数器和错误状态
        self._consecutive_timeout_count = 0
        self._last_error_reason = None

        total_extracted = 0
        total_processed = 0
        total_filtered = 0
        total_timeout = 0
        total_empty_response = 0
        total_error = 0
        total_llm_none = 0
        total_skipped = 0
        details: List[Dict[str, Any]] = []

        total_files = len(pending_files)
        for file_idx, md_file in enumerate(pending_files, 1):
            if self._progress_callback:
                try:
                    self._progress_callback(file_idx, total_files, "正在处理")
                except Exception:
                    pass

            try:
                raw_content = md_file.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"读取 MD 文件失败 {md_file.name}: {e}")
                continue

            # 从 MD 日志中提取用户输入
            user_input = self._extract_user_input_from_md(raw_content)

            # 智能提取内容预览（优先展示 Query 段，而非 CLI 噪音）
            content_preview = self._extract_content_preview(raw_content)

            if not raw_content.strip():
                self._mark_file_extracted(md_file.name, os.path.getsize(str(md_file)))
                total_filtered += 1
                details.append({
                    "id": md_file.name, "query_id": 0, "turn": 0,
                    "user_input": user_input, "content_preview": "(空文件)",
                    "action": "空文件跳过", "reason": "MD 日志文件为空",
                })
                continue

            # 原始内容完整送 LLM 提取（CLI 段中也可能包含有价值信息）
            content = raw_content

            if self._consecutive_timeout_count >= 3:
                logger.warning("连续 3 次提取超时，跳过本轮剩余文件")
                details.append({
                    "id": md_file.name, "query_id": 0, "turn": 0,
                    "user_input": user_input, "content_preview": content_preview,
                    "action": "雪崩跳过", "reason": "连续 3 次 LLM 提取超时，雪崩防护",
                })
                total_skipped += 1
                continue

            session_id = md_file.name
            extracted_memories = self._extract_with_llm([{"content": content}])

            if extracted_memories is None:
                error_reason = self._last_error_reason or "unknown"
                if error_reason == "timeout":
                    reason_text = f"LLM 提取超时（{self._timeout}s），保留待下次提取"
                    total_timeout += 1
                elif error_reason == "empty_response":
                    reason_text = "LLM 返回空响应（可能因输入过长或内部异常），保留待下次提取"
                    total_empty_response += 1
                else:
                    reason_text = f"LLM 调用失败（{error_reason}），保留待下次提取"
                    total_error += 1
                details.append({
                    "id": md_file.name, "query_id": 0, "turn": 0,
                    "user_input": user_input, "content_preview": content_preview,
                    "action": f"超时/失败（{error_reason}）", "reason": reason_text,
                })
                continue

            if not extracted_memories:
                self._mark_file_extracted(md_file.name, os.path.getsize(str(md_file)))
                total_llm_none += 1
                total_processed += 1
                details.append({
                    "id": md_file.name, "query_id": 0, "turn": 0,
                    "user_input": user_input, "content_preview": content_preview,
                    "action": "LLM判定无价值，标记已提取", "reason": "LLM 返回 NONE",
                })
                continue

            extracted_summaries = []
            for idx, memory in enumerate(extracted_memories, 1):
                if memory.get("type") == "caution" and self._problem_base:
                    pb_id = self._find_related_problem(memory)
                    if pb_id:
                        memory["source_problem_id"] = pb_id

                template_entry = {"session_id": session_id, "query_id": 0}
                new_id = self._write_extracted_memory(template_entry, memory)
                total_extracted += 1

                if memory.get("type") == "caution" and memory.get("source_problem_id") and new_id:
                    if self._problem_base:
                        self._problem_base.mark_memory_linked(
                            memory["source_problem_id"], new_id
                        )

                tags_str = "".join(f"[{t}]" for t in memory.get("tags", []))
                extracted_summaries.append(f"({idx}) {tags_str} {memory.get('content', '')[:300]}")

            self._mark_file_extracted(md_file.name, os.path.getsize(str(md_file)))
            total_processed += 1
            reason_lines = [f"提取出 {len(extracted_memories)} 条记忆:"]
            for summary in extracted_summaries:
                reason_lines.append(f"  {summary}")
            details.append({
                "id": md_file.name, "query_id": 0, "turn": 0,
                "user_input": user_input, "content_preview": content_preview,
                "action": "成功提取后，标记已提取",
                "reason": "\n".join(reason_lines),
            })

        if self._progress_callback:
            try:
                self._progress_callback(total_files, total_files, "完成")
            except Exception:
                pass

        self._store.save_metadata()

        return {
            "processed": total_processed,
            "extracted": total_extracted,
            "marked_processed": total_processed,
            "filtered": total_filtered,
            "timeout": total_timeout,
            "empty_response": total_empty_response,
            "error": total_error,
            "llm_none": total_llm_none,
            "skipped": total_skipped,
            "details": details,
        }

    def _should_skip_extraction(self, entries: List[Dict]) -> bool:
        """前置过滤：判断是否跳过 LLM 提取。

        已彻底禁用所有内容过滤条件。
        用户要求：不得以"对话过短"或"无技术关键词"为由跳过提取，
        因为无法预判对话中是否包含有价值的信息。
        所有条目一律送 LLM 提取，由 LLM 判断是否有值得记忆的内容。

        Args:
            entries: 同一 Query 的所有 raw 条目

        Returns:
            True 表示跳过（仅在 entries 为空时跳过）
        """
        if not entries:
            return True

        return False

    def _has_tech_keywords(self, text: str) -> bool:
        """检查文本中是否包含技术关键词。"""
        text_lower = text.lower()
        for kw in self._TECH_KEYWORDS:
            if kw.lower() in text_lower:
                return True
        return False

    def _extract_user_input_from_md(self, content: str) -> str:
        """从 MD 日志内容中提取用户输入。

        MD 日志格式: "## 📋 Query N: 用户输入内容"
        提取第一个 Query 的用户输入作为代表。
        """
        match = re.search(r"^## 📋 Query \d+: (.+)$", content, re.MULTILINE)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_content_preview(self, content: str, max_chars: int = 500) -> str:
        """从 MD 日志中智能提取内容预览。

        优先展示 Query 段内容（包含用户输入和 LLM 交互），
        如果没有 Query 段则回退到文件开头。
        """
        query_match = re.search(
            r"(^## 📋 Query \d+:.*?)(?=^## (?:📋 Query|⌨️ CLI)|\Z)",
            content,
            flags=re.MULTILINE | re.DOTALL,
        )
        if query_match:
            return query_match.group(1)[:max_chars]
        return content[:max_chars]

    def _extract_with_llm(self, entries: List[Dict]) -> Optional[List[Dict]]:
        """调用 LLM 从原始条目中提取结构化记忆。

        Args:
            entries: 同一 Query 的 raw 条目列表

        Returns:
            提取的记忆列表，None 表示超时/失败，空列表表示 LLM 返回 NONE
        """
        if not self._llm_chat_fn:
            logger.warning("LLM 调用函数未注入，跳过提取")
            return None

        # 拼接原始条目内容
        raw_parts = []
        for entry in entries:
            raw_parts.append(entry.get("content", ""))
        raw_entries_text = "\n---\n".join(raw_parts)

        # 输入长度截断保护：防止多轮对话拼接后过长导致 LLM 超时
        MAX_INPUT_CHARS = 12000
        if len(raw_entries_text) > MAX_INPUT_CHARS:
            logger.info(
                f"原始记录过长（{len(raw_entries_text)} 字符），截断至 {MAX_INPUT_CHARS} 字符"
            )
            raw_entries_text = raw_entries_text[:MAX_INPUT_CHARS]
            raw_entries_text += "\n\n[注意：原始记录过长，已截断，仅展示前部分内容]"

        # 加载 Prompt 模板
        prompt_template = _load_prompt("extraction_prompt.txt")
        if not prompt_template:
            # 降级：使用内置 Prompt
            prompt_template = self._get_builtin_prompt()

        prompt = prompt_template.replace("{raw_entries}", raw_entries_text)

        # 实体规范化预处理
        prompt = self._add_entity_context(prompt)

        try:
            response = self._call_llm_with_timeout(prompt, timeout=self._timeout)
            if response is None:
                # 仅超时才递增雪崩计数器，空响应/异常不触发雪崩
                if self._last_error_reason == "timeout":
                    self._consecutive_timeout_count += 1
                return None

            self._consecutive_timeout_count = 0
            return self._parse_extraction_response(response)
        except Exception as e:
            logger.error(f"LLM 提取失败: {e}")
            return None

    def _call_llm_with_timeout(self, prompt: str, timeout: int = 120) -> Optional[str]:
        """调用 LLM，带超时保护。

        直接将 timeout 传递给 simple_chat，由 httpx 在 HTTP 层处理超时，
        确保超时后连接被正确关闭，避免线程泄漏和僵尸连接。

        Args:
            prompt: 完整 Prompt
            timeout: 超时秒数，默认 120 秒

        Returns:
            LLM 响应文本，None 表示超时或调用失败（含空响应）
        """
        import time

        try:
            start = time.time()
            response = self._llm_chat_fn(
                prompt,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                timeout=float(timeout),
            )
            elapsed = time.time() - start

            if not response:
                # simple_chat 内部捕获异常后返回空字符串
                # 用耗时启发式区分：接近超时阈值 → 大概率是 HTTP 超时
                if elapsed >= timeout * 0.9:
                    logger.warning(
                        f"LLM 疑似超时（耗时 {elapsed:.1f}s，阈值 {timeout}s）"
                    )
                    self._last_error_reason = "timeout"
                else:
                    logger.warning(
                        f"LLM 返回空响应（耗时 {elapsed:.1f}s）"
                    )
                    self._last_error_reason = "empty_response"
                return None

            logger.info(f"LLM 提取成功（耗时 {elapsed:.1f}s）")
            self._last_error_reason = None
            return response
        except Exception as e:
            logger.error(f"LLM 调用异常: {e}")
            self._last_error_reason = "error"
            return None

    def _parse_extraction_response(self, response: str) -> List[Dict]:
        """解析 LLM 提取响应。

        支持两种格式：
            新格式：- MEMORY: [type:reference 或 type:caution] [主题标签] 记忆内容描述
            旧格式：- MEMORY: [主题标签] 记忆内容描述（默认 type=reference）
            或
            - NONE

        Returns:
            解析出的记忆列表，空列表表示 NONE
        """
        if not response:
            return []

        response = response.strip()

        # 检查 NONE
        if response.upper().startswith("NONE") or response == "- NONE":
            return []

        memories = []
        for line in response.split("\n"):
            line = line.strip()
            if not line:
                continue

            # 新格式: - MEMORY: [type:reference] [标签1, 标签2] 内容
            match = re.match(
                r"^-\s*MEMORY:\s*\[type:(reference|caution)\]\s*\[([^\]]+)\]\s*(.+)$",
                line,
            )
            if match:
                mem_type = match.group(1)
                tags_str = match.group(2)
                content = match.group(3).strip()
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]

                content = self._normalize_entities(content, tags)
                memories.append({
                    "type": mem_type,
                    "tags": tags,
                    "content": content,
                })
                continue

            # 新格式（无前导 -）: MEMORY: [type:reference] [标签] 内容
            match = re.match(
                r"^MEMORY:\s*\[type:(reference|caution)\]\s*\[([^\]]+)\]\s*(.+)$",
                line,
            )
            if match:
                mem_type = match.group(1)
                tags_str = match.group(2)
                content = match.group(3).strip()
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]

                content = self._normalize_entities(content, tags)
                memories.append({
                    "type": mem_type,
                    "tags": tags,
                    "content": content,
                })
                continue

            # 旧格式兼容: - MEMORY: [标签1] [标签2] 内容
            match = re.match(
                r"^-\s*MEMORY:\s*\[([^\]]+)\]\s*(.+)$",
                line,
            )
            if match:
                tags_str = match.group(1)
                content = match.group(2).strip()
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]

                content = self._normalize_entities(content, tags)
                memories.append({
                    "type": "reference",
                    "tags": tags,
                    "content": content,
                })
                continue

            # 旧格式兼容（无前导 -）: MEMORY: [标签] 内容
            match = re.match(
                r"^MEMORY:\s*\[([^\]]+)\]\s*(.+)$",
                line,
            )
            if match:
                tags_str = match.group(1)
                content = match.group(2).strip()
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]

                content = self._normalize_entities(content, tags)
                memories.append({
                    "type": "reference",
                    "tags": tags,
                    "content": content,
                })
                continue

        # 限制最大条目数
        if len(memories) > self._max_entries_per_query:
            memories = memories[: self._max_entries_per_query]

        return memories

    def _normalize_entities(self, content: str, tags: List[str]) -> str:
        """实体规范化：将别名替换为标准名称。"""
        aliases = self._store._metadata_cache.get("entity_aliases", {})
        normalized = content
        for alias, canonical in aliases.items():
            # 使用全词匹配替换
            pattern = re.compile(r"\b" + re.escape(alias) + r"\b")
            normalized = pattern.sub(canonical, normalized)
        return normalized

    def _add_entity_context(self, prompt: str) -> str:
        """在 Prompt 中添加已有实体信息，帮助 LLM 做实体规范化。"""
        inv_index = self._store._metadata_cache.get("inverted_index", {})
        entities = list(inv_index.get("entities", {}).keys())
        aliases = self._store._metadata_cache.get("entity_aliases", {})

        if entities or aliases:
            context = "\n\n已有标准实体名称（请保持一致）：\n"
            if entities:
                context += ", ".join(entities[:20]) + "\n"
            if aliases:
                context += "别名映射：\n"
                for alias, canonical in list(aliases.items())[:10]:
                    context += f"  {alias} → {canonical}\n"
            prompt += context

        return prompt

    def _write_extracted_memory(self, template_entry: Dict, memory: Dict) -> str:
        """将提取的结构化记忆写入 Layer 1 并更新元数据。

        Args:
            template_entry: 原始条目（用于继承 session_id, query_id 等）
            memory: 提取出的记忆（含 type, tags, content）

        Returns:
            新条目 ID
        """
        from datetime import datetime
        import uuid

        now = datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        # 生成唯一 ID（不再依赖 Layer 0 的序号计数器）
        entry_id = f"m_{timestamp_str}_{uuid.uuid4().hex[:6]}"
        iso_timestamp = now.isoformat()

        # 获取类型，默认为 reference
        mem_type = memory.get("type", "reference")

        # 更新元数据
        self._store.update_metadata_entry(
            entry_id,
            tags=memory.get("tags", []),
            status="unprocessed",
            is_consumed=False,
            is_evolved=False,
            created_at=iso_timestamp,
            last_accessed=iso_timestamp,
            access_count=0,
            importance_score=None,
        )

        # 更新倒排索引
        self._store.update_inverted_index(
            entry_id,
            memory.get("tags", []),
            memory.get("content", ""),
        )

        # 追加写入 Layer 1（MEMORY.md）—— 构建职责
        # extract() 负责将提取的记忆写入 Layer 1，不再依赖 compact() 来搬运
        # 写入 session_id 以支持召回时的显式过滤（禁止召回当前 session 的记忆）
        # caution 类型在 Layer 1 中增加 [caution] 标记
        session_id = template_entry.get("session_id", "")
        tags = memory.get("tags", [])
        if mem_type == "caution":
            tags = ["caution"] + tags  # caution 标记放在最前
        tags_str = "".join(f"[{t}]" for t in tags)
        layer1_line = f"- {tags_str} {memory.get('content', '')}"
        if entry_id:
            layer1_line += f" (id={entry_id})"
        if session_id:
            layer1_line += f" (session={session_id})"

        existing_layer1 = self._store.read_layer1()
        if existing_layer1 and existing_layer1.strip():
            new_layer1 = existing_layer1.rstrip() + "\n\n" + layer1_line
        else:
            new_layer1 = layer1_line
        self._store.write_layer1(new_layer1)

        return entry_id

    def _get_builtin_prompt(self) -> str:
        """内置提取 Prompt（当模板文件不存在时使用）。"""
        return (
            "你是一个记忆提取专家。以下是 AI 编程助手与用户在一次任务中的完整交互记录"
            "（可能包含多个轮次）。\n"
            "请从中提取值得长期记忆的关键信息，规则如下：\n\n"
            "1. 只提取「跨 Query 仍有价值」的信息：架构决策、用户偏好、技术选型、"
            "踩过的坑、项目约束。\n"
            "2. 丢弃「一次性」信息：临时调试输出、本次对话的闲聊、已由代码固化的实现细节。\n"
            "3. 每条记忆用一句话描述，确保脱离当前上下文后仍可理解。\n"
            "4. 最多提取 3 条，宁缺毋滥。如果没有值得记忆的信息，返回 NONE。\n"
            "5. 为每条记忆打上 1~3 个主题标签（如 [数据库]、[路径规范]、[API规范]）。\n"
            "6. 如果多个轮次记录了同一件事的演进过程，只保留最终结论。\n"
            "7. 实体规范化：如果记忆中涉及的实体与已有记忆中的实体是同一对象，"
            "使用已有记忆中的标准名称。\n"
            "8. 类型判断：对于每条提取的记忆，请额外判断其类型：\n"
            "   - reference: 参考性记忆（知识、经验、偏好、设计决策等）\n"
            "   - caution: 警示性记忆（从 Bug 中归纳出的通用规则，用于避免重蹈覆辙）\n"
            "   caution 类型的准入门槛（必须满足以下条件之一）：\n"
            "   1. 该 Bug 揭示了一个可复现的代码模式\n"
            "   2. 该 Bug 反映了一个反复出现的认知偏差\n"
            "   3. 该 Bug 的教训可以跨文件、跨模块适用\n"
            "   不满足以上条件的具体 Bug，不要提取为 caution 记忆。\n\n"
            "输出格式（每条一行）：\n"
            "- MEMORY: [type:reference 或 type:caution] [主题标签] 记忆内容描述\n"
            "或\n"
            "- NONE\n\n"
            "交互记录（按时间排列）：\n"
            "{raw_entries}"
        )
