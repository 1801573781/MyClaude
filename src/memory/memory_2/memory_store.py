"""
memory_2 持久化存储层（纯 JSON，无 Embedding 向量）

重构要点：
- 统一时间戳为 ISO 8601 UTC（datetime.now(timezone.utc).isoformat()）
- 新增 content_hash 内容去重，add() 重复内容返回已有 ID
- query() 参数从 exclude_compressed 改为 include_compressed（默认 True），
  使压缩摘要纳入检索候选
- 新增 count_compressed() / count_uncompressed() 精确统计
- _parse_timestamp 统一返回 datetime.min(UTC) 作为 fallback
"""

import hashlib
import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryStore:
    """记忆条目 JSON 持久化存储（纯文本，无向量）。

    数据格式：list[dict]，每个 dict 包含：
        id, role, content, content_hash, timestamp, turn,
        importance, tags, compressed, last_score

    **严禁**包含 embedding 字段。
    """

    MAIN_FILE = "memory2.json"
    TMP_FILE = "memory2.tmp"
    BAK_PATTERN = "memory2.bak{}.json"

    def __init__(self, storage_path: str, backup_count: int = 3):
        self._file_path = Path(storage_path)
        self._backup_count = max(backup_count, 0)
        self._data: List[Dict[str, Any]] = []
        self._content_hashes: set[str] = set()
        self._ensure_directory()
        self.load()

    # ------------------------------------------------------------------ #
    #  CRUD
    # ------------------------------------------------------------------ #

    def add(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加一条记忆，返回 ID。重复内容返回已有 ID（不覆盖）。

        严禁存储 Embedding 向量。
        """
        content_hash = self._compute_hash(content)

        # 去重检查
        for entry in self._data:
            if entry.get("content_hash") == content_hash:
                logger.debug(f"MemoryStore.add: 内容重复，跳过 (hash={content_hash[:8]})")
                return entry["id"]

        entry = {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "content_hash": content_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn": metadata.get("turn") if metadata else None,
            "importance": metadata.get("importance", 0.5) if metadata else 0.5,
            "tags": metadata.get("tags", []) if metadata else [],
            "compressed": metadata.get("compressed", False) if metadata else False,
            "last_score": None,
        }
        self._data.append(entry)
        self._content_hashes.add(content_hash)
        self._save()
        logger.debug(f"MemoryStore.add: id={entry['id']}, role={role}")
        return entry["id"]

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        for entry in self._data:
            if entry["id"] == memory_id:
                return dict(entry)
        return None

    def update(self, memory_id: str, **fields: Any) -> bool:
        for entry in self._data:
            if entry["id"] == memory_id:
                for key, value in fields.items():
                    entry[key] = value
                self._save()
                return True
        return False

    def delete(self, memory_id: str) -> bool:
        before = len(self._data)
        self._data = [e for e in self._data if e["id"] != memory_id]
        if len(self._data) < before:
            self._rebuild_hash_index()
            self._save()
            return True
        return False

    def get_all(self) -> List[Dict[str, Any]]:
        return [dict(e) for e in self._data]

    def get_by_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        id_set = set(ids)
        return [dict(e) for e in self._data if e["id"] in id_set]

    def delete_by_ids(self, ids: List[str]) -> int:
        before = len(self._data)
        id_set = set(ids)
        self._data = [e for e in self._data if e["id"] not in id_set]
        deleted = before - len(self._data)
        if deleted > 0:
            self._rebuild_hash_index()
            self._save()
        return deleted

    # ------------------------------------------------------------------ #
    #  统计
    # ------------------------------------------------------------------ #

    def count(self) -> int:
        return len(self._data)

    def count_compressed(self) -> int:
        return sum(1 for e in self._data if e.get("compressed", False))

    def count_uncompressed(self) -> int:
        return sum(1 for e in self._data if not e.get("compressed", False))

    def get_stats(self) -> dict:
        total = len(self._data)
        by_role: Dict[str, int] = {}
        by_tag: Dict[str, int] = {}
        for entry in self._data:
            role = entry.get("role", "unknown")
            by_role[role] = by_role.get(role, 0) + 1
            for tag in entry.get("tags", []):
                by_tag[tag] = by_tag.get(tag, 0) + 1
        return {
            "total": total,
            "compressed": self.count_compressed(),
            "uncompressed": self.count_uncompressed(),
            "by_role": by_role,
            "by_tag": by_tag,
            "file_size_bytes": self.get_file_size(),
        }

    # ------------------------------------------------------------------ #
    #  预过滤查询
    # ------------------------------------------------------------------ #

    def query(
        self,
        tag_filter: Optional[List[str]] = None,
        time_window_days: int = 0,
        role_filter: Optional[str] = None,
        include_compressed: bool = True,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """预过滤查询：按标签、时间窗口、角色过滤。

        Args:
            include_compressed: 是否包含压缩摘要。
                检索时默认 True（压缩摘要高价值，应纳入候选）。
                压缩候选选择时设为 False（只压缩未压缩的条目）。
        """
        results = [dict(e) for e in self._data]

        if tag_filter:
            filter_set = set(tag_filter)
            results = [r for r in results if filter_set.intersection(set(r.get("tags", [])))]

        if time_window_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=time_window_days)
            results = [r for r in results if self._parse_timestamp(r.get("timestamp")) >= cutoff]

        if role_filter:
            results = [r for r in results if r.get("role") == role_filter]

        if not include_compressed:
            results = [r for r in results if not r.get("compressed", False)]

        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        if 0 < limit < len(results):
            results = results[:limit]
        return results

    def batch_update_scores(self, score_map: Dict[str, float]) -> int:
        updated = 0
        for entry in self._data:
            if entry["id"] in score_map:
                entry["last_score"] = score_map[entry["id"]]
                updated += 1
        if updated > 0:
            self._save()
            logger.debug(f"MemoryStore.batch_update_scores: 更新 {updated} 条")
        return updated

    def decay_importance(self, factor: float = 0.95) -> int:
        """重要性自动衰减：仅对已压缩记忆生效（工作记忆不受影响）。"""
        count = 0
        for entry in self._data:
            if entry.get("compressed", False):
                old = entry.get("importance", 0.5)
                entry["importance"] = round(old * factor, 4)
                count += 1
        if count > 0:
            self._save()
            logger.debug(f"MemoryStore.decay_importance: 衰减 {count} 条 (factor={factor})")
        return count

    def clear_all(self) -> int:
        count = len(self._data)
        self._data.clear()
        self._content_hashes.clear()

        if self._file_path.exists():
            self._file_path.unlink()
        tmp_path = self._file_path.parent / self.TMP_FILE
        if tmp_path.exists():
            tmp_path.unlink()
        for bak_file in self._glob_backups():
            bak_file.unlink()

        logger.info(f"MemoryStore.clear_all: 已删除 {count} 条记忆及全部持久化文件")
        return count

    # ------------------------------------------------------------------ #
    #  持久化
    # ------------------------------------------------------------------ #

    def load(self) -> bool:
        if not self._file_path.exists():
            self._data = []
            self._content_hashes.clear()
            logger.info("MemoryStore.load: 数据文件不存在，初始化为空")
            return True
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if "embedding" in item:
                        logger.warning(f"MemoryStore.load: 检测到违规 embedding 字段 (id={item.get('id', '?')})")
                        item.pop("embedding", None)
                    # 兼容旧数据：补全 content_hash
                    if "content_hash" not in item:
                        item["content_hash"] = self._compute_hash(item.get("content", ""))
                    # 兼容旧数据：补全 compressed 字段
                    if "compressed" not in item:
                        item["compressed"] = False
                self._data = data
                self._rebuild_hash_index()
                logger.info(f"MemoryStore.load: 加载 {len(self._data)} 条记忆")
                return True
            else:
                raise ValueError("数据文件内容不是 list 格式")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"MemoryStore.load: 数据文件损坏 ({e})，尝试从备份恢复")
            return self._restore_from_backup()

    def _save(self) -> None:
        parent = self._file_path.parent
        tmp_path = parent / self.TMP_FILE

        safe_data = [{k: v for k, v in entry.items() if k != "embedding"} for entry in self._data]

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(safe_data, f, ensure_ascii=False, indent=2)

        if self._file_path.exists():
            self._rotate_backups()
        os.replace(tmp_path, self._file_path)

    # ------------------------------------------------------------------ #
    #  备份与恢复
    # ------------------------------------------------------------------ #

    def _rotate_backups(self) -> None:
        if self._backup_count == 0:
            return
        parent = self._file_path.parent
        existing = sorted(self._glob_backups(), reverse=True)

        for bak in existing[self._backup_count - 1:]:
            bak.unlink()

        for i in range(self._backup_count - 1, 0, -1):
            old = parent / self.BAK_PATTERN.format(i)
            new = parent / self.BAK_PATTERN.format(i + 1)
            if old.exists():
                os.replace(old, new)

        bak1 = parent / self.BAK_PATTERN.format(1)
        shutil.copy2(self._file_path, bak1)

    def _glob_backups(self) -> List[Path]:
        return sorted(self._file_path.parent.glob("memory2.bak*.json"), key=lambda p: p.name)

    def _restore_from_backup(self) -> bool:
        backups = sorted(self._glob_backups(), reverse=True)
        for bak in backups:
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        item.pop("embedding", None)
                        if "content_hash" not in item:
                            item["content_hash"] = self._compute_hash(item.get("content", ""))
                        if "compressed" not in item:
                            item["compressed"] = False
                    self._data = data
                    self._rebuild_hash_index()
                    self._save()
                    logger.info(f"MemoryStore: 从备份 {bak.name} 恢复 {len(self._data)} 条记忆")
                    return True
            except (json.JSONDecodeError, ValueError):
                continue

        logger.warning("MemoryStore: 所有备份均损坏，初始化为空")
        self._data = []
        self._content_hashes.clear()
        return False

    def _ensure_directory(self) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

    def _rebuild_hash_index(self) -> None:
        self._content_hashes = {
            e.get("content_hash", self._compute_hash(e.get("content", "")))
            for e in self._data
        }

    @staticmethod
    def _compute_hash(content: str) -> str:
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_timestamp(ts_str: Optional[str]) -> datetime:
        """解析时间戳，统一返回 UTC datetime。解析失败返回 datetime.min(UTC)。"""
        if not ts_str:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        except (ValueError, TypeError):
            pass
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
            return ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
        return datetime.min.replace(tzinfo=timezone.utc)

    def get_storage_path(self) -> Path:
        return self._file_path

    def get_file_size(self) -> int:
        if self._file_path.exists():
            return self._file_path.stat().st_size
        return 0
