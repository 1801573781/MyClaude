"""
MemoryStore - 记忆条目持久化存储层

功能特性：
- JSON 文件持久化，原子写入（写临时文件后重命名）
- 滚动备份（保留最近 N 个备份，默认 5 个）
- 损坏检测与自动备份恢复
- CRUD 操作 + 按类型筛选
- 内嵌向量（embedding）存储支持

与旧 memory_store 的区别：
- 使用 dataclass 强类型，不再裸字典
- 支持 embedding 字段（为向量召回做准备）
- 支持 summary / tags 字段（为 LLM 召回做准备）
- 更健壮的损坏恢复机制
"""

import json
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass, field, asdict


@dataclass
class MemoryEntry:
    """单条记忆条目"""
    id: str = ""
    content: str = ""
    embedding: Optional[List[float]] = None
    importance: float = 0.5
    access_count: int = 0
    last_access: float = 0.0
    created_at: float = 0.0
    source_turn: int = 0
    memory_type: str = "long_term"  # short_term / long_term / working
    tags: List[str] = field(default_factory=list)
    summary: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.id:
            self.id = uuid.uuid4().hex[:12]


class MemoryStore:
    """
    JSON 文件持久化存储，支持原子写入、滚动备份、损坏恢复。

    使用方式：
        store = MemoryStore("D:/AI/MyClaude/data/memory2.json")
        store.add(MemoryEntry(content="用户喜欢 Python", importance=0.8))
        store.save()
    """

    MAX_BACKUPS = 5

    def __init__(self, file_path: str):
        self._file_path = Path(file_path)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_dir = self._file_path.parent / f"{self._file_path.stem}_backups"
        self._memories: Dict[str, MemoryEntry] = {}
        self._loaded = False

    # ========== 加载 / 保存 ==========

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    def load(self) -> bool:
        """
        从磁盘加载记忆。失败时自动尝试从备份恢复。

        Returns:
            bool: 是否成功加载（即使从备份恢复也算成功）
        """
        self._loaded = True

        if not self._file_path.exists():
            self._memories = {}
            return True

        try:
            with open(self._file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, list):
                raise ValueError("记忆文件损坏：顶层不是 JSON 数组")

            self._memories = {}
            skipped = 0
            for item in data:
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                try:
                    entry = MemoryEntry(**item)
                    self._memories[entry.id] = entry
                except (TypeError, KeyError):
                    skipped += 1

            if skipped > 0:
                print(f"[MemoryStore] 加载时跳过 {skipped} 条损坏条目")

            return True

        except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as e:
            print(f"[MemoryStore] 加载失败: {e}，尝试从备份恢复...")
            recovered = self._recover_from_backup()
            if recovered is not None:
                self._memories = recovered
                print(f"[MemoryStore] 从备份恢复成功，共 {len(recovered)} 条记忆")
                return True
            else:
                self._memories = {}
                print("[MemoryStore] 无可用备份，从空白状态开始")
                return False

    def _recover_from_backup(self) -> Optional[Dict[str, MemoryEntry]]:
        """尝试从最新有效备份恢复。"""
        if not self._backup_dir.exists():
            return None

        backups = sorted(
            self._backup_dir.glob("memory_backup_*.json"),
            reverse=True
        )

        for backup in backups:
            try:
                with open(backup, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    continue

                memories = {}
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    try:
                        entry = MemoryEntry(**item)
                        memories[entry.id] = entry
                    except (TypeError, KeyError):
                        continue

                if memories:
                    return memories
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                continue

        return None

    def _create_backup(self):
        """保存前创建滚动备份。"""
        if not self._memories:
            return

        self._backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time() * 1000)
        backup_path = self._backup_dir / f"memory_backup_{timestamp}.json"

        data = [asdict(m) for m in self._memories.values()]
        try:
            with open(backup_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            return  # 备份失败不阻塞主流程

        # 滚动删除旧备份
        backups = sorted(self._backup_dir.glob("memory_backup_*.json"))
        for old in backups[:-self.MAX_BACKUPS]:
            try:
                old.unlink()
            except OSError:
                pass

    def save(self) -> bool:
        """
        原子保存：先写 .tmp，再 rename 覆盖正式文件。

        Returns:
            bool: 保存成功返回 True
        """
        self._ensure_loaded()
        self._create_backup()

        data = [asdict(m) for m in self._memories.values()]
        tmp_path = self._file_path.with_suffix('.json.tmp')

        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self._file_path)
            return True
        except OSError as e:
            print(f"[MemoryStore] 保存失败: {e}")
            return False

    # ========== CRUD ==========

    def add(self, entry: MemoryEntry) -> str:
        """
        添加一条记忆。

        Returns:
            str: 记忆 ID
        """
        self._ensure_loaded()
        if not entry.id:
            entry.id = uuid.uuid4().hex[:12]
        self._memories[entry.id] = entry
        return entry.id

    def get(self, memory_id: str) -> Optional[MemoryEntry]:
        """
        获取单条记忆，同时更新 access_count 和 last_access。

        Returns:
            MemoryEntry 或 None
        """
        self._ensure_loaded()
        entry = self._memories.get(memory_id)
        if entry:
            entry.access_count += 1
            entry.last_access = time.time()
        return entry

    def update(self, memory_id: str, **kwargs) -> bool:
        """
        更新记忆字段。不存在的字段会被忽略。

        Returns:
            bool: 是否更新成功
        """
        self._ensure_loaded()
        entry = self._memories.get(memory_id)
        if not entry:
            return False
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        return True

    def delete(self, memory_id: str) -> bool:
        """删除单条记忆。"""
        self._ensure_loaded()
        if memory_id in self._memories:
            del self._memories[memory_id]
            return True
        return False

    def get_all(self) -> List[MemoryEntry]:
        """获取全部记忆。"""
        self._ensure_loaded()
        return list(self._memories.values())

    def get_by_type(self, memory_type: str) -> List[MemoryEntry]:
        """按类型筛选记忆。"""
        self._ensure_loaded()
        return [m for m in self._memories.values() if m.memory_type == memory_type]

    def count(self) -> int:
        """返回记忆总数。"""
        self._ensure_loaded()
        return len(self._memories)

    def clear(self):
        """清空全部记忆（不会自动保存）。"""
        self._memories = {}

    def delete_many(self, memory_ids: List[str]) -> int:
        """批量删除。返回实际删除数。"""
        self._ensure_loaded()
        count = 0
        for mid in memory_ids:
            if mid in self._memories:
                del self._memories[mid]
                count += 1
        return count

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, memory_id: str) -> bool:
        self._ensure_loaded()
        return memory_id in self._memories