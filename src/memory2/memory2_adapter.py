"""
Memory2Adapter - 为 src/memory2/MemoryManager 实现 MemoryBackend 接口

职责：将新 MemoryManager（Embedding + LLM 双路召回）的调用方式适配到统一接口，
不修改 src/memory2/ 内部代码。
"""

from typing import Optional

from src.memory.memory_interface import MemoryBackend
from src.memory2.memory_store import MemoryStore
from src.memory2.embedding_retriever import EmbeddingRetriever, EmbeddingGenerator
from src.memory2.memory_manager import (
    MemoryManager as MemoryManagerV2,
    MemoryConfig,
)

from src.utility.config_loader import global_cfg
from src.query import chat_llm

import logging

logger = logging.getLogger(__name__)


class Memory2Adapter(MemoryBackend):
    """适配新 memory 模块（Embedding + LLM 双路召回）到统一接口。"""

    def __init__(self):
        mem = global_cfg.memory
        storage = mem.storage_path

        cfg = MemoryConfig(
            long_term_path=f"{storage}/memory2_long_term.json",
            short_term_path=f"{storage}/memory2_short_term.json",
            max_short_term=getattr(mem, 'short_term_max_entries', 50),
            max_long_term=getattr(mem, 'long_term_max_entries', 200),
            max_working_memories=getattr(mem, 'working_memory_max_entries', 20),
            embedding_top_k=getattr(mem, 'embedding_top_k', 10),
            llm_top_k=getattr(mem, 'llm_top_k', 5),
            similarity_threshold=getattr(mem, 'similarity_threshold', 0.15),
            context_token_budget=getattr(mem, 'context_token_budget', 1500),
            chars_per_token=getattr(mem, 'chars_per_token', 2.0),
            forgetting_slope=getattr(mem, 'forgetting_slope', 0.5),
            forgetting_min=getattr(mem, 'forgetting_min', 0.01),
            decay_check_interval=getattr(mem, 'decay_check_interval', 3600),
            compress_trigger_count=getattr(mem, 'compress_trigger_count', 12),
            max_compressed_per_batch=getattr(mem, 'max_compressed_per_batch', 6),
            inject_header="[系统提醒] 以下是与当前任务相关的历史记忆，仅供参考！\n",
        )

        # LLM 对话函数
        def _llm_chat_fn(messages):
            result, _, _ = chat_llm.chat_with_retry(messages)
            return result

        # Embedding 生成器（如果配置了 API key）
        embedding_gen = None
        try:
            from src.utility.config_loader import global_cfg as gcfg
            api_key = getattr(gcfg.model, 'api_key', None)
            # MiniMax embedding 模型
            if api_key:
                # 使用 MiniMax emb 模型
                embedding_gen = EmbeddingGenerator(
                    api_key=api_key,
                    model="embo-01",
                )
        except Exception as e:
            logger.warning(f"Embedding 生成器初始化失败: {e}")

        self._mgr = MemoryManagerV2(
            config=cfg,
            llm_chat_fn=_llm_chat_fn,
            embedding_generator=embedding_gen,
        )

    # ------------------------------------------------------------------
    #  实现 MemoryBackend 接口
    # ------------------------------------------------------------------

    def clear_working_memory(self) -> None:
        """清空工作记忆。MemoryManagerV2 无此方法，直接操作内部列表。"""
        self._mgr._working_memories.clear()

    def inject_context(self, current_query: str) -> str:
        """召回相关记忆并格式化为上下文。"""
        return self._mgr.inject_context(query=current_query)

    def add_turn_memory(
        self,
        user_input: str,
        turn: int,
        reasoning_content: str,
        remaining_text: str,
        tools: list,
    ) -> None:
        """
        记录一轮对话。
        MemoryManagerV2 的 record_turn 方法签名不同，此处做摘要适配。
        """
        user_summary = user_input[:100] if user_input else ""

        thinking_summary = ""
        if reasoning_content:
            thinking_summary = reasoning_content[:250].strip()
            if len(reasoning_content) > 250:
                thinking_summary += "..."

        response_summary = ""
        if remaining_text:
            response_summary = remaining_text[:250].strip()
            if len(remaining_text) > 250:
                response_summary += "..."

        tool_details = []
        for t in tools:
            name = t.get("llm_tool", "")
            params = t.get("params", {})
            path = params.get("path", "")
            summ = params.get("summary", "")
            if name in ("create", "str_replace") and path:
                detail = f"{name}({path}"
                if summ:
                    detail += summ
                detail += ")"
            elif name == "file_view" and path:
                detail = f"file_view({path})"
            elif name == "bash":
                detail = "bash"
            elif name == "done":
                detail = "done"
            else:
                detail = name
            tool_details.append(detail)
        tool_summary = "; ".join(tool_details) if tool_details else "无工具调用"

        self._mgr.record_turn(
            user_input=user_input,
            llm_response=response_summary,
            llm_reasoning=thinking_summary,
            tool_calls=tool_summary,
            importance=0.5,
        )

    def persist_and_maintain(self) -> None:
        """会话结束时压缩并持久化。"""
        self._mgr.on_session_end()

    def clear_all_memories(self) -> int:
        """清空所有记忆并返回删除总数。"""
        l_count = self._mgr._long_store.count()
        s_count = self._mgr._short_store.count()
        w_count = len(self._mgr._working_memories)
        self._mgr.clear_all()
        return l_count + s_count + w_count