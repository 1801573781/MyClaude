"""记忆提取器。

对应设计文档第一章 1.4 节。

职责：
- Query 结束后批量处理 status=raw 的条目
- 使用 LLM 从原始对话中提取 1~3 条结构化记忆
- 前置过滤降低 LLM 调用成本
- 更新条目状态为 unprocessed 或 processed
- 维护倒排索引和实体规范化
"""

import logging
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

    在 Query 结束后由 query_loop.py 显式调用 extract() 方法，
    批量处理该 Query 中所有 status=raw 的条目。
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

    def set_llm_chat_fn(self, fn):
        """注入 LLM 调用函数。"""
        self._llm_chat_fn = fn

    def set_progress_callback(self, callback):
        """注入进度回调函数。

        Args:
            callback: 回调函数，签名 callback(completed: int, total: int, action: str)
        """
        self._progress_callback = callback

    def extract_raw_entries(self) -> dict:
        """提取入口：处理所有 status=raw 的条目。

        Returns:
            统计信息字典
        """
        raw_entries = self._store.get_raw_entries()
        if not raw_entries:
            return {"skipped": True, "reason": "no_raw_entries", "processed": 0}

        # 按 query_id 分组
        query_groups: Dict[int, List[Dict]] = {}
        for entry in raw_entries:
            qid = entry.get("query_id", 0)
            query_groups.setdefault(qid, []).append(entry)

        total_extracted = 0
        total_marked_processed = 0
        total_filtered = 0
        total_timed_out = 0
        details: List[Dict[str, Any]] = []

        def _entry_brief(entry: Dict) -> Dict:
            """提取条目的摘要信息（用于明细展示）。"""
            meta = entry.get("metadata", {})
            user_input = meta.get("user_input", "")
            content_preview = entry.get("content", "")[:80].replace("\n", " ")
            return {
                "id": entry.get("id", ""),
                "query_id": entry.get("query_id", 0),
                "turn": meta.get("turn", 0),
                "user_input": user_input,
                "content_preview": content_preview,
            }

        total_groups = len(query_groups)
        for group_idx, (qid, entries) in enumerate(query_groups.items(), 1):
            # 通知进度回调
            if self._progress_callback:
                try:
                    self._progress_callback(group_idx, total_groups, "正在处理")
                except Exception:
                    pass

            # 前置过滤
            if self._should_skip_extraction(entries):
                logger.info(f"Query {qid} 被前置过滤跳过")
                for entry in entries:
                    self._store.update_layer0_entry(
                        entry["id"],
                        {"status": "processed"},
                    )
                    self._store.update_metadata_entry(entry["id"], status="processed")
                    details.append({
                        **_entry_brief(entry),
                        "action": "前置过滤跳过",
                        "reason": "对话过短或无技术关键词，不值得提取",
                    })
                total_filtered += len(entries)
                total_marked_processed += len(entries)
                continue

            # 雪崩防护：连续 2 次超时则跳过
            if self._consecutive_timeout_count >= 2:
                logger.warning("连续 2 次提取超时，跳过本轮提取")
                for entry in entries:
                    details.append({
                        **_entry_brief(entry),
                        "action": "超时跳过",
                        "reason": "连续 2 次 LLM 提取超时，雪崩防护",
                    })
                total_timed_out += len(entries)
                continue

            # LLM 提取
            extracted_memories = self._extract_with_llm(entries)
            if extracted_memories is None:
                # 超时或失败，保留 raw 状态
                for entry in entries:
                    details.append({
                        **_entry_brief(entry),
                        "action": "超时/失败",
                        "reason": "LLM 提取超时或调用失败，保留 raw 状态",
                    })
                total_timed_out += len(entries)
                continue

            if not extracted_memories:
                # LLM 返回 NONE，标记为 processed
                for entry in entries:
                    self._store.update_layer0_entry(
                        entry["id"],
                        {"status": "processed"},
                    )
                    self._store.update_metadata_entry(entry["id"], status="processed")
                    details.append({
                        **_entry_brief(entry),
                        "action": "LLM判定无价值，标记已处理",
                        "reason": "LLM 返回 NONE，认为无可提取的长期记忆",
                    })
                total_marked_processed += len(entries)
                continue

            # 写入提取结果
            extracted_summaries = []
            for memory in extracted_memories:
                self._write_extracted_memory(entries[0], memory)
                total_extracted += 1
                tags_str = "".join(f"[{t}]" for t in memory.get("tags", []))
                extracted_summaries.append(f"{tags_str} {memory.get('content', '')[:60]}")

            # 将原始条目标记为 processed（已提取）
            for entry in entries:
                self._store.update_layer0_entry(
                    entry["id"],
                    {"status": "processed", "source": "query_extraction"},
                )
                self._store.update_metadata_entry(entry["id"], status="processed")
                details.append({
                    **_entry_brief(entry),
                    "action": "成功提取后，标记已处理",
                    "reason": f"提取出 {len(extracted_memories)} 条记忆: " + " | ".join(extracted_summaries),
                })
            total_marked_processed += len(entries)

        # 提取完成
        if self._progress_callback:
            try:
                self._progress_callback(total_groups, total_groups, "完成")
            except Exception:
                pass

        # 持久化元数据
        self._store.save_metadata()

        return {
            "processed": len(raw_entries),
            "extracted": total_extracted,
            "marked_processed": total_marked_processed,
            "filtered": total_filtered,
            "timed_out": total_timed_out,
            "details": details,
        }

    def _should_skip_extraction(self, entries: List[Dict]) -> bool:
        """前置过滤：判断是否跳过 LLM 提取。

        对应设计文档 1.4.0 节。

        Args:
            entries: 同一 Query 的所有 raw 条目

        Returns:
            True 表示跳过（不值得提取）
        """
        if not entries:
            return True

        # 条件 1: Turn 数 < 2 且无工具调用
        has_tools = any(
            e.get("metadata", {}).get("has_tools", False) for e in entries
        )
        if len(entries) < 2 and not has_tools:
            return True

        # 条件 2: 用户输入 < 10 字符且无技术关键词
        user_inputs = [
            e.get("metadata", {}).get("user_input", "") for e in entries
        ]
        all_user_text = " ".join(user_inputs)
        if len(all_user_text) < 10 and not self._has_tech_keywords(all_user_text):
            return True

        # 条件 3: 整轮对话总字符数 < 50
        total_chars = sum(len(e.get("content", "")) for e in entries)
        if total_chars < 50:
            return True

        return False

    def _has_tech_keywords(self, text: str) -> bool:
        """检查文本中是否包含技术关键词。"""
        text_lower = text.lower()
        for kw in self._TECH_KEYWORDS:
            if kw.lower() in text_lower:
                return True
        return False

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
            response = self._call_llm_with_timeout(prompt, timeout=30)
            if response is None:
                # 超时
                self._consecutive_timeout_count += 1
                return None

            self._consecutive_timeout_count = 0
            return self._parse_extraction_response(response)
        except Exception as e:
            logger.error(f"LLM 提取失败: {e}")
            self._consecutive_timeout_count += 1
            return None

    def _call_llm_with_timeout(self, prompt: str, timeout: int = 5) -> Optional[str]:
        """调用 LLM，带超时保护。

        Args:
            prompt: 完整 Prompt
            timeout: 超时秒数

        Returns:
            LLM 响应文本，None 表示超时
        """
        import signal
        import threading

        result = {"response": None, "done": False}

        def _call():
            try:
                response = self._llm_chat_fn(
                    prompt,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
                result["response"] = response
            except Exception as e:
                logger.error(f"LLM 调用异常: {e}")
            finally:
                result["done"] = True

        # 使用线程实现超时（signal 在非主线程中不可用）
        thread = threading.Thread(target=_call, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if not result["done"]:
            logger.warning(f"LLM 提取超时（{timeout}s）")
            return None

        return result["response"]

    def _parse_extraction_response(self, response: str) -> List[Dict]:
        """解析 LLM 提取响应。

        预期格式：
            - MEMORY: [主题标签] 记忆内容描述
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

            # 匹配: - MEMORY: [标签1] [标签2] 内容
            match = re.match(
                r"^-\s*MEMORY:\s*\[([^\]]+)\]\s*(.+)$",
                line,
            )
            if match:
                tags_str = match.group(1)
                content = match.group(2).strip()
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]

                # 实体规范化
                content = self._normalize_entities(content, tags)

                memories.append({
                    "tags": tags,
                    "content": content,
                })

            # 重试格式：MEMORY: [标签] 内容（无前导 -）
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
                    "tags": tags,
                    "content": content,
                })

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
        """将提取的结构化记忆写入 Layer 0 并更新元数据。

        Args:
            template_entry: 原始条目（用于继承 session_id, query_id 等）
            memory: 提取出的记忆（含 tags 和 content）

        Returns:
            新条目 ID
        """
        from datetime import datetime

        now = datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        entry_id = f"m_{timestamp_str}_{self._store._seq_counter}"
        self._store._seq_counter += 1

        iso_timestamp = now.isoformat()

        entry = {
            "id": entry_id,
            "timestamp": iso_timestamp,
            "session_id": template_entry.get("session_id", ""),
            "query_id": template_entry.get("query_id", 0),
            "tags": memory.get("tags", []),
            "content": memory.get("content", ""),
            "source": "query_extraction",
            "status": "unprocessed",
            "compacted": False,
            "evolved": False,
            "metadata": {
                "created_at": iso_timestamp,
                "last_accessed": iso_timestamp,
                "access_count": 0,
            },
        }

        # 追加到 Layer 0
        import json

        line = json.dumps(entry, ensure_ascii=False)
        try:
            with open(self._store._layer0_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.error(f"写入提取记忆失败: {e}")
            return ""

        # 同步追加到 raw_memory.md（人类可读副本）
        self._store._append_layer0_md(entry)

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
        tags_str = "".join(f"[{t}]" for t in memory.get("tags", []))
        layer1_line = f"- {tags_str} {memory.get('content', '')}"
        if entry_id:
            layer1_line += f" (id={entry_id})"

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
            "使用已有记忆中的标准名称。\n\n"
            "输出格式（每条一行）：\n"
            "- MEMORY: [主题标签] 记忆内容描述\n"
            "或\n"
            "- NONE\n\n"
            "交互记录（按时间排列）：\n"
            "{raw_entries}"
        )
