import logging
import re
from typing import List, Dict


logger = logging.getLogger(__name__)


class MemoryInjector:
    """
    将工作记忆和检索到的长期记忆格式化为可注入的上下文文本。
    """

    # 注入内容的最大总 token 数（默认）
    DEFAULT_MAX_TOKENS = 2000

    def __init__(self, max_tokens: int = DEFAULT_MAX_TOKENS):
        """
        参数:
            max_tokens: 注入上下文的最大 token 数。
        """
        self._max_tokens = max_tokens

    # ========== 公开接口 ==========

    def format_context(self,
                       working_memories: List[Dict],
                       long_memories: List[Dict],
                       max_tokens: int = 0) -> str:
        """
        将工作记忆和长期记忆格式化为注入文本。

        参数:
            working_memories: 当前工作记忆列表。
            long_memories:    检索到的长期记忆列表（已含 _score 字段）。
            max_tokens:       最大 token 数，0 表示使用默认值。

        返回:
            格式化的 Markdown 上下文字符串。
            若无任何记忆，返回空字符串 ""。
        """
        if max_tokens <= 0:
            max_tokens = self._max_tokens

        if not working_memories and not long_memories:
            return ""

        parts = []

        # 工作记忆段
        if working_memories:
            wm_section = self._format_working_section(working_memories)
            parts.append(wm_section)

        # 长期记忆段
        if long_memories:
            lm_section = self._format_long_section(long_memories)
            parts.append(lm_section)

        if not parts:
            return ""

        header = "[记忆上下文 - 由 Memory 模块自动生成]\n\n"
        raw = header + "\n\n".join(parts)

        # Token 截断
        raw = self._truncate_by_tokens(raw, max_tokens)

        return raw

    # ========== 格式化 ==========

    @staticmethod
    def _format_working_section(working_memories: List[Dict]) -> str:
        """格式化工作记忆段（优先使用 metadata 结构化字段，换行展示）。"""
        lines = ["[当前任务上下文]"]
        for mem in working_memories:
            metadata = mem.get("metadata", {})
            if metadata and isinstance(metadata, dict) and "turn" in metadata:
                # 使用结构化 metadata 换行展示
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
                # 降级：直接展示 content（兼容旧格式）
                content = mem.get("content", "")
                lines.append(f"- {content}")
        return "\n".join(lines)

    @staticmethod
    def _format_long_section(long_memories: List[Dict]) -> str:
        """格式化长期记忆段。"""
        lines = ["[相关历史记忆]"]
        for mem in long_memories:
            content = mem.get("content", "")
            score = mem.get("_score", 0.0)
            lines.append(f"- {content} (相关性: {score:.2f})")
        return "\n".join(lines)

    # ========== Token 截断 ==========

    def _truncate_by_tokens(self, text: str, max_tokens: int) -> str:
        """
        按 token 数截断文本。

        策略：
            1. 工作记忆段最多占 max_tokens * 0.6，超出则截断末尾条目。
            2. 长期记忆段随后追加，直到总 token 超过 max_tokens。
            3. 被截断的条目记录 logging.debug。
        """
        if self._estimate_tokens(text) <= max_tokens:
            return text

        # 分割为段落
        sections = text.split("\n\n")
        if len(sections) < 2:
            # 仅有 header，按字符截断
            return self._truncate_text(text, max_tokens)

        header = sections[0]  # [记忆上下文...]
        body_sections = sections[1:]

        current = header
        working_limit = int(max_tokens * 0.6)

        for i, section in enumerate(body_sections):
            candidate = current + ("\n\n" + section if current else section)
            effective_limit = working_limit if i == 0 else max_tokens

            if self._estimate_tokens(candidate) <= effective_limit:
                current = candidate
            else:
                # 需要截断当前 section
                if i == 0:
                    # 截断工作记忆
                    truncated = self._truncate_section_lines(
                        section, effective_limit - self._estimate_tokens(current)
                    )
                    if truncated:
                        current = current + "\n\n" + truncated if current else truncated
                    logger.debug("工作记忆段因 token 限制被截断")
                # 长期记忆段不再追加
                break

        return current

    @staticmethod
    def _truncate_section_lines(section: str, max_tokens: int) -> str:
        """逐行截断一个段落到指定 token 数。"""
        lines = section.split("\n")
        if not lines:
            return ""

        result = []
        current_text = ""

        for line in lines:
            test = (current_text + "\n" + line).strip() if current_text else line
            if MemoryInjector._estimate_tokens(test) > max_tokens:
                break
            result.append(line)
            current_text = test

        return "\n".join(result)

    @staticmethod
    def _truncate_text(text: str, max_tokens: int) -> str:
        """按字符粗略截断文本。"""
        # 粗略折算：1 token ≈ 4 字符
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        return text[:max_chars - 3] + "..."

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
            chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
            other_chars = len(text) - chinese_chars
            return other_chars // 4 + chinese_chars // 2