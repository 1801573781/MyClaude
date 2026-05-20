"""Memory6Adapter —— 将 Memory6 适配到 query_loop 所需的 MemoryBackend 接口

用法：替代 Memory5Adapter，让 query_loop 通过统一的 MemoryBackend
接口使用 memory6 的三层记忆（Embedding + FAISS + LLM 双路召回）。
"""

import logging
from typing import Optional

from src.memory.memory_interface import MemoryBackend
from src.memory_ex.memory6.memory6 import Memory6
from src.utility.config_loader import global_cfg

logger = logging.getLogger(__name__)


class Memory6Adapter(MemoryBackend):
    """适配 Memory6（Embedding + FAISS + LLM）到 query_loop 的统一接口。"""

    def __init__(self, config: Optional[dict] = None):
        # 1. 合并 memory 配置：先取传入 config，再补 global_cfg 通用字段
        merged_cfg = dict(config) if config else {}

        if "storage_dir" not in merged_cfg:
            merged_cfg["storage_dir"] = getattr(
                global_cfg.memory, "storage_path", ".memdir"
            )

        for key in ("working_max", "short_max", "long_max"):
            if key not in merged_cfg:
                val = getattr(global_cfg.memory, key.replace("_max", "_max_entries"), None)
                if val is not None:
                    merged_cfg[key] = val

        self._mgr = Memory6(config=merged_cfg)

    # ------------------------------------------------------------------
    #  实现 MemoryBackend 接口（与 Memory5Adapter 相同的方法签名）
    # ------------------------------------------------------------------

    def clear_working_memory(self) -> None:
        """清空所有工作记忆（但保留短期/长期记忆）"""
        working = [
            m for m in self._mgr.get_all_memories()
            if m.get("layer") == "working"
        ]
        for mem in working:
            self._mgr.delete_memory(mem["id"])
        logger.info(f"已清空 {len(working)} 条工作记忆")

    def inject_context(self, current_query: str) -> str:
        """搜索相关记忆并格式化为 LLM 可注入的上下文字符串"""
        # 1. 搜索记忆（Embedding + FAISS + LLM 双路召回）
        recall_limit = getattr(global_cfg.memory, "memory_recall_limit", 10)
        results = self._mgr.search_memory(current_query, limit=recall_limit)
        if not results:
            return ""

        # 2. Token 预算控制
        max_tokens = getattr(global_cfg.memory, "max_inject_tokens", 2000)
        chars_per_token = getattr(global_cfg.memory, "chars_per_token", 2.0)
        max_chars = int(max_tokens * chars_per_token)

        # 3. 格式化
        lines = []
        current_chars = 0
        for r in results:
            layer_label = {
                "working": "工作记忆",
                "short": "短期记忆",
                "long": "长期记忆",
            }.get(r.get("layer", ""), r.get("layer", ""))

            score = r.get("score", 0.0)
            entry = (
                f"- [id={r['id']}, "
                f"ts={r.get('created_at', r.get('accessed_at', 0)):.0f}] "
                f"[{layer_label}]\n"
                f"  {r['content']}"
                f" (相关性: {score:.2f})"
            )
            entry_chars = len(entry)
            if current_chars + entry_chars > max_chars and lines:
                break
            lines.append(entry)
            current_chars += entry_chars

        if not lines:
            return ""

        mem_context = "\n".join(lines)
        return "[系统提醒] 以下是与当前任务相关的历史记忆，仅供参考！\n\n" + mem_context

    def add_turn_memory(
        self,
        user_input: str,
        turn: int,
        reasoning_content: str,
        remaining_text: str,
        tools: list,
    ) -> None:
        """将一轮对话摘要存入工作记忆"""
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
            layer="working",
            importance=0.5,
            metadata=metadata,
        )

    def persist_and_maintain(self) -> None:
        """会话结束时：压缩短期记忆 → 遗忘过期记忆"""
        compressed_count = self._mgr.compress()
        logger.info(f"本次压缩生成 {compressed_count} 条长期记忆")

        forgot_count = self._mgr.forget_outdated()
        logger.info(f"本次遗忘 {forgot_count} 条过期/低价值记忆")

    def clear_all_memories(self) -> int:
        """清除所有记忆，返回清除条数"""
        count = len(self._mgr.get_all_memories())
        self._mgr.clear_all()
        return count