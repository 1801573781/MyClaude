"""
memory_1 LLM 压缩器

当短期记忆超过压缩阈值时，调用 LLM 将多条短期记忆合并压缩为一条长期记忆摘要。
压缩后的摘要保留关键信息（主题、决策、结论），丢弃冗余对话细节。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 压缩系统提示词
_COMPRESSION_SYSTEM_PROMPT = """你是一个记忆压缩助手。你需要将多段对话记忆压缩为一条简洁的摘要。

要求：
1. 保留核心信息：任务目标、关键决策、重要结论、涉及的文件/函数/类名
2. 丢弃冗余细节：寒暄、重复陈述、中间错误尝试（除非最终方案依赖该错误经验）
3. 输出纯文本摘要，不超过 500 字符
4. 以第三人称视角描述（如"用户询问了...，助手建议..."）

输入格式：多条记忆以"---"分隔，每条含角色标签和内容。
输出格式：仅输出摘要文本，不要加任何前缀或标记。"""


class MemoryCompressor:
    """LLM 记忆压缩器。

    职责：
    - 检测短期记忆是否超量
    - 调用 LLM 将多条短期记忆压缩为长期摘要
    - 标记原始记忆为 compressed
    """

    def __init__(
        self,
        enabled: bool = True,
        model: str = "default",
        max_tokens_per_batch: int = 4000,
        llm_chat_fn: Optional[Callable] = None,
    ):
        """
        Args:
            enabled: 是否启用压缩
            model: LLM 模型名（"default" 表示使用全局配置）
            max_tokens_per_batch: 单批最大输入 token 数
            llm_chat_fn: LLM 对话函数，签名 (messages: list[dict], max_tokens: int) -> str
        """
        self._enabled = enabled
        self._model = model
        self._max_tokens_per_batch = max_tokens_per_batch
        self._llm_chat_fn = llm_chat_fn

    def set_llm_chat_fn(self, chat_fn: Callable) -> None:
        """注入 LLM 对话函数。"""
        self._llm_chat_fn = chat_fn

    def should_compress(self, short_term_count: int, short_term_max: int,
                        compression_threshold: float = 0.8) -> bool:
        """判断是否需要触发压缩。

        Args:
            short_term_count: 当前短期记忆条数
            short_term_max: 短期记忆最大容量
            compression_threshold: 压缩触发阈值（占用率）

        Returns:
            是否需要压缩
        """
        if not self._enabled:
            return False
        if short_term_max <= 0:
            return False
        occupancy = short_term_count / short_term_max
        return occupancy >= compression_threshold

    def compress(
        self,
        items: List[Dict[str, Any]],
        target_count: int,
    ) -> Optional[str]:
        """压缩多条记忆为一条摘要。

        Args:
            items: 待压缩的记忆条目列表（至少 2 条）
            target_count: 压缩后期望保留的条数（决定压缩激进程度）

        Returns:
            压缩摘要文本，失败返回 None
        """
        if not self._enabled:
            return None
        if not items or len(items) < 2:
            return None
        if self._llm_chat_fn is None:
            logger.warning("MemoryCompressor: LLM 函数未注入，跳过压缩")
            return None

        # 按时间排序，压缩最旧的
        items = sorted(items, key=lambda x: x.get("timestamp", ""))
        compress_count = len(items) - target_count + 1
        if compress_count < 2:
            return None
        to_compress = items[:compress_count]

        # 构造 Prompt
        memory_texts = []
        for item in to_compress:
            role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(
                item.get("role", ""), item.get("role", "未知")
            )
            content = item.get("content", "")[:500]  # 截断长文本
            memory_texts.append(f"[{role_label}] {content}")

        combined_text = "\n---\n".join(memory_texts)

        # 估算 token 数（粗略：1 字符 ≈ 0.5 token），超长时截断
        estimated_tokens = len(combined_text) * 0.5
        if estimated_tokens > self._max_tokens_per_batch:
            # 缩减条目数
            while estimated_tokens > self._max_tokens_per_batch and len(memory_texts) > 1:
                memory_texts = memory_texts[: len(memory_texts) // 2]
                combined_text = "\n---\n".join(memory_texts)
                estimated_tokens = len(combined_text) * 0.5

        messages = [
            {"role": "system", "content": _COMPRESSION_SYSTEM_PROMPT},
            {"role": "user", "content": f"请将以下 {len(memory_texts)} 条对话记忆压缩为一条摘要：\n\n{combined_text}"},
        ]

        try:
            summary = self._llm_chat_fn(messages, max_tokens=800)
            if summary:
                logger.info(f"MemoryCompressor: 压缩 {len(to_compress)} 条记忆为 {len(summary)} 字符摘要")
                return summary.strip()
            return None
        except Exception as e:
            logger.error(f"MemoryCompressor.compress: LLM 调用失败: {e}")
            return None

    def mark_compressed(self, store: Any, item_ids: List[str]) -> int:
        """将原始记忆标记为 compressed。

        Args:
            store: MemoryStore 实例
            item_ids: 已压缩的记忆 ID 列表

        Returns:
            成功标记的数量
        """
        count = 0
        for mid in item_ids:
            if store.update(mid, compressed=True):
                count += 1
        return count