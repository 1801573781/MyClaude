"""
MemoryInjector - 上下文格式化与注入

在每次 LLM 请求前，从长期和工作记忆中选择最相关的条目，生成注入的上下文文本。
"""

import logging
from typing import Dict, List

from src.memory.memory_compressor import MemoryCompressor

logger = logging.getLogger(__name__)


class MemoryInjector:
    """
    记忆上下文注入器。

    特性:
        - 格式化工作记忆和长期记忆为 Markdown 上下文字符串
        - 控制 token 总数不超过 max_tokens
        - 工作记忆优先，长期记忆按 importance * score 排序
    """

    def __init__(self, max_tokens: int = 2000):
        """
        参数:
            max_tokens: 注入上下文的最大 token 数。
        """
        self._max_tokens = max_tokens

    # ========== 公共接口 ==========

    def format_context(
        self,
        working_memories: List[Dict],
        long_term_results: List[Dict],
    ) -> str:
        """
        格式化注入上下文文本。

        参数:
            working_memories:   工作记忆列表。
            long_term_results:  长期记忆检索结果列表（已包含 "score" 字段）。

        返回:
            格式化的 Markdown 上下文字符串。
            若工作记忆为空且无相关长期记忆，返回空字符串 ""。
        """
        if not working_memories and not long_term_results:
            return ""

        lines = ["[记忆上下文 - 由 Memory 模块自动生成]", ""]

        # 1. 工作记忆段
        if working_memories:
            lines.append("[当前任务上下文]")
            for mem in working_memories:
                lines.append(f"- {mem['content']}")
            lines.append("")

        # 2. 长期记忆段
        if long_term_results:
            # 按 importance * score 降序排列
            sorted_long = sorted(
                long_term_results,
                key=lambda m: m.get("importance", 0.5) * m.get("score", 0),
                reverse=True,
            )
            lines.append("[相关历史记忆]")
            for mem in sorted_long:
                score = mem.get("score", 0.0)
                lines.append(f"- {mem['content']} (相关性: {score:.2f})")

        context_text = "\n".join(lines)

        # 3. Token 截断
        context_text = self._truncate_by_tokens(
            context_text,
            working_memories,
            long_term_results,
        )

        return context_text

    # ========== Token 截断 ==========

    def _truncate_by_tokens(
        self,
        full_text: str,
        working_memories: List[Dict],
        long_term_results: List[Dict],
    ) -> str:
        """
        按 token 预算截断上下文。

        规则:
            1. 先计算工作记忆段的 token 数。
            2. 若工作记忆段已超过 max_tokens * 0.6，截断工作记忆（丢弃末尾条目）。
            3. 长期记忆按 importance * score 降序依次加入，直到总 token 数超过 max_tokens。
        """
        total_estimated = self._estimate_tokens(full_text)

        if total_estimated <= self._max_tokens:
            return full_text

        working_budget = int(self._max_tokens * 0.6)

        working_lines = []
        if working_memories:
            working_lines.append("[当前任务上下文]")
            working_used = self._estimate_tokens("[当前任务上下文]")
            for mem in working_memories:
                line = f"- {mem['content']}"
                line_tokens = self._estimate_tokens(line)
                if working_used + line_tokens > working_budget:
                    logger.debug("工作记忆截断：超出 token 预算")
                    break
                working_lines.append(line)
                working_used += line_tokens

        # 剩余给长期记忆
        remaining_budget = self._max_tokens - working_used if working_lines else self._max_tokens
        # 预留分隔和头部
        header_text = "[记忆上下文 - 由 Memory 模块自动生成]\n\n"
        header_tokens = self._estimate_tokens(header_text)
        remaining_budget -= header_tokens

        long_lines = []
        if long_term_results and remaining_budget > 0:
            long_lines.append("[相关历史记忆]")
            long_used = self._estimate_tokens("[相关历史记忆]")
            # 按 importance * score 降序
            sorted_long = sorted(
                long_term_results,
                key=lambda m: m.get("importance", 0.5) * m.get("score", 0),
                reverse=True,
            )
            for mem in sorted_long:
                score = mem.get("score", 0.0)
                line = f"- {mem['content']} (相关性: {score:.2f})"
                line_tokens = self._estimate_tokens(line)
                if long_used + line_tokens > remaining_budget:
                    logger.debug("长期记忆截断：超出剩余 token 预算")
                    break
                long_lines.append(line)
                long_used += line_tokens

        # 构建最终文本
        result = [header_text.strip()]
        if working_lines:
            result.extend(working_lines)
        if long_lines:
            if working_lines:
                result.append("")  # 空行分隔
            result.extend(long_lines)

        return "\n".join(result)

    # ========== Token 估算 ==========

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """
        委托 MemoryCompressor.estimate_tokens。
        """
        return MemoryCompressor.estimate_tokens(text)