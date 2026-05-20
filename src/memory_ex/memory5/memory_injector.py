"""memory5 独立注入器 —— 将工作记忆 / 长期记忆格式化为 LLM 可注入上下文

从 src/memory/memory_injector.py 重新实现，保持行为语义一致。
"""

import logging
import re
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)


class Memory5Injector:
    """将记忆格式化为 LLM 可注入的 Markdown 上下文。"""

    DEFAULT_MAX_TOKENS = 2000

    def __init__(self, max_tokens: int = DEFAULT_MAX_TOKENS):
        self._max_tokens = int(max_tokens) if max_tokens else DEFAULT_MAX_TOKENS

    def format_context(self,
                       working_memories: List[Dict],
                       long_memories: List[Dict],
                       max_tokens: int = 0) -> str:
        if max_tokens <= 0:
            max_tokens = self._max_tokens

        if not working_memories and not long_memories:
            return ""

        parts = []

        if working_memories:
            parts.append(self._format_working_section(working_memories))

        if long_memories:
            parts.append(self._format_long_section(long_memories))

        if not parts:
            return ""

        raw = "\n\n".join(parts)
        raw = self._truncate_by_tokens(raw, max_tokens)
        return raw

    @staticmethod
    def _format_working_section(working_memories: List[Dict]) -> str:
        lines = ["[当前任务上下文]"]
        for mem in working_memories:
            metadata = mem.get("metadata", {})
            if metadata and isinstance(metadata, dict) and "turn" in metadata:
                turn = metadata.get("turn", "?")
                user_input = metadata.get("user_input", "")
                llm_reasoning = metadata.get("llm_reasoning", "")
                llm_response = metadata.get("llm_response", "")
                llm_tool_call = metadata.get("llm_tool_call", "")

                parts = [f"- [Turn {turn}]"]
                parts.append(f"  用户输入: {user_input}")
                if llm_reasoning:
                    parts.append(f"  LLM推理过程: {llm_reasoning}")
                if llm_response:
                    parts.append(f"  LLM应答: {llm_response}")
                parts.append(f"  LLM工具调用: {llm_tool_call}")
                lines.append("\n".join(parts))
            else:
                content = mem.get("content", "")
                lines.append(f"- {content}")
        return "\n".join(lines)

    @staticmethod
    def _format_long_section(long_memories: List[Dict]) -> str:
        lines = []
        for mem in long_memories:
            content = mem.get("content", "")
            score = mem.get("_score", 0.0)
            mem_id = mem.get("id", "")
            ts = mem.get("timestamp", 0)
            if ts:
                if isinstance(ts, (int, float)) and ts > 0:
                    ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                elif isinstance(ts, str) and ts.strip():
                    ts_str = ts[:19]
                else:
                    ts_str = "未知"
            else:
                ts_str = "未知"
            id_short = mem_id[:8] if mem_id else "无ID"
            lines.append(
                f"- [id={id_short}, ts={ts_str}] {content} (相关性: {score:.2f})"
            )
        return "\n".join(lines)

    def _truncate_by_tokens(self, text: str, max_tokens: int) -> str:
        if self._estimate_tokens(text) <= max_tokens:
            return text

        sections = text.split("\n\n")
        if len(sections) < 2:
            return self._truncate_text(text, max_tokens)

        header = sections[0]
        body_sections = sections[1:]

        current = header
        working_limit = int(max_tokens * 0.6)

        for i, section in enumerate(body_sections):
            candidate = current + ("\n\n" + section if current else section)
            effective_limit = working_limit if i == 0 else max_tokens

            if self._estimate_tokens(candidate) <= effective_limit:
                current = candidate
            else:
                if i == 0:
                    truncated = self._truncate_section_lines(
                        section, effective_limit - self._estimate_tokens(current)
                    )
                    if truncated:
                        current = current + "\n\n" + truncated if current else truncated
                break

        return current

    @staticmethod
    def _truncate_section_lines(section: str, max_tokens: int) -> str:
        lines = section.split("\n")
        if not lines:
            return ""
        result = []
        current_text = ""
        for line in lines:
            test = (current_text + "\n" + line).strip() if current_text else line
            if Memory5Injector._estimate_tokens(test) > max_tokens:
                break
            result.append(line)
            current_text = test
        return "\n".join(result)

    @staticmethod
    def _truncate_text(text: str, max_tokens: int) -> str:
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        return text[:max_chars - 3] + "..."

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