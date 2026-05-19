"""
Memory1Adapter - 为 src/memory/MemoryManager 实现 MemoryBackend 接口

职责：将旧 MemoryManager 的调用方式适配到统一接口，不修改 src/memory/ 内部代码。
"""

from typing import Optional

from src.memory.memory_interface import MemoryBackend
from src.memory.memory_manager import MemoryManager
from src.utility.config_loader import global_cfg
from src.query import chat_llm

import logging

logger = logging.getLogger(__name__)


class Memory1Adapter(MemoryBackend):
    """适配旧 memory 模块（TF-IDF 检索）到统一接口。"""

    def __init__(self):
        mem_cfg_dict = {
            'enabled': True,
            'storage_path': getattr(global_cfg.memory, 'storage_path', '.memdir'),
            'similarity_threshold': getattr(global_cfg.memory, 'similarity_threshold', 0.15),
            'short_term_max_entries': getattr(global_cfg.memory, 'short_term_max_entries', 50),
            'short_term_max_tokens': getattr(global_cfg.memory, 'short_term_max_tokens', 8000),
            'compress_batch_size': getattr(global_cfg.memory, 'compress_batch_size', 20),
            'working_memory_max_tokens': getattr(global_cfg.memory, 'working_memory_max_tokens', 2000),
            'long_term_max_inject': getattr(global_cfg.memory, 'long_term_max_inject', 5),
            'forget_older_than_days': getattr(global_cfg.memory, 'forget_older_than_days', 30),
            'forget_importance_below': getattr(global_cfg.memory, 'forget_importance_below', 0.2),
        }
        self._mgr = MemoryManager(config={'memory': mem_cfg_dict})

        # 注入 LLM 回调（压缩用）
        def _llm_call(messages, max_tokens, temperature):
            result, _, _ = chat_llm.chat_with_retry(messages)
            return result

        self._mgr.set_llm_call(_llm_call)

    # ------------------------------------------------------------------
    #  实现 MemoryBackend 接口
    # ------------------------------------------------------------------

    def clear_working_memory(self) -> None:
        self._mgr.clear_working_memory()

    def inject_context(self, current_query: str) -> str:
        mem_context, _ = self._mgr.inject_context(current_query=current_query)
        # 统一包装 [系统提醒] 前缀（与 Memory2Adapter 对齐）
        if mem_context:
            return "[系统提醒] 以下是与当前任务相关的历史记忆，仅供参考！\n\n" + mem_context
        return ""

    def add_turn_memory(
        self,
        user_input: str,
        turn: int,
        reasoning_content: str,
        remaining_text: str,
        tools: list,
    ) -> None:
        """将一轮对话摘要存入工作记忆。逻辑抽取自 query_loop._on_llm_rsp()。"""
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

        content_parts = [f"[Turn {turn}]"]
        content_parts.append(f"用户输入: {user_summary}")
        if thinking_summary:
            content_parts.append(f"LLM推理过程: {thinking_summary}")
        if response_summary:
            content_parts.append(f"LLM应答: {response_summary}")
        content_parts.append(f"LLM工具调用: {tool_summary}")
        memory_content = "\n".join(content_parts)

        if len(memory_content) > 800:
            memory_content = memory_content[:797] + "..."

        metadata = {
            "turn": turn,
            "user_input": user_summary,
            "llm_reasoning": thinking_summary,
            "llm_response": response_summary,
            "llm_tool_call": tool_summary,
        }

        self._mgr.add_memory(
            content=memory_content,
            mem_type="working",
            importance=0.5,
            metadata=metadata,
        )

    def persist_and_maintain(self) -> None:
        count = self._mgr.persist_working_to_short()
        logger.info(f"已持久化 {count} 条工作记忆为短期记忆")

        compressed_count = self._mgr.compress_short_term()
        logger.info(f"本次压缩生成 {compressed_count} 条长期记忆")

        forgot_count = self._mgr.forget()
        logger.info(f"本次遗忘 {forgot_count} 条长期记忆")

    def clear_all_memories(self) -> int:
        return self._mgr.clear_all_memories()