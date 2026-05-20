"""memory6 独立注入器 —— 将三层记忆格式化为 LLM 可注入上下文

从 src/memory2/memory_manager.py 的 inject_context() 重新实现。
支持 summary 字段展示、token 预算截断。
"""

import logging
import re
import time as _time
from typing import List, Dict

logger = logging.getLogger(__name__)


class Memory6Injector:
    """将三层记忆（Working/Short/Long）格式化为 LLM 注入文本。"""

    DEFAULT_MAX_TOKENS = 2000
    DEFAULT_CHARS_PER_TOKEN = 2.0  # 中文估算

    def __init__(self, max_tokens: int = DEFAULT_MAX_TOKENS, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN):
        self._max_tokens = max_tokens
        self._chars_per_token = chars_per_token

    def format_context(self,
                       working_memories: List[Dict],
                       recall_results: List[Dict],
                       inject_header: str = "",
                       max_tokens: int = 0) -> str:
        if max_tokens <= 0:
            max_tokens = self._max_tokens

        if not working_memories and not recall_results:
            return ""

        lines = [inject_header] if inject_header else []
        char_count = len(inject_header)

        # 工作记忆段
        if working_memories:
            wm_section = self._format_working_section(working_memories)
            for line in wm_section:
                if char_count + len(line) > max_tokens * self._chars_per_token:
                    break
                lines.append(line)
                char_count += len(line)

        # 长期记忆段（召回结果）
        if recall_results:
            lines.append("")
            char_count += 1
            for r in recall_results:
                ts_str = "未知"
                ts = r.get("timestamp", 0)
                if isinstance(ts, str):
                    ts_str = ts[:19]
                elif ts > 0:
                    ts_str = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(ts))

                mem_id = r.get("id", "")[:8]
                summary = r.get("summary", "") or r.get("content", "")[:80].replace("\n", " ").strip()
                content = r.get("content", "")[:200].replace("\n", " ").strip()
                score = r.get("score", r.get("_score", 0))

                item_text = (
                    f"- [id={mem_id}, ts={ts_str}] {summary}\n"
                    f"  内容: {content} (相关性: {score:.2f})"
                )

                if char_count + len(item_text) > max_tokens * self._chars_per_token:
                    break

                lines.append(item_text)
                char_count += len(item_text)

        if len(lines) <= (1 if inject_header else 0):
            return ""

        return "\n".join(lines)

    @staticmethod
    def _format_working_section(working_memories: List[Dict]) -> List[str]:
        lines = ["[工作记忆 (Working Memory)]"]
        for mem in working_memories:
            metadata = mem.get("metadata", {})
            if metadata and isinstance(metadata, dict) and "turn" in metadata:
                turn = metadata.get("turn", "?")
                user_input = metadata.get("user_input", "")
                lines.append(f"- [Turn {turn}] 用户输入: {user_input}")
                if metadata.get("llm_reasoning"):
                    lines.append(f"  LLM推理过程: {metadata['llm_reasoning']}")
                if metadata.get("llm_tool_call"):
                    lines.append(f"  LLM工具调用: {metadata['llm_tool_call']}")
                if metadata.get("llm_response"):
                    lines.append(f"  LLM应答: {metadata['llm_response']}")
            else:
                content = mem.get("content", "")
                lines.append(f"- {content}")
        return lines

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