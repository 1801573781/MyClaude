"""BugInjector — Bug库注入层。

将召回的Bug记录格式化为 LLM 可读的上下文文本，
控制注入的 token 预算，区分"参考"和"警示"的语气。
"""

from .bug_store import BugRecord


class BugInjector:
    """将Bug记录格式化为注入文本。"""

    def __init__(self, max_tokens: int = 2000):
        """初始化注入器。

        Args:
            max_tokens: 注入文本的最大 token 预算。
        """
        self.max_tokens = max_tokens

    def inject(self, records: list[BugRecord]) -> str:
        """将Bug记录格式化为注入文本。

        Args:
            records: 召回的Bug记录列表。

        Returns:
            格式化的字符串，供 QueryLoop 注入到 api_messages。
            无记录时返回空串。
        """
        if not records:
            return ""

        header = "[⚠️ 历史Bug提醒] 即将修改的文件有以下历史Bug记录，请注意避免重蹈覆辙：\n"
        sections = []
        total_tokens = self._estimate_tokens(header)

        for i, record in enumerate(records, 1):
            section = self._format_record(record, i)
            section_tokens = self._estimate_tokens(section)

            # 检查 token 预算
            if total_tokens + section_tokens > self.max_tokens:
                sections.append(f"...（因 token 预算限制，省略剩余 {len(records) - i + 1} 条记录）")
                break

            sections.append(section)
            total_tokens += section_tokens

        return header + "\n\n".join(sections)

    def _format_record(self, record: BugRecord, index: int) -> str:
        """格式化单条记录为警示文本。"""
        files_str = ", ".join(record.affected_files) if record.affected_files else "无"

        lines = [
            f"{index}. [{record.id}] {record.title}",
            f"   - 文件: {files_str}",
            f"   - 根因: {record.root_cause}",
            f"   - 注意事项: {record.caution}",
        ]

        if record.generalization:
            lines.append(f"   - 举一反三: {record.generalization}")

        return "\n".join(lines)

    def _estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数。

        中文按 1.5 字/token，英文按 4 字符/token。
        """
        chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_count = len(text) - chinese_count
        return int(chinese_count / 1.5 + other_count / 4)
