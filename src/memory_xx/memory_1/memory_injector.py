"""
memory_1 记忆注入器

将工作记忆与检索到的长期/短期记忆格式化为 LLM 可注入的 Markdown 上下文，
支持按 token 预算智能截断。
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 注入前缀（必须与 session_log.py 的 _classify_user() 识别一致）
_INJECTION_PREFIX = "[系统提醒] 以下是与当前任务相关的历史记忆，请参考："


class MemoryInjector:
    """记忆上下文格式化注入器。

    职责：
    - 将工作记忆+检索结果格式化为 Markdown 文本
    - 按 token 预算截断超长内容
    - 生成 session_log 兼容的日志格式
    """

    def __init__(self, max_tokens: int = 2000, approx_chars_per_token: float = 2.5):
        """
        Args:
            max_tokens: 记忆注入的最大 token 预算
            approx_chars_per_token: 中英文混合的每 token 字符数估算（中文 ≈1.5，英文 ≈4，混合取 2.5）
        """
        self._max_tokens = max_tokens
        self._chars_per_token = approx_chars_per_token
        self._max_chars = int(max_tokens * approx_chars_per_token)

    # ------------------------------------------------------------------ #
    #  格式化
    # ------------------------------------------------------------------ #

    def format_context(
        self,
        working_memory_items: List[Dict[str, Any]],
        retrieved_items: Optional[List[Dict[str, Any]]] = None,
        include_scores: bool = True,
    ) -> str:
        """格式化完整的记忆上下文。

        将工作记忆与检索结果合并为统一列表，按相关性降序排列。
        工作记忆条目视为最高相关性（score=1.0），避免两个区块重复展示。

        Args:
            working_memory_items: 工作记忆条目列表（本次会话的历史轮次）
            retrieved_items: 检索到的记忆条目列表（来自 search()）
            include_scores: 是否包含相关性得分

        Returns:
            格式化后的 Markdown 字符串，可直接注入 api_messages
        """
        # 合并去重
        merged = self._merge_working_and_retrieved(
            working_memory_items, retrieved_items or []
        )

        if not merged:
            return ""

        parts = [_INJECTION_PREFIX, "", self._format_unified_list(merged, include_scores)]
        full_text = "\n".join(parts)
        return self._truncate_by_tokens(full_text)

    def _merge_working_and_retrieved(
        self,
        working: List[Dict[str, Any]],
        retrieved: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """合并工作记忆与检索结果，工作记忆给 score=1.0，按内容去重。"""
        merged = list(retrieved)

        # 收集检索结果内容前缀用于去重
        retrieved_prefixes = set()
        for mem in merged:
            content = mem.get("content", "")
            if content:
                retrieved_prefixes.add(content[:80])

        for item in working[-10:]:
            content = item.get("content", "")
            if not content:
                continue

            prefix = content[:80]
            if prefix in retrieved_prefixes:
                continue

            merged.append({
                "id": "",
                "content": content,
                "score": 1.0,
                "timestamp": "",
                "turn": "?",
                "role": item.get("role", ""),
                "_is_working": True,
            })
            retrieved_prefixes.add(prefix)

        # 按 score 降序
        merged.sort(key=lambda x: x.get("score", 0), reverse=True)
        return merged

    def _format_unified_list(
        self,
        items: List[Dict[str, Any]],
        include_scores: bool = True,
    ) -> str:
        """格式化统一排名列表。"""
        lines = []
        for item in items:
            # 完整 UUID，不再截断
            mem_id = item.get("id", "")
            content = item.get("content", "")
            score = item.get("score")
            if not isinstance(score, (int, float)):
                score = 0.0

            if len(content) > 400:
                content = content[:400] + "..."

            wm_tag = " [工作记忆]" if item.get("_is_working") else ""
            id_part = f"id={mem_id} " if mem_id else ""

            parts = [content]
            if include_scores and score is not None:
                parts.append(f"(相关性: {score:.2f})")

            line = f"- [{id_part}]\n\n" + " ".join(parts) + wm_tag
            lines.append(line)

        return "[相关历史记忆]\n" + "\n".join(lines)

    def format_working_memory_only(self, working_memory_items: List[Dict[str, Any]]) -> str:
        """仅格式化工作记忆（get_working_memory() 使用）。"""
        return self.format_context(working_memory_items, retrieved_items=None)

    def format_search_results(
        self,
        query: str,
        results: List[Dict[str, Any]],
    ) -> str:
        """格式化检索结果（关键词触发搜索时使用）。

        Args:
            query: 用户搜索查询
            results: search() 返回的结果列表

        Returns:
            格式化后的文本
        """
        if not results:
            return f"[记忆搜索] 查询 '{query[:50]}' 未找到相关记忆。"

        parts = [
            _INJECTION_PREFIX,
            "",
            f"[记忆搜索: {query[:80]}]",
            self._format_retrieved_items(results, include_scores=True),
        ]
        full_text = "\n".join(parts)
        return self._truncate_by_tokens(full_text)

    # ------------------------------------------------------------------ #
    #  内部格式化
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_working_memory(items: List[Dict[str, Any]]) -> str:
        """格式化工作记忆条目列表。"""
        lines = []
        for i, item in enumerate(items[-10:], 1):  # 最多 10 条
            role_icon = {"user": "👤", "assistant": "🤖", "system": "⚙️"}.get(
                item.get("role", ""), "❓"
            )
            content = item.get("content", "")
            # 截断超长内容
            if len(content) > 300:
                content = content[:300] + "..."
            lines.append(f"- [Turn {i}] {role_icon} {content}")
        return "\n".join(lines)

    @staticmethod
    def _format_retrieved_items(
        items: List[Dict[str, Any]],
        include_scores: bool = True,
    ) -> str:
        """格式化检索记忆条目列表。"""
        lines = []
        for item in items:
            mem_id = item.get("id", "unknown")
            content = item.get("content", "")
            timestamp = item.get("timestamp", "")
            score = item.get("score")
            turn = item.get("turn")

            # 截断过长内容
            if len(content) > 500:
                content = content[:500] + "..."

            # 构造条目行
            parts = []
            if turn is not None:
                parts.append(f"[Turn {turn}]")
            if content:
                parts.append(content)
            if include_scores and score is not None:
                parts.append(f"(相关性: {score:.2f})")

            line = f"- [id={mem_id}]\n  " + " ".join(parts)
            lines.append(line)

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Token 截断
    # ------------------------------------------------------------------ #

    def _truncate_by_tokens(self, text: str) -> str:
        """按 token 预算截断文本。

        采用字符数估算（1 token ≈ chars_per_token 字符），
        超出部分从尾部截断。
        """
        if len(text) <= self._max_chars:
            return text

        # 保留前缀完整
        prefix = _INJECTION_PREFIX
        remaining_chars = self._max_chars - len(prefix) - 50  # 留出 "... 已截断" 的余量

        if remaining_chars <= 0:
            return prefix + "\n\n(记忆内容过多，已全部省略)"

        truncated_body = text[len(prefix):][:remaining_chars]
        return prefix + truncated_body + "\n\n... (以上记忆已截断，剩余部分因 token 预算不足已省略)"

    # ------------------------------------------------------------------ #
    #  日志格式（session_log 兼容）
    # ------------------------------------------------------------------ #

    @staticmethod
    def to_log_entry(context_text: str) -> Dict[str, str]:
        """将格式化后的记忆上下文转为 session_log 记录格式。

        Args:
            context_text: format_context() 的返回值

        Returns:
            {"role": "user", "content": context_text}（可直接传给 log_dict_info()）
        """
        # 如果没有实际内容，返回占位文本
        if not context_text or context_text == _INJECTION_PREFIX:
            return {
                "role": "user",
                "content": "没有召回到相关记忆",
            }
        return {
            "role": "user",
            "content": context_text,
        }

    def get_max_chars(self) -> int:
        """返回当前 token 预算对应的字符上限。"""
        return self._max_chars