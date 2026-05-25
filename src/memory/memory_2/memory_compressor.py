"""
memory_2 LLM 记忆压缩器

将多条短期记忆通过 LLM 合并压缩为一条长期记忆摘要。
压缩后原始短期记忆标记为 compressed。

核心理念：调用对话 LLM，将多条短期记忆总结为精炼摘要，
保留关键信息（主题、决策、结论），丢弃冗余对话细节。

接口与 memory_1.MemoryCompressor 对齐：
- __init__(enabled, model, max_tokens_per_batch, llm_chat_fn)
- set_llm_chat_fn(fn)  允许外部注入/更新 LLM 函数
- should_compress(count, max_items, threshold) -> bool  判断是否需要压缩
- compress(items, target_count) -> Optional[str]  返回摘要字符串
- mark_compressed(store, item_ids) -> int  标记原始记忆为已压缩
"""

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryCompressor:
    """LLM 记忆压缩器（与 memory_1 对等接口）。

    将多条短期记忆合并压缩为一条长期记忆摘要。

    所需回调：
        llm_chat_fn(api_messages, max_tokens, temperature) -> str
    """

    def __init__(
        self,
        enabled: bool = True,
        model: str = "",
        max_tokens_per_batch: int = 4000,
        llm_chat_fn: Optional[Callable] = None,
    ):
        """
        Args:
            enabled: 是否启用 LLM 压缩
            model: LLM 模型名（用于日志）
            max_tokens_per_batch: 每批最大 token 估算值
            llm_chat_fn: LLM 对话回调 (api_messages, max_tokens, temperature) -> str
        """
        self._enabled = enabled
        self._model = model
        self._max_tokens_per_batch = max_tokens_per_batch
        self._llm_chat = llm_chat_fn

    # ------------------------------------------------------------------ #
    #  接口方法
    # ------------------------------------------------------------------ #

    def set_llm_chat_fn(self, fn: Callable) -> None:
        """外部注入/更新 LLM 对话函数。"""
        self._llm_chat = fn

    @property
    def enabled(self) -> bool:
        """返回压缩器是否启用。"""
        return self._enabled

    def should_compress(self, count: int, max_items: int, threshold: float) -> bool:
        """判断是否需要执行压缩。

        Args:
            count: 当前待压缩条目数
            max_items: 最大允许条目数
            threshold: 触发压缩的比例阈值（如 0.9 表示达到 90% 时触发）

        Returns:
            是否需要压缩
        """
        if not self._enabled:
            return False
        if count <= 0:
            return False
        return count >= int(max_items * threshold)

    def compress(
        self,
        items: List[Dict[str, Any]],
        target_count: int,
    ) -> Optional[str]:
        """压缩多条记忆为一条摘要字符串。

        Args:
            items: 待压缩的记忆列表（每个元素含 id/content/role/timestamp 等）
            target_count: 目标保留条数（供 LLM 参考）

        Returns:
            压缩后的摘要文本；若 LLM 调用失败或压缩器禁用则返回 None
        """
        if not self._enabled:
            logger.info("MemoryCompressor: 压缩器已禁用")
            return None

        if not items:
            logger.info("MemoryCompressor: 无待压缩记忆")
            return None

        if not self._llm_chat:
            logger.warning("MemoryCompressor.compress: llm_chat_fn 未设置，无法压缩")
            return None

        try:
            # 分批处理
            batches = self._split_into_batches(items)
            summaries = []
            for batch in batches:
                s = self._compress_batch(batch)
                if s:
                    summaries.append(s)

            if not summaries:
                return None

            # 如果只有一批，直接返回
            if len(summaries) == 1:
                return summaries[0]

            # 多批时再合并压缩一次
            merged = self._merge_summaries(summaries, target_count)
            return merged if merged else "\n".join(summaries)

        except Exception as e:
            logger.error(f"MemoryCompressor.compress: 压缩失败 ({e})")
            return None

    def mark_compressed(self, store, item_ids: List[str]) -> int:
        """将已压缩的原始记忆标记为 compressed=True。

        Args:
            store: MemoryStore 实例（需支持 update 方法）
            item_ids: 要标记的记忆 ID 列表

        Returns:
            成功标记的条数
        """
        marked = 0
        for mem_id in item_ids:
            if store.update(mem_id, compressed=True):
                marked += 1
        logger.info(f"MemoryCompressor.mark_compressed: 标记 {marked}/{len(item_ids)} 条")
        return marked

    # ------------------------------------------------------------------ #
    #  分批
    # ------------------------------------------------------------------ #

    def _split_into_batches(
        self,
        memories: List[Dict[str, Any]],
    ) -> List[List[Dict[str, Any]]]:
        """按 token 估算将记忆分批。"""
        batches = []
        current_batch = []
        current_estimated_tokens = 0

        for mem in memories:
            content = mem.get("content", "")
            # 粗略估算：1 个中文字符 ≈ 1 token，1 个英文单词 ≈ 0.75 token
            estimated = len(content) // 2  # 保守估计

            if current_estimated_tokens + estimated > self._max_tokens_per_batch and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_estimated_tokens = 0

            current_batch.append(mem)
            current_estimated_tokens += estimated

        if current_batch:
            batches.append(current_batch)

        return batches

    # ------------------------------------------------------------------ #
    #  单批压缩
    # ------------------------------------------------------------------ #

    def _compress_batch(self, batch: List[Dict[str, Any]]) -> Optional[str]:
        """调用 LLM 压缩一批记忆为摘要文本。"""
        memory_text = self._format_batch_for_compression(batch)

        system_prompt = self._compression_system_prompt()
        user_prompt = (
            f"请将以下 {len(batch)} 条对话记忆压缩为一条精炼摘要。\n\n"
            f"{memory_text}\n\n"
            f"请输出压缩后的摘要文本（纯文本，不要 JSON，不要 markdown 标记）。"
        )

        api_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self._llm_chat(api_messages, 2048, 0.3)
        if not response or not response.strip():
            logger.warning("MemoryCompressor._compress_batch: LLM 返回空响应")
            return None

        # 清理可能的 markdown 标记
        summary = response.strip()
        if summary.startswith("```"):
            lines = summary.split("\n")
            summary = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        return summary

    # ------------------------------------------------------------------ #
    #  多批摘要合并
    # ------------------------------------------------------------------ #

    def _merge_summaries(
        self,
        summaries: List[str],
        target_count: int,
    ) -> Optional[str]:
        """将多个批次的摘要再合并压缩为一条。"""
        if not self._llm_chat:
            return None

        combined = "\n\n---\n\n".join(
            f"批次 {i + 1}: {s}" for i, s in enumerate(summaries)
        )

        system_prompt = self._compression_system_prompt()
        user_prompt = (
            f"以下是从不同批次记忆生成的 {len(summaries)} 段摘要，"
            f"请将它们合并为一条统一的摘要（目标保留 {target_count} 条记忆的核心信息）：\n\n"
            f"{combined}\n\n"
            f"请输出合并后的摘要文本（纯文本，不要 JSON，不要 markdown 标记）。"
        )

        api_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self._llm_chat(api_messages, 2048, 0.3)
        if not response or not response.strip():
            logger.warning("MemoryCompressor._merge_summaries: LLM 返回空响应")
            return None

        return response.strip()

    # ------------------------------------------------------------------ #
    #  格式化 & 提示词
    # ------------------------------------------------------------------ #

    def _format_batch_for_compression(self, batch: List[Dict[str, Any]]) -> str:
        """格式化记忆为压缩候选文本。"""
        lines = []
        for idx, mem in enumerate(batch):
            role = mem.get("role", "unknown")
            timestamp = mem.get("timestamp", "")
            content = mem.get("content", "")
            importance = mem.get("importance", 0.5)

            if len(content) > 1000:
                content = content[:1000] + "..."

            line = (
                f"### 记忆 {idx + 1}\n"
                f"- 时间: {timestamp}\n"
                f"- 角色: {role}\n"
                f"- 重要性: {importance:.2f}\n"
                f"- 内容: {content}\n"
            )
            lines.append(line)

        return "\n".join(lines)

    def _compression_system_prompt(self) -> str:
        """压缩用系统提示词。"""
        return """你是一个对话记忆压缩器。你的任务是将多条相关对话记忆压缩为一条精炼摘要。

压缩规则：
1. **保留关键信息**：任务目标、重要决策、代码修改内容、发现的问题、解决方案
2. **保留实体引用**：文件名、函数名、类名、路径、配置项名称
3. **丢弃冗余细节**：问候语、确认重复、中间纠错过程、情绪表达
4. **合并相似信息**：多条记忆讨论同一主题时合并描述
5. **保持客观**：不添加推断、评价、建议
6. **代码记忆特殊处理**：若记忆涉及代码修改，保留修改前后的关键差异；若涉及错误修复，保留错误现象和修复方法

摘要格式：简洁段落，100-300 字，中文优先。"""