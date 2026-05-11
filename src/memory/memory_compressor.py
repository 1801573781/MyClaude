import logging
import re
import math
from typing import List, Dict, Optional, Callable


logger = logging.getLogger(__name__)


class MemoryCompressor:
    """
    压缩短期记忆为长期记忆。
    当短期记忆超过阈值时，调用 LLM 将多条旧条目合并为一条总结性的长期记忆。
    """

    def __init__(self,
                 short_term_max_entries: int = 50,
                 short_term_max_tokens: int = 8000,
                 compress_batch_size: int = 20,
                 llm_call: Optional[Callable] = None):
        """
        参数:
            short_term_max_entries: 短期记忆条目数阈值。
            short_term_max_tokens:  短期记忆 token 总量阈值。
            compress_batch_size:    每批压缩的条目数。
            llm_call:               LLM 调用函数，签名为
                                    (messages, max_tokens, temperature) -> str | None。
        """
        self._max_entries = short_term_max_entries
        self._max_tokens = short_term_max_tokens
        self._batch_size = compress_batch_size
        self._llm_call = llm_call

    # ========== 公开接口 ==========

    def compress(self,
                 short_memories: List[Dict],
                 add_long_memory: Callable[[str, float, List[str], Dict], str],
                 delete_memory: Callable[[str], bool]) -> int:
        """
        对短期记忆执行压缩循环。

        参数:
            short_memories:  当前短期记忆列表（会原地修改）。
            add_long_memory: 创建长期记忆的函数，签名为
                             (content, importance, tags, metadata) -> id。
            delete_memory:   删除记忆的函数，签名为 (id) -> bool。

        返回:
            新生成的长期记忆数量。
        """
        new_long_count = 0
        last_round_produced = True

        while self._should_compress(short_memories):
            batch = self._select_compression_batch(short_memories)

            if len(batch) < 3:
                logger.debug(f"剩余待压缩条目 < 3，停止压缩")
                break

            prompt = self._build_compression_prompt(batch)
            summary = self._call_llm_for_summary(prompt)

            if summary and summary.strip():
                # 成功：创建长期记忆
                avg_importance = sum(m.get("importance", 0.5)
                                     for m in batch) / len(batch)
                add_long_memory(
                    content=summary.strip(),
                    importance=round(avg_importance, 2),
                    tags=["compressed"],
                    metadata={"original_count": len(batch)}
                )
                # 删除已压缩的短期记忆
                for m in batch:
                    delete_memory(m["id"])
                new_long_count += 1
                last_round_produced = True
                logger.info(f"压缩成功: {len(batch)} 条短期记忆 → 1 条长期记忆")

            elif summary is not None:
                # LLM 返回空：仍然删除 batch 避免死循环
                for m in batch:
                    delete_memory(m["id"])
                logger.warning("LLM 压缩返回空结果，已丢弃该批次")
                last_round_produced = True

            else:
                # LLM 调用异常：仅删除一半避免丢失重要信息
                delete_count = max(len(batch) // 2, 5)
                for m in batch[:delete_count]:
                    delete_memory(m["id"])
                logger.error(
                    f"LLM 压缩调用失败，已删除 {delete_count} 条短期记忆"
                )
                if not last_round_produced:
                    logger.warning("连续两轮压缩失败，终止循环")
                    break
                last_round_produced = False

        # 兜底：仍超标则强制删除最旧条目
        self._enforce_thresholds(short_memories, delete_memory)

        return new_long_count

    # ========== 内部方法 ==========

    def _should_compress(self, short_memories: List[Dict]) -> bool:
        """检查是否满足压缩触发条件。"""
        if not short_memories:
            return False

        if len(short_memories) > self._max_entries:
            return True

        total_text = " ".join(m.get("content", "") for m in short_memories)
        if self._estimate_tokens(total_text) > self._max_tokens:
            return True

        return False

    def _select_compression_batch(self,
                                  short_memories: List[Dict]) -> List[Dict]:
        """
        选择待压缩条目：按 (importance 升序, timestamp 升序) 排序，
        取前 batch_size 条。
        """
        sorted_mems = sorted(
            short_memories,
            key=lambda m: (m.get("importance", 0.5), m.get("timestamp", 0))
        )
        return sorted_mems[:self._batch_size]

    def _build_compression_prompt(self, batch: List[Dict]) -> str:
        """构造 LLM 压缩提示词。"""
        items = "\n".join(
            f"- {m.get('content', '')}" for m in batch
        )

        return (
            "你是一个记忆压缩助手。你的任务是将多条离散的短期记忆总结为一条简洁、"
            "信息密集的长期记忆。\n\n"
            "压缩规则：\n"
            "1. 保留所有关键事实、用户偏好、项目决策、技术约束。\n"
            "2. 丢弃冗余、重复、琐碎的细节。\n"
            "3. 使用简洁的陈述句，每条关键信息用分号或逗号分隔。\n"
            "4. 输出仅包含总结文本，不要加任何前缀、解释或 Markdown 标记。\n"
            "5. 总结控制在 100 ~ 500 字符以内。\n\n"
            f"待压缩的短期记忆：\n{items}\n\n"
            "请输出总结："
        )

    def _call_llm_for_summary(self, prompt: str) -> Optional[str]:
        """
        调用 LLM 获取压缩总结。

        返回:
            总结文本，失败返回 None，空结果返回 ""。
        """
        if self._llm_call is None:
            logger.warning("未注入 LLM 调用函数，压缩跳过")
            return None

        try:
            result = self._llm_call(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.3
            )
            return result
        except Exception as e:
            logger.error(f"LLM 压缩调用失败: {e}")
            return None

    def _enforce_thresholds(self,
                            short_memories: List[Dict],
                            delete_memory: Callable[[str], bool]) -> None:
        """
        兜底：超出阈值则强制删除最旧条目直到达标。
        目标为阈值的 80%。
        """
        target_entries = int(self._max_entries * 0.8)
        target_tokens = int(self._max_tokens * 0.8)

        # 按数目删除
        while len(short_memories) > target_entries:
            oldest = min(short_memories, key=lambda m: m.get("timestamp", 0))
            delete_memory(oldest["id"])
            short_memories.remove(oldest)

        # 按 token 删除
        while True:
            total_text = " ".join(
                m.get("content", "") for m in short_memories
            )
            if self._estimate_tokens(total_text) <= target_tokens:
                break
            if not short_memories:
                break
            oldest = min(short_memories, key=lambda m: m.get("timestamp", 0))
            delete_memory(oldest["id"])
            short_memories.remove(oldest)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """
        估算文本的 token 数。

        优先使用 tiktoken，否则使用粗略公式。
        """
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except (ImportError, Exception):
            # 粗略公式：英文 4 字符/token，中文 2 字符/token
            chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
            other_chars = len(text) - chinese_chars
            return other_chars // 4 + chinese_chars // 2