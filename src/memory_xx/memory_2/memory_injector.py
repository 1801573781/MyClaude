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

    def add(self, role: str, content: str) -> None:
        """添加一条到工作记忆。"""
        self._working_memory.append({"role": role, "content": content})

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
                role = item.get("role", "unknown")
                content = item.get("content", "")
                parts.append(f"\n[{role}]: {content}")
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

        Args:
            query: 原始查询
            results: search() 返回的记忆列表

        Returns:
            格式化的 Markdown 文本
        """
        if not results:
            return "没有召唤到相关记忆"

        parts = [f"{self.MEMORY_PREFIX}\n"]

        # 工作记忆头部
        if self._working_memory:
            parts.append(self.WORKING_HEADER)
            # 只显示最近 2 轮的摘要
            recent = self._working_memory[-4:]
            for item in recent:
                role = item.get("role", "unknown")
                content = item.get("content", "")
                if len(content) > 200:
                    content = content[:200] + "..."
                parts.append(f"[{role}]: {content}")
            parts.append("")

        # 搜索结果
        parts.append(f"[检索结果 - 查询: {query}]")
        for i, mem in enumerate(results):
            mem_id = mem.get("id", "")[:8]
            content = mem.get("content", "")
            score = mem.get("score", 0)
            if not isinstance(score, (int, float)):
                score = 0.0
            timestamp = mem.get("timestamp", "")[:19]

            if len(content) > 400:
                content = content[:400] + "..."

            parts.append(
                f"- [id={mem_id}] [Turn {mem.get('turn', '?')}] "
                f"[{timestamp}] {content} (相关性: {score:.2f})"
            )

        full_text = "\n".join(parts)
        return self._truncate_by_tokens(full_text)

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