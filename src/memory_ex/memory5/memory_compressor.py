"""memory5 独立压缩器 —— 短期记忆超标时调用 LLM 批量压缩为长期记忆

从 src/memory/memory_compressor.py 重新实现，保持行为语义一致。
"""

import logging
import re
from typing import List, Dict, Callable, Optional

logger = logging.getLogger(__name__)


class Memory5Compressor:
    """短期记忆批量压缩器，依赖 LLM 生成总结。"""

    def __init__(self,
                 short_term_max_entries: int = 50,
                 short_term_max_tokens: int = 8000,
                 compress_batch_size: int = 20,
                 llm_call: Optional[Callable] = None):
        self._max_entries = short_term_max_entries
        self._max_tokens = short_term_max_tokens
        self._batch_size = compress_batch_size
        self._llm_call = llm_call

    def compress(self,
                 short_memories: List[Dict],
                 add_long_memory: Callable[[str, float, List[str], Dict], str],
                 delete_memory: Callable[[str], bool]) -> int:
        new_long_count = 0
        last_round_produced = True

        while self._should_compress(short_memories):
            batch = self._select_batch(short_memories)
            if len(batch) < 3:
                break

            prompt = self._build_prompt(batch)
            summary = self._call_llm_for_summary(prompt)

            if summary and summary.strip():
                avg_importance = sum(m.get("importance", 0.5) for m in batch) / len(batch)
                add_long_memory(
                    content=summary.strip(),
                    importance=round(avg_importance, 2),
                    tags=["compressed"],
                    metadata={"original_count": len(batch)}
                )
                for m in batch:
                    delete_memory(m["id"])
                new_long_count += 1
                last_round_produced = True
                logger.info(f"Memory5Compressor 压缩成功: {len(batch)} → 1 条长期")
            elif summary is not None:
                for m in batch:
                    delete_memory(m["id"])
                logger.warning("Memory5Compressor LLM 返回空，已丢弃批次")
                last_round_produced = True
            else:
                delete_count = max(len(batch) // 2, 5)
                for m in batch[:delete_count]:
                    delete_memory(m["id"])
                logger.error(f"Memory5Compressor LLM 调用失败，删除 {delete_count} 条")
                if not last_round_produced:
                    logger.warning("Memory5Compressor 连续两轮失败，终止")
                    break
                last_round_produced = False

        self._enforce_thresholds(short_memories, delete_memory)
        return new_long_count

    def _should_compress(self, short_memories: List[Dict]) -> bool:
        if not short_memories:
            return False
        if len(short_memories) > self._max_entries:
            return True
        total_text = " ".join(m.get("content", "") for m in short_memories)
        if self._estimate_tokens(total_text) > self._max_tokens:
            return True
        return False

    def _select_batch(self, short_memories: List[Dict]) -> List[Dict]:
        sorted_mems = sorted(
            short_memories,
            key=lambda m: (m.get("importance", 0.5), m.get("timestamp", 0))
        )
        return sorted_mems[:self._batch_size]

    def _build_prompt(self, batch: List[Dict]) -> str:
        items = "\n".join(f"- {m.get('content', '')}" for m in batch)
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
        if self._llm_call is None:
            logger.warning("Memory5Compressor 未注入 LLM 回调")
            return None
        try:
            result = self._llm_call(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.3
            )
            return result
        except Exception as e:
            logger.error(f"Memory5Compressor LLM 调用失败: {e}")
            return None

    def _enforce_thresholds(self,
                            short_memories: List[Dict],
                            delete_memory: Callable[[str], bool]) -> None:
        target_entries = int(self._max_entries * 0.8)
        target_tokens = int(self._max_tokens * 0.8)

        while len(short_memories) > target_entries:
            oldest = min(short_memories, key=lambda m: m.get("timestamp", 0))
            delete_memory(oldest["id"])
            short_memories.remove(oldest)

        while True:
            total_text = " ".join(m.get("content", "") for m in short_memories)
            if self._estimate_tokens(total_text) <= target_tokens:
                break
            if not short_memories:
                break
            oldest = min(short_memories, key=lambda m: m.get("timestamp", 0))
            delete_memory(oldest["id"])
            short_memories.remove(oldest)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except (ImportError, Exception):
            chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
            other_chars = len(text) - chinese_chars
            return other_chars // 4 + chinese_chars // 2