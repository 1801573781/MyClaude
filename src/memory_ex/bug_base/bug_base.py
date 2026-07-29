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

        扫描 raw_memory/MyClaude_*.md 文件，
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

    def check_and_archive_fixed(self) -> int:
        """检查所有 open 记录的文件哈希，若文件已变更则标记为 fixed。

        同时将已有的 fixed 记录归档到 _archive/。

        Returns:
            标记为 fixed 的记录数（不含归档数）。
        """
        count = 0

        # 1. 检查 open 记录的文件哈希变更，标记为 fixed
        for record in self.store.get_all_open():
            if not record.file_hashes:
                continue
            for file_path, old_hash in record.file_hashes.items():
                current_hash = self.store._compute_file_hash(file_path)
                # 文件存在且哈希变更，或文件已被删除
                if current_hash and current_hash != old_hash:
                    self.store.update(record.id, status="fixed")
                    count += 1
                    break
                elif not current_hash and old_hash:
                    # 文件被删除也视为已修复（代码已变更）
                    self.store.update(record.id, status="fixed")
                    count += 1
                    break

        # 2. 归档所有已有的 fixed 记录（含本轮新标记的）
        for record in self.store.get_all_fixed():
            self.store.archive(record.id)

        return count

    def archive_fixed(self, record_id: str | None = None) -> int:
        """归档已修复的Bug。

        Args:
            record_id: 指定 ID 归档。如果为 None，则归档所有 fixed 状态的记录。

        Returns:
            归档的记录数。
        """
        count = 0
        if record_id:
            if self.store.archive(record_id):
                count = 1
        else:
            for record in self.store.get_all_fixed():
                if self.store.archive(record.id):
                    count += 1
        return count

    def mark_memory_linked(self, record_id: str, memory_id: str) -> bool:
        """标记某Bug已提取到 Memory 系统。

        Returns:
            是否标记成功。
        """
        return self.store.update(record_id, memory_linked=memory_id)

    def get_stats(self) -> dict[str, dict[str, int]]:
        """获取统计信息。"""
        return self.store.get_stats()

    def get_record(self, record_id: str) -> BugRecord | None:
        """按 ID 获取记录。"""
        return self.store.get(record_id)

    def get_all_open(self) -> list[BugRecord]:
        """获取所有 open 状态的记录。"""
        return self.store.get_all_open()

    def get_by_module(self, module: str) -> list[BugRecord]:
        """获取指定模块的所有 open 状态记录。"""
        return self.store.get_by_module(module)

    def get_by_file(self, file_path: str) -> list[BugRecord]:
        """获取涉及指定文件的所有 open 状态记录。"""
        return self.store.get_by_file(file_path)
