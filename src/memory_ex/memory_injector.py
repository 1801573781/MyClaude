"""记忆上下文注入器。

对应设计文档第二章和第六章。

职责：
- 格式化 Layer 1（MEMORY.md）内容为注入文本
- 在注入内容开头添加专用标记 [MEMORY_INJECTION_v1]
- 空内容时返回空字符串（冷启动不注入）
- 控制 token 预算（超过上限时截断）
- 统计召回条目数（供 query_loop.py 打印召回提示）
"""

import logging
import re
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

# 专用注入标记，供 LLMAPIMessage.refresh_memory_injection() 匹配
INJECTION_MARKER = "[MEMORY_INJECTION_v1]"


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（字符数 / 2.5）。"""
    return int(len(text) / 2.5)


class MemoryInjector:
    """记忆上下文注入器。

    将 Layer 1 内容格式化为可供 api_messages 使用的注入文本。
    注入内容由专用标记 [MEMORY_INJECTION_v1] 开头，便于跨 Query 替换。
    """

    def __init__(self, mem_config: Any):
        """初始化注入器。

        Args:
            mem_config: memory_ex.yaml 配置对象
        """
        injection_config = mem_config.injection
        self._max_tokens = int(getattr(injection_config, "max_tokens", 2000))

    def format_for_injection(self, entries: list, query: str = "") -> str:
        """格式化记忆条目列表为注入文本。

        Args:
            entries: 记忆条目列表，每个元素含 id, tags, content, raw_line
            query: 当前用户查询（保留接口兼容，当前版本未使用）

        Returns:
            格式化后的注入文本。如果列表为空，返回空字符串。

        格式：
            [MEMORY_INJECTION_v1]
            ## 记忆索引
            <条目内容>

            [召回数: N]

        注意：
            caution 类型的记忆条目，其首个 tag 为 [caution]，
            注入时替换为 [⚠️ caution] 以增加视觉警示效果。
        """
        if not entries:
            return ""

        # 从条目列表构建 Layer 1 文本
        layer1_lines = []
        for entry in entries:
            raw_line = entry.get("raw_line", "")
            if raw_line:
                # caution 类型增加警示标记
                if raw_line.strip().startswith("- [caution]"):
                    raw_line = raw_line.replace(
                        "- [caution]", "- [⚠️ caution]", 1
                    )
                layer1_lines.append(raw_line)

        if not layer1_lines:
            return ""

        layer1_content = "\n".join(layer1_lines)
        recall_count = len(layer1_lines)

        # 构建注入文本
        header = f"{INJECTION_MARKER}\n## 记忆索引\n"
        footer = f"\n[召回数: {recall_count}]"

        injection_text = header + layer1_content.strip() + footer

        # Token 预算控制
        estimated_tokens = _estimate_tokens(injection_text)
        if estimated_tokens > self._max_tokens:
            injection_text = self._truncate_to_budget(
                layer1_content.strip(), recall_count
            )

        return injection_text

    def _count_entries(self, layer1_content: str) -> int:
        """统计 Layer 1 中的记忆条目数。

        统计以 "- " 开头的行数（包括进化条目）。
        """
        if not layer1_content:
            return 0

        count = 0
        for line in layer1_content.split("\n"):
            line = line.strip()
            if line.startswith("- ") or line.startswith("-\t"):
                count += 1

        return count

    def _truncate_to_budget(
        self, layer1_content: str, recall_count: int
    ) -> str:
        """截断 Layer 1 内容以满足 token 预算。

        保留进化条目和高价值条目，截断低价值条目。

        Args:
            layer1_content: Layer 1 原始内容
            recall_count: 原始召回数

        Returns:
            截断后的注入文本
        """
        lines = layer1_content.split("\n")
        kept_lines = []
        current_tokens = 0

        # 预留 header + footer 的 token
        header_footer = f"{INJECTION_MARKER}\n## 记忆索引\n\n[召回数: {recall_count}]"
        reserved_tokens = _estimate_tokens(header_footer)
        budget = self._max_tokens - reserved_tokens

        # 优先保留进化条目
        evolved_lines = []
        normal_lines = []

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith("- [EVOLVED]"):
                # 进化条目可能占用两行（正文 + 来源行）
                block = [line]
                if i + 1 < len(lines) and lines[i + 1].strip().startswith("来源:"):
                    block.append(lines[i + 1])
                    i += 1
                evolved_lines.append(block)
            elif stripped.startswith("- "):
                normal_lines.append([line])
            else:
                # 非条目行（如标题、空行），保留
                kept_lines.append(line)

            i += 1

        # 先添加进化条目
        for block in evolved_lines:
            block_text = "\n".join(block)
            block_tokens = _estimate_tokens(block_text)
            if current_tokens + block_tokens <= budget:
                kept_lines.append(block_text)
                current_tokens += block_tokens

        # 再添加普通条目
        for block in normal_lines:
            block_text = "\n".join(block)
            block_tokens = _estimate_tokens(block_text)
            if current_tokens + block_tokens <= budget:
                kept_lines.append(block_text)
                current_tokens += block_tokens
            else:
                break

        # 统计实际保留的条目数
        actual_count = sum(
            1
            for line in kept_lines
            if isinstance(line, str)
            and line.strip().startswith("- ")
        )

        # 重新构建
        header = f"{INJECTION_MARKER}\n## 记忆索引\n"
        footer = f"\n[召回数: {actual_count}]"

        body = "\n".join(kept_lines)

        return header + body + footer

    def get_injection_marker(self) -> str:
        """返回注入标记字符串。

        供 LLMAPIMessage.refresh_memory_injection() 匹配使用。
        """
        return INJECTION_MARKER
