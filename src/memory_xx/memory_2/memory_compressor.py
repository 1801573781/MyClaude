"""
memory_2 LLM 记忆压缩器

将多条短期记忆通过 LLM 合并压缩为一条长期记忆摘要。
压缩后原始短期记忆标记为 compressed。

核心理念：调用对话 LLM，将多条短期记忆总结为精炼摘要，
保留关键信息（主题、决策、结论），丢弃冗余对话细节。
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryCompressor:
    """LLM 记忆压缩器。

    将多条短期记忆合并压缩为一条长期记忆摘要。

    所需回调：
        llm_chat(api_messages, max_tokens, temperature) -> str
    """

    def __init__(
        self,
        memory_store,                   # MemoryStore 实例
        config: Dict[str, Any],         # memory_2 配置
        llm_chat: Callable,             # LLM 对话回调
    ):
        """
        Args:
            memory_store: MemoryStore 持久化存储实例
            config: memory_2 配置字典
            llm_chat: LLM 对话函数
        """
        self._store = memory_store
        self._llm_chat = llm_chat

        comp_cfg = config.get("compressor", {})
        self._enabled = comp_cfg.get("enabled", True)
        self._max_tokens_per_batch = comp_cfg.get("max_tokens_per_batch", 4000)

    # ------------------------------------------------------------------ #
    #  压缩主入口
    # ------------------------------------------------------------------ #

    def compress(self, short_memories: List[Dict[str, Any]]) -> int:
        """压缩多条短期记忆为长期摘要。

        Args:
            short_memories: 待压缩的短期记忆列表

        Returns:
            压缩生成的长期记忆条数
        """
        if not self._enabled:
            logger.info("MemoryCompressor: 压缩器已禁用")
            return 0

        if not short_memories:
            logger.info("MemoryCompressor: 无待压缩记忆")
            return 0

        # 分批处理（每批最多 max_tokens_per_batch 对应的记忆数）
        batches = self._split_into_batches(short_memories)

        compressed_count = 0
        for batch in batches:
            try:
                summary = self._compress_batch(batch)
                if summary:
                    self._save_summary(summary, batch)
                    compressed_count += 1
            except Exception as e:
                logger.error(f"MemoryCompressor.compress: 压缩失败 ({e})")

        logger.info(f"MemoryCompressor: 已压缩 {compressed_count} 批，生成 {compressed_count} 条长期记忆")
        return compressed_count

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
        # 构造记忆文本
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

摘要格式：简洁段落，100-300 字，中文优先。"""

    # ------------------------------------------------------------------ #
    #  保存摘要
    # ------------------------------------------------------------------ #

    def _save_summary(
        self,
        summary: str,
        source_batch: List[Dict[str, Any]],
    ) -> None:
        """将压缩摘要保存为长期记忆。"""
        # 提取来源记忆的相关信息
        source_ids = [m.get("id", "") for m in source_batch]
        source_roles = list(set(m.get("role", "") for m in source_batch))
        source_tags = list(set(
            tag for m in source_batch
            for tag in (m.get("tags") or [])
        ))

        # 取最高重要性作为摘要的重要性
        max_importance = max(
            (float(m.get("importance", 0.5)) for m in source_batch),
            default=0.5,
        )

        # 取最早时间戳作为摘要的时间
        timestamps = [m.get("timestamp", "") for m in source_batch]
        earliest_ts = min(ts for ts in timestamps if ts) if timestamps else datetime.now().isoformat()

        metadata = {
            "importance": max_importance,
            "tags": source_tags[:5],  # 最多保留 5 个标签
            "turn": None,
            "compressed": True,
        }

        # 增强摘要内容：附加压缩元信息
        enhanced_content = (
            f"[压缩摘要 - {len(source_batch)} 条记忆]\n"
            f"涉及角色: {', '.join(source_roles) if source_roles else 'N/A'}\n"
            f"时间范围: {earliest_ts}\n\n"
            f"{summary}"
        )

        mem_id = self._store.add(
            role="system",
            content=enhanced_content,
            metadata=metadata,
        )

        logger.info(
            f"MemoryCompressor._save_summary: 已生成长期记忆 id={mem_id}，"
            f"来源 {len(source_ids)} 条短期记忆"
        )