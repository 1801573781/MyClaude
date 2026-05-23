"""
memory_2 记忆注入器

将工作记忆和长期记忆格式化为 LLM 可注入的 Markdown 上下文，
支持 token 预算截断和摘要展示。

与 memory_1 注入器核心区别：无 embedding 相关内容。
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryInjector:
    """记忆注入器（纯文本版本）。

    负责：
    1. 维护当前会话的工作记忆（内存中）
    2. 将工作记忆格式化为可注入的上下文
    3. 将长期记忆一并纳入注入（可选）
    4. Token 预算控制
    """

    # 系统提醒前缀（与 session_log 的 _classify_user() 保持一致）
    MEMORY_PREFIX = "[系统提醒] 以下是与当前任务相关的历史记忆"
    WORKING_HEADER = "[当前任务上下文]"

    def __init__(self, store, config: Dict[str, Any]):
        """
        Args:
            store: MemoryStore 实例（用于读取长期记忆）
            config: memory_2 配置字典
        """
        self._store = store
        self._working_memory: List[Dict[str, str]] = []

        working_cfg = config.get("working_memory", {})
        self._max_turns = working_cfg.get("max_turns", 20)

        retrieval_cfg = config.get("retrieval", {})
        self._default_top_k = retrieval_cfg.get("default_top_k", 5)

        # Token 预算（留给记忆注入的部分）
        self._max_tokens = config.get("injection", {}).get("max_tokens", 2000)

    # ------------------------------------------------------------------ #
    #  工作记忆管理
    # ------------------------------------------------------------------ #

    def add(self, role: str, content: str, memory_id: str = "") -> None:
        """添加一条到工作记忆。

        Args:
            role: 角色（user/assistant/system）
            content: 记忆内容
            memory_id: 持久化存储中的记忆 ID（用于日志展示完整 UUID）
        """
        self._working_memory.append({
            "role": role,
            "content": content,
            "id": memory_id,
        })

        # 按轮次截断（一轮 = user + assistant，即 2 条）
        max_items = self._max_turns * 2
        if len(self._working_memory) > max_items:
            self._working_memory = self._working_memory[-max_items:]

    def get_all(self) -> List[Dict[str, str]]:
        """获取所有工作记忆。"""
        return list(self._working_memory)

    def clear(self) -> None:
        """清空工作记忆。"""
        self._working_memory.clear()

    def maintain(self, max_turns: int) -> int:
        """维护工作记忆：截断到 max_turns 轮。"""
        before = len(self._working_memory)
        max_items = max_turns * 2
        if len(self._working_memory) > max_items:
            self._working_memory = self._working_memory[-max_items:]
        return before - len(self._working_memory)

    # ------------------------------------------------------------------ #
    #  格式化注入
    # ------------------------------------------------------------------ #

    def format_working_memory(self, include_long_term: bool = False) -> str:
        """格式化工作记忆为 LLM 注入文本。

        Args:
            include_long_term: 是否同时纳入长期记忆

        Returns:
            格式化的 Markdown 文本
        """
        parts = []

        # 工作记忆部分
        if self._working_memory:
            parts.append(f"{self.MEMORY_PREFIX}\n")
            parts.append(self.WORKING_HEADER)
            for item in self._working_memory:
                role = item.get("role", "")
                content = item.get("content", "")
                if role:
                    parts.append(f"\n[{role}]: {content}")
                else:
                    parts.append(f"\n{content}")
            parts.append("")

        # 长期记忆部分（可选）
        if include_long_term:
            long_term = self._get_long_term_memories()
            if long_term:
                if not parts:
                    parts.append(f"{self.MEMORY_PREFIX}\n")
                parts.append("[长期记忆]")
                for mem in long_term:
                    content = mem.get("content", "")
                    mem_id = mem.get("id", "")[:8]
                    timestamp = mem.get("timestamp", "")[:19]
                    score = mem.get("last_score")
                    if score is not None and not isinstance(score, (int, float)):
                        score = 0.0
                    score_str = f" (相关性: {score:.2f})" if score is not None else ""
                    parts.append(f"- [id={mem_id}] [{timestamp}]{score_str}\n  {content[:300]}")
                parts.append("")

        if not parts:
            return ""

        full_text = "\n".join(parts)

        # Token 截断
        return self._truncate_by_tokens(full_text)

    def format_search_results(
        self,
        query: str,
        results: List[Dict[str, Any]],
    ) -> str:
        """格式化 search() 结果为注入文本。

        将工作记忆与检索结果合并为统一列表，按相关性降序排列。
        仅当检索结果非空时才注入工作记忆（避免不相关记忆污染上下文）。

        Args:
            query: 原始查询
            results: search() 返回的记忆列表

        Returns:
            格式化的 Markdown 文本
        """
        # 关键规则：仅当检索结果非空时才合并工作记忆。
        # 若 LLM 判定查询与所有长期/短期记忆无关（search 返回空），
        # 说明当前查询是全新话题，历史记忆不应污染上下文，
        # 工作记忆（当前会话）已在 api_messages 中，无需额外注入。
        if not results:
            return "没有召唤到相关记忆"

        # 合并工作记忆与检索结果，去重并统一排名
        merged = self._merge_working_and_retrieved(results)

        parts = [f"{self.MEMORY_PREFIX}\n"]

        # 如果没有任何记忆（极端情况）
        if not merged:
            return "没有召唤到相关记忆"
        else:
            # 统一展示，按得分降序
            parts.append("[相关历史记忆]")
            merged.sort(key=lambda x: x.get("score", 0), reverse=True)

            # 去重：按 content 前 80 字符去重
            seen = set()
            deduped = []
            for mem in merged:
                key = mem.get("content", "")[:80]
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(mem)

            for i, mem in enumerate(deduped):
                mem_id = mem.get("id", "")
                content = mem.get("content", "")
                score = mem.get("score", 0)
                if not isinstance(score, (int, float)):
                    score = 0.0
                timestamp = mem.get("timestamp", "")[:19] if mem.get("timestamp") else ""
                turn = mem.get("turn", "?")

                if len(content) > 400:
                    content = content[:400] + "..."

                # 统一使用 UUID 显示 ID（工作记忆也应有持久化 UUID）
                if mem_id:
                    id_display = f"id={mem_id}"
                else:
                    id_display = "(未知)"

                parts.append(f"- [{id_display}]")
                parts.append("")
                parts.append(
                    f"[用户输入] {content} (相关性: {score:.2f})"
                )

        full_text = "\n".join(parts)
        return self._truncate_by_tokens(full_text)

    def _merge_working_and_retrieved(
        self,
        results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """合并工作记忆与检索结果，工作记忆条目给默认高分。

        检测工作记忆内容是否已存在于检索结果中（按内容前缀匹配），
        若已存在则跳过工作记忆版本，避免重复。

        Returns:
            合并后的统一列表
        """
        import time as _time

        merged = list(results) if results else []

        # 收集检索结果中的内容前缀（用于去重）
        retrieved_prefixes = set()
        for mem in merged:
            content = mem.get("content", "")
            if content:
                retrieved_prefixes.add(content[:80])

        # 将工作记忆条目加入，给 score=1.0
        for item in self._working_memory:
            role = item.get("role", "user")
            content = item.get("content", "")

            # 跳过系统消息 / 空内容
            if role not in ("user", "assistant"):
                continue
            if not content:
                continue

            # 如果检索结果中已有相同内容，跳过
            prefix = content[:80]
            if prefix in retrieved_prefixes:
                continue

            merged.append({
                "id": item.get("id", ""),
                "content": content,
                "score": 1.0,
                "timestamp": _time.strftime("%Y-%m-%d %H:%M:%S"),
                "turn": "?",
                "role": role,
                "_is_working": True,
            })
            retrieved_prefixes.add(prefix)

        return merged

    # ------------------------------------------------------------------ #
    #  长期记忆
    # ------------------------------------------------------------------ #

    def _get_long_term_memories(self, top_k: int = None) -> List[Dict[str, Any]]:
        """获取长期记忆（已压缩的）。"""
        k = top_k or self._default_top_k
        all_mem = self._store.get_all()
        # 筛选已压缩的长期记忆
        long_mem = [m for m in all_mem if m.get("compressed", False)]
        # 按 last_score 降序
        long_mem.sort(
            key=lambda m: float(m.get("last_score") or m.get("importance", 0.5)),
            reverse=True,
        )
        return long_mem[:k]

    # ------------------------------------------------------------------ #
    #  Token 控制
    # ------------------------------------------------------------------ #

    def _truncate_by_tokens(self, text: str) -> str:
        """按 token 预算截断文本（粗略估算）。"""
        if not text:
            return text

        # 粗略估算：2 字符 ≈ 1 个中文 token
        estimated_tokens = len(text) // 2

        if estimated_tokens <= self._max_tokens:
            return text

        # 截断
        target_chars = self._max_tokens * 2
        truncated = text[:target_chars]

        # 尽量在换行处截断
        last_newline = truncated.rfind("\n")
        if last_newline > target_chars * 0.8:
            truncated = truncated[:last_newline]

        return truncated + "\n\n[记忆内容已截断，超出 token 预算]"

    def estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数。"""
        return len(text) // 2