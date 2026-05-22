"""
memory_1 持久化存储层

基于 JSON 文件的记忆条目持久化，支持：
- 原子写入（先写临时文件，再 rename）
- 滚动备份（保留最近 N 个 .bak 文件）
- 损坏恢复（JSON 解析失败时回退到最新备份）
- 增删改查（CRUD）操作
"""

import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryStore:
    """记忆条目 JSON 持久化存储。

    数据格式：list[dict]，每个 dict 至少包含：
        id, role, content, timestamp, importance, tags, embedding
    """

    # 文件名模板
    MAIN_FILE = "memory1.json"
    TMP_FILE = "memory1.tmp"
    BAK_PATTERN = "memory1.bak{}.json"

    def __init__(self, storage_path: str, backup_count: int = 3):
        """
        Args:
            storage_path: 数据文件完整路径（如 D:/AI/MyClaude/data/memory/memory1.json）
            backup_count: 滚动备份数量
        """
        self._file_path = Path(storage_path)
        self._backup_count = max(backup_count, 0)
        self._data: List[Dict[str, Any]] = []
        self._ensure_directory()
        self.load()

    # ------------------------------------------------------------------ #
    #  CRUD
    # ------------------------------------------------------------------ #

    def add(self, role: str, content: str, embedding: Optional[List[float]] = None,
            metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加一条记忆，返回 ID。"""
        entry = {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
            "turn": metadata.get("turn") if metadata else None,
            "importance": metadata.get("importance", 0.5) if metadata else 0.5,
            "tags": metadata.get("tags", []) if metadata else [],
            "embedding": embedding,
            "compressed": False,
            "last_score": None,
        }
        self._data.append(entry)
        self._save()
        logger.debug(f"MemoryStore.add: id={entry['id']}, role={role}")
        return entry["id"]

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 获取单条记忆。"""
        for entry in self._data:
            if entry["id"] == memory_id:
                return dict(entry)
        return None

    def update(self, memory_id: str, **fields: Any) -> bool:
        """更新指定字段。"""
        for i, entry in enumerate(self._data):
            if entry["id"] == memory_id:
                for key, value in fields.items():
                    if key in entry:
                        entry[key] = value
                self._save()
                return True
        return False

    def delete(self, memory_id: str) -> bool:
        """删除单条记忆。"""
        before = len(self._data)
        self._data = [e for e in self._data if e["id"] != memory_id]
        if len(self._data) < before:
            self._save()
            return True
        return False

    def get_all(self) -> List[Dict[str, Any]]:
        """获取所有记忆（返回副本，防止外部修改内部数据）。"""
        return [dict(e) for e in self._data]

    def get_by_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        """批量按 ID 获取。"""
        id_set = set(ids)
        return [dict(e) for e in self._data if e["id"] in id_set]

    def delete_by_ids(self, ids: List[str]) -> int:
        """批量删除。返回删除条数。"""
        before = len(self._data)
        id_set = set(ids)
        self._data = [e for e in self._data if e["id"] not in id_set]
        deleted = before - len(self._data)
        if deleted > 0:
            self._save()
        return deleted

    def clear_all(self) -> int:
        """清空所有内存数据并删除持久化文件及备份。

        Returns:
            删除的条目数
        """
        count = len(self._data)
        self._data.clear()

        # 删除主数据文件
        if self._file_path.exists():
            self._file_path.unlink()

        # 删除临时文件
        tmp_path = self._file_path.parent / self.TMP_FILE
        if tmp_path.exists():
            tmp_path.unlink()

        # 删除所有备份文件
        for bak_file in self._glob_backups():
            bak_file.unlink()

        logger.info(f"MemoryStore.clear_all: 已删除 {count} 条记忆及全部持久化文件")
        return count

    def count(self) -> int:
        """返回当前记忆条目数。"""
        return len(self._data)

    # ------------------------------------------------------------------ #
    #  持久化
    # ------------------------------------------------------------------ #

    def load(self) -> bool:
        """从 JSON 文件加载数据。损坏时尝试从最新备份恢复。"""
        if not self._file_path.exists():
            self._data = []
            logger.info("MemoryStore.load: 数据文件不存在，初始化为空")
            return True

        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._data = data
                logger.info(f"MemoryStore.load: 加载 {len(self._data)} 条记忆")
                return True
            else:
                raise ValueError("数据文件内容不是 list 格式")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"MemoryStore.load: 数据文件损坏 ({e})，尝试从备份恢复")
            return self._restore_from_backup()

    def _save(self) -> None:
        """原子写入：先写临时文件，再 rename 到正式文件。"""
        parent = self._file_path.parent
        tmp_path = parent / self.TMP_FILE

        # 序列化为带缩进的 JSON
        json_str = json.dumps(self._data, ensure_ascii=False, indent=2)

        # 将 embedding 数组压缩为紧凑单行
        json_str = re.sub(
            r'("embedding":\s*)\[([^\]]*)\]',
            lambda m: f'{m.group(1)}[{re.sub(r"\s+", " ", m.group(2).strip())}]',
            json_str,
            flags=re.DOTALL,
        )

        # 写入临时文件
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json_str)

        # 如果原文件存在，先做滚动备份
        if self._file_path.exists():
            self._rotate_backups()

        # 原子 rename
        os.replace(tmp_path, self._file_path)

    # ------------------------------------------------------------------ #
    #  备份与恢复
    # ------------------------------------------------------------------ #

    def _rotate_backups(self) -> None:
        """滚动备份：将当前文件复制为 memory1.bak1.json，旧备份依次后移。"""
        if self._backup_count == 0:
            return

        parent = self._file_path.parent

        # 删除最旧的备份（超过 backup_count 的）
        existing = sorted(self._glob_backups(), reverse=True)
        for bak in existing[self._backup_count - 1:]:
            bak.unlink()
            logger.debug(f"MemoryStore: 删除旧备份 {bak.name}")

        # 将现有备份依次后移
        for i in range(self._backup_count - 1, 0, -1):
            old = parent / self.BAK_PATTERN.format(i)
            new = parent / self.BAK_PATTERN.format(i + 1)
            if old.exists():
                os.replace(old, new)

        # 将当前正式文件复制为 bak1
        bak1 = parent / self.BAK_PATTERN.format(1)
        shutil.copy2(self._file_path, bak1)

    def _glob_backups(self) -> List[Path]:
        """获取所有备份文件路径。"""
        return sorted(
            self._file_path.parent.glob("memory1.bak*.json"),
            key=lambda p: p.name
        )

    def _restore_from_backup(self) -> bool:
        """从最新备份恢复数据。"""
        backups = sorted(self._glob_backups(), reverse=True)
        for bak in backups:
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._data = data
                    self._save()
                    logger.info(f"MemoryStore: 从备份 {bak.name} 恢复 {len(self._data)} 条记忆")
                    return True
            except (json.JSONDecodeError, ValueError):
                continue

        logger.warning("MemoryStore: 所有备份均损坏，初始化为空")
        self._data = []
        return False

    # ------------------------------------------------------------------ #
    #  辅助
    # ------------------------------------------------------------------ #

    def _ensure_directory(self) -> None:
        """确保数据文件父目录存在。"""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

    def get_storage_path(self) -> Path:
        """返回数据文件路径。"""
        return self._file_path

    def get_file_size(self) -> int:
        """返回数据文件大小（字节）。"""
        if self._file_path.exists():
            return self._file_path.stat().st_size
        return 0