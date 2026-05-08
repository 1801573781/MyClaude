"""
MemoryCompressor - LLM 驱动的短期记忆压缩

当短期记忆超过阈值时，调用 LLM 将多条旧记忆合并/总结为更高层的长期记忆。
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MemoryCompressor:
    """
    短期记忆压缩器。

    特性:
        - 按 importance + timestamp 选择待压缩条目
        - 调用 LLM 生成总结（通过依赖注入，避免耦合 chat_llm）
        - 循环压缩直到低于阈值
        - 连续失败保护机制
    """

    def __init__(
        self,
        short_term_max_entries: int = 50,
        short_term_max_tokens: int = 8000,
        compress_batch_size: int = 20,
        llm_call: callable = None,
    ):
        """
        参数:
            short_term_max_entries: 短期记忆条目数阈值。
            short_term_max_tokens:  短期记忆 token 总量阈值。
            compress_batch_size:    每批压缩的条目数量。
            llm_call:               LLM 调用函数，签名:
                                    llm_call(model: str, messages: List[Dict],
                                             max_tokens: int, temperature: float)
                                    → Tuple[str, bool]  (content, is_truncated)
        """
        self._short_term_max_entries = short_term_max_entries
        self._short_term_max_tokens = short_term_max_tokens
        self._compress_batch_size = compress_batch_size
        self._llm_call = llm_call

    # ========== 公共接口 ==========

    def should_compress(self, short_term_memories: List[Dict]) -> bool:
        """
        判断是否需要触发压缩。

        任一条件满足即返回 True:
            - 条目数 > short_term_max_entries
            - token 总量 > short_term_max_tokens
        """
        if len(short_term_memories) > self._short_term_max_entries:
            return True
        total_tokens = self._estimate_total_tokens(short_term_memories)
        return total_tokens > self._short_term_max_tokens

    def compress(
        self,
        short_term_memories: List[Dict],
        get_all_short: callable,
        delete_memory: callable,
        add_long_memory: callable,
        model: str = "DeepSeek",
    ) -> int:
        """
        执行循环压缩，返回新生成的长期记忆数量。

        参数:
            short_term_memories: 当前短期记忆列表（会被原地修改）。
            get_all_short:       获取最新短期记忆列表的回调，签名: () → List[Dict]。
            delete_memory:       删除记忆的回调，签名: (memory_id: str) → bool。
            add_long_memory:     添加长期记忆的回调，签名:
                                 (content, importance, tags, metadata) → str。
            model:               压缩使用的 LLM 模型名。

        返回:
            本次压缩新生成的长期记忆数量。
        """
        new_long_count = 0
        last_round_produced = True

        while True:
            current = get_all_short()
            if not self.should_compress(current):
                break

            batch = self._select_batch(current)
            if len(batch) < 3:
                break  # 太少，不值得压缩

            summary = self._call_llm_for_summary(batch, model)

            if summary and summary.strip():
                # 成功：创建长期记忆
                avg_importance = sum(m.get("importance", 0.5) for m in batch) / len(batch)
                add_long_memory(
                    content=summary.strip(),
                    importance=round(avg_importance, 2),
                    tags=["compressed"],
                    metadata={"original_count": len(batch)},
                )
                # 删除已压缩的短期记忆
                for m in batch:
                    delete_memory(m["id"])
                new_long_count += 1
                last_round_produced = True
            elif summary is not None:
                # LLM 返回空：仍然删除 batch 避免死循环
                for m in batch:
                    delete_memory(m["id"])
                logger.warning("LLM 压缩返回空结果，已丢弃该批次")
                last_round_produced = True
            else:
                # LLM 调用异常：仅删除一半避免丢失重要信息
                remove_count = max(len(batch) // 2, 5)
                for m in batch[:remove_count]:
                    delete_memory(m["id"])
                logger.error("LLM 压缩调用失败，已删除部分条目")
                if not last_round_produced:
                    break
                last_round_produced = False

        # 兜底：仍超标则强制删除最旧条目
        self._enforce_thresholds(get_all_short, delete_memory)
        return new_long_count

    # ========== 批次选择 ==========

    def _select_batch(self, short_term_memories: List[Dict]) -> List[Dict]:
        """
        选择待压缩条目：按 (importance, timestamp) 升序排列，取前 compress_batch_size 条。
        """
        sorted_mems = sorted(
            short_term_memories,
            key=lambda m: (m.get("importance", 0.5), m.get("timestamp", 0))
        )
        return sorted_mems[: self._compress_batch_size]

    # ========== LLM 调用 ==========

    def _build_compression_prompt(self, batch: List[Dict]) -> str:
        """
        构造 LLM 压缩提示词。
        """
        lines = []
        lines.append("你是一个记忆压缩助手。你的任务是将多条离散的短期记忆总结为一条简洁、信息密集的长期记忆。")
        lines.append("")
        lines.append("压缩规则：")
        lines.append("1. 保留所有关键事实、用户偏好、项目决策、技术约束。")
        lines.append("2. 丢弃冗余、重复、琐碎的细节。")
        lines.append("3. 使用简洁的陈述句，每条关键信息用分号或逗号分隔。")
        lines.append("4. 输出仅包含总结文本，不要加任何前缀、解释或 Markdown 标记。")
        lines.append("5. 总结控制在 100 ~ 500 字符以内。")
        lines.append("")
        lines.append("待压缩的短期记忆：")
        for mem in batch:
            lines.append(f"- {mem['content']}")
        lines.append("")
        lines.append("请输出总结：")
        return "\n".join(lines)

    def _call_llm_for_summary(self, batch: List[Dict], model: str) -> Optional[str]:
        """
        调用 LLM 生成总结。

        返回:
            总结文本字符串，成功返回空字符串时为 ""（非 None），异常返回 None。
        """
        if self._llm_call is None:
            logger.error("LLM 调用函数未注入，无法执行压缩")
            return None

        prompt = self._build_compression_prompt(batch)
        messages = [{"role": "user", "content": prompt}]

        try:
            content, is_truncated = self._llm_call(
                model=model,
                messages=messages,
                max_tokens=512,
                temperature=0.3,
            )
            return content or ""
        except Exception as e:
            logger.error(f"LLM 压缩调用失败: {e}")
            return None

    # ========== 阈值强制 ==========

    def _enforce_thresholds(
        self,
        get_all_short: callable,
        delete_memory: callable,
    ) -> None:
        """
        兜底：若短期记忆仍超标，强制删除最旧条目直到低于阈值的 80%。
        """
        target_entries = int(self._short_term_max_entries * 0.8)
        target_tokens = int(self._short_term_max_tokens * 0.8)

        # 按条目数
        while True:
            current = get_all_short()
            if len(current) <= target_entries:
                break
            oldest = min(current, key=lambda m: m.get("timestamp", 0))
            delete_memory(oldest["id"])

        # 按 token
        while True:
            current = get_all_short()
            if self._estimate_total_tokens(current) <= target_tokens:
                break
            oldest = min(current, key=lambda m: m.get("timestamp", 0))
            delete_memory(oldest["id"])

    # ========== Token 估算 ==========

    @staticmethod
    def _estimate_total_tokens(memories: List[Dict]) -> int:
        """
        估算记忆列表的总 token 数。
        """
        total = 0
        for mem in memories:
            total += MemoryCompressor.estimate_tokens(mem.get("content", ""))
        return total

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """
        粗略估算文本的 token 数。

        公式: len(text) // 4 + 中文字符数 // 2

        若 tiktoken 可用，优先使用 cl100k_base 编码。
        """
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError:
            pass

        import re
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        return len(text) // 4 + chinese_chars // 2