"""BugBase — Bug库系统主入口。

协调 store、extractor、retriever、injector 四个组件，
提供统一的外部调用接口，管理组件生命周期。
"""

from pathlib import Path

from .bug_store import BugStore, BugRecord
from .bug_extractor import BugExtractor
from .bug_retriever import BugRetriever
from .bug_injector import BugInjector


class BugBase:
    """Bug库系统主入口，协调各组件。"""

    def __init__(self, base_dir: Path, llm_client, max_injection_tokens: int = 2000):
        """初始化Bug库系统。

        Args:
            base_dir: Bug库根目录，指向 memory_storage/memory_ex/bug_base/
            llm_client: LLM 客户端实例。
            max_injection_tokens: 注入文本的最大 token 预算。
        """
        self.store = BugStore(base_dir)
        self.extractor = BugExtractor(llm_client, self.store)
        self.retriever = BugRetriever(llm_client, self.store)
        self.injector = BugInjector(max_tokens=max_injection_tokens)

    def extract_from_raw_entries(self) -> dict:
        """从 Memory 系统 Layer 0 原始记忆中提取 Bug。

        读取 raw_memory.jsonl，过滤已提取条目，调用 LLM 提取 Bug。

        Returns:
            统计信息字典，包含 processed, extracted, skipped, details。
        """
        return self.extractor.extract_from_raw_entries()

    def extract_from_md_logs(self) -> dict:
        """从 MD 会话日志中提取 Bug。

        扫描 raw_session_log/MyClaude_*.md 文件，
        跳过已提取的文件（记录在 bug_ext_record.md 中），
        调用 LLM 提取 Bug，存入Bug库。

        Returns:
            统计信息字典
        """
        return self.extractor.extract_from_md_logs()

    def extract_from_session(
        self, api_messages: list[dict], session_id: str
    ) -> list[str]:
        """从对话中提取 Bug，存入Bug库。

        Returns:
            新增的 Bug ID 列表。
        """
        return self.extractor.extract_from_session(api_messages, session_id)

    def retrieve_and_inject(
        self, file_paths: list[str], task_context: str
    ) -> str:
        """召回相关Bug并格式化为注入文本。

        Returns:
            注入字符串（无匹配时返回空串）。
        """
        records = self.retriever.retrieve(file_paths, task_context)
        if not records:
            return ""
        return self.injector.inject(records)

    def retrieve(
        self, file_paths: list[str], task_context: str = "", skip_stage2: bool = False
    ) -> list[BugRecord]:
        """仅召回，不注入（用于 /bug rt 手动测试）。

        Returns:
            召回的Bug记录列表。
        """
        return self.retriever.retrieve(file_paths, task_context, skip_stage2=skip_stage2)

    def mark_memory_linked(self, record_id: str, memory_id: str) -> bool:
        """标记某Bug已提取到 Memory 系统。

        Returns:
            是否标记成功。
        """
        return self.store.update(record_id, memory_linked=memory_id)

    def get_stats(self) -> dict[str, int]:
        """获取统计信息。"""
        return self.store.get_stats()

    def get_record(self, record_id: str) -> BugRecord | None:
        """按 ID 获取记录。"""
        return self.store.get(record_id)

    def get_all(self) -> list[BugRecord]:
        """获取所有记录。"""
        return self.store.get_all()

    def get_by_module(self, module: str) -> list[BugRecord]:
        """获取指定模块的所有记录。"""
        return self.store.get_by_module(module)

    def get_by_file(self, file_path: str) -> list[BugRecord]:
        """获取涉及指定文件的所有记录。"""
        return self.store.get_by_file(file_path)

    def set_extract_progress_callback(self, callback):
        """设置提取进度回调函数。

        Args:
            callback: 回调函数，签名 callback(completed: int, total: int, action: str)
        """
        self.extractor.set_progress_callback(callback)

    def get_extraction_stats(self) -> dict:
        """获取Bug提取的统计信息（用于 /bug ext 启动前预估）。

        Returns:
            包含 md_total, md_extracted, md_pending 的字典
        """
        import os
        raw_memory_dir = self.store.base_dir.parent / "raw_session_log"
        if not raw_memory_dir.exists():
            return {"md_total": 0, "md_extracted": 0, "md_pending": 0}

        md_files = sorted(raw_memory_dir.glob("MyClaude_*.md"))
        md_total = len(md_files)
        extracted_offsets = self.store.get_extracted_md_files()

        md_pending = 0
        for md_file in md_files:
            current_size = os.path.getsize(str(md_file))
            last_offset = extracted_offsets.get(md_file.name, 0)
            if current_size > last_offset:
                md_pending += 1

        return {
            "md_total": md_total,
            "md_extracted": md_total - md_pending,
            "md_pending": md_pending,
        }
