"""
memory_2 持久化存储层（纯 JSON，无 Embedding 向量）

与 memory_1 的 MemoryStore 功能对等，但**严禁存储 Embedding 向量字段**。
支持：
- 原子写入（先写临时文件，再 rename）
- 滚动备份（保留最近 N 个 .bak 文件）
- 损坏恢复（JSON 解析失败时回退到最新备份）
- 增删改查（CRUD）操作
- 标签、时间窗口等预过滤查询
"""

import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryStore:
    """记忆条目 JSON 持久化存储（纯文本，无向量）。

    数据格式：list[dict]，每个 dict 至少包含：
        id, role, content, timestamp, importance, tags
    **严禁**包含 embedding 字段。
    """

    # 文件名模板
    MAIN_FILE = "memory2.json"
    TMP_FILE = "memory2.tmp"
    BAK_PATTERN = "memory2.bak{}.json"

    def __init__(self, storage_path: str, backup_count: int = 3):
        """
        Args:
            storage_path: 数据文件完整路径（如 D:/AI/MyClaude/data/memory/memory2.json）
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

    def add(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加一条记忆，返回 ID。

        严禁在此方法中计算或存储 Embedding 向量。
        """
        entry = {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn": metadata.get("turn") if metadata else None,
            "importance": metadata.get("importance", 0.5) if metadata else 0.5,
            "tags": metadata.get("tags", []) if metadata else [],
            "compressed": False,
            "last_score": None,  # LLM 最近一次评定的相关性分数
        }
        self._data.append(entry)
        self._save()
        logger.debug(f"Memory2Store.add: id={entry['id']}, role={role}")
        return entry["id"]

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 获取单条记忆。"""
        for entry in self._data:
            if entry["id"] == memory_id:
                return dict(entry)
        return None

    def update(self, memory_id: str, **fields: Any) -> bool:
        """更新指定字段（如 importance、tags、last_score）。"""
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

        logger.info(f"Memory2Store.clear_all: 已删除 {count} 条记忆及全部持久化文件")
        return count

    def count(self) -> int:
        """返回当前记忆条目数。"""
        return len(self._data)

    # ------------------------------------------------------------------ #
    #  预过滤查询（LLM 召回前使用）
    # ------------------------------------------------------------------ #

    def query(
        self,
        tag_filter: Optional[List[str]] = None,
        time_window_days: int = 0,
        role_filter: Optional[str] = None,
        exclude_compressed: bool = True,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """预过滤查询：按标签、时间窗口、角色过滤。

        Args:
            tag_filter: 按标签过滤（取交集）
            time_window_days: 仅取最近 N 天的记忆（0 = 不限）
            role_filter: 按角色过滤
            exclude_compressed: 排除已压缩的条目
            limit: 最大返回条数

        Returns:
            过滤后的记忆列表（按时间降序排列）
        """
        results = [dict(e) for e in self._data]

        # 标签过滤
        if tag_filter:
            filter_set = set(tag_filter)
            results = [
                r for r in results
                if filter_set.intersection(set(r.get("tags", [])))
            ]

        # 时间窗口过滤
        if time_window_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=time_window_days)
            results = [
                r for r in results
                if self._parse_timestamp(r.get("timestamp")) >= cutoff
            ]

        # 角色过滤
        if role_filter:
            results = [r for r in results if r.get("role") == role_filter]

        # 排除已压缩
        if exclude_compressed:
            results = [r for r in results if not r.get("compressed", False)]

        # 按时间降序排列
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        # 截断
        if 0 < limit < len(results):
            results = results[:limit]

        return results

    def batch_update_scores(self, score_map: Dict[str, float]) -> int:
        """批量回写 LLM 评分。

        Args:
            score_map: {memory_id: score} 映射

        Returns:
            成功更新的条数
        """
        updated = 0
        for entry in self._data:
            if entry["id"] in score_map:
                entry["last_score"] = score_map[entry["id"]]
                updated += 1
        if updated > 0:
            self._save()
            logger.debug(f"Memory2Store.batch_update_scores: 更新 {updated} 条")
        return updated

    # ------------------------------------------------------------------ #
    #  持久化
    # ------------------------------------------------------------------ #

    def load(self) -> bool:
        """从 JSON 文件加载数据。损坏时尝试从最新备份恢复。"""
        if not self._file_path.exists():
            self._data = []
            logger.info("Memory2Store.load: 数据文件不存在，初始化为空")
            return True

        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                # 验证无 embedding 字段（红线合规检查）
                for item in data:
                    if "embedding" in item:
                        logger.warning(
                            f"Memory2Store.load: 检测到违规 embedding 字段 "
                            f"（id={item.get('id', '?')}），将被忽略"
                        )
                        item.pop("embedding", None)
                self._data = data
                logger.info(f"Memory2Store.load: 加载 {len(self._data)} 条记忆")
                return True
            else:
                raise ValueError("数据文件内容不是 list 格式")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Memory2Store.load: 数据文件损坏 ({e})，尝试从备份恢复")
            return self._restore_from_backup()

    def _save(self) -> None:
        """原子写入：先写临时文件，再 rename 到正式文件。"""
        parent = self._file_path.parent
        tmp_path = parent / self.TMP_FILE

        # 写入前确保无 embedding 字段
        safe_data = [{k: v for k, v in entry.items() if k != "embedding"}
                     for entry in self._data]

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(safe_data, f, ensure_ascii=False, indent=2)

        # 滚动备份
        if self._file_path.exists():
            self._rotate_backups()

        # 原子 rename
        os.replace(tmp_path, self._file_path)

    # ------------------------------------------------------------------ #
    #  备份与恢复
    # ------------------------------------------------------------------ #

    def _rotate_backups(self) -> None:
        """滚动备份：memory2.json → memory2.bak1.json，旧备份依次后移。"""
        if self._backup_count == 0:
            return

        parent = self._file_path.parent
        existing = sorted(self._glob_backups(), reverse=True)

        # 删除最旧的备份
        for bak in existing[self._backup_count - 1:]:
            bak.unlink()
            logger.debug(f"Memory2Store: 删除旧备份 {bak.name}")

        # 备份文件依次后移
        for i in range(self._backup_count - 1, 0, -1):
            old = parent / self.BAK_PATTERN.format(i)
            new = parent / self.BAK_PATTERN.format(i + 1)
            if old.exists():
                os.replace(old, new)

        # 复制当前文件为 bak1
        bak1 = parent / self.BAK_PATTERN.format(1)
        shutil.copy2(self._file_path, bak1)

    def _glob_backups(self) -> List[Path]:
        """获取所有备份文件路径。"""
        return sorted(
            self._file_path.parent.glob("memory2.bak*.json"),
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
                    # 移除 embedding 字段
                    for item in data:
                        item.pop("embedding", None)
                    self._data = data
                    self._save()
                    logger.info(f"Memory2Store: 从备份 {bak.name} 恢复 {len(self._data)} 条记忆")
                    return True
            except (json.JSONDecodeError, ValueError):
                continue

        logger.warning("Memory2Store: 所有备份均损坏，初始化为空")
        self._data = []
        return False

    def _ensure_directory(self) -> None:
        """确保数据文件父目录存在。"""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _parse_timestamp(ts_str: Optional[str]) -> datetime:
        """解析 ISO 时间戳。"""
        if not ts_str:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        except (ValueError, TypeError):
            return datetime.min.replace(tzinfo=timezone.utc)

    def get_storage_path(self) -> Path:
        """返回数据文件路径。"""
        return self._file_path

    def get_file_size(self) -> int:
        """返回数据文件大小（字节）。"""
        if self._file_path.exists():
            return self._file_path.stat().st_size
        return 0