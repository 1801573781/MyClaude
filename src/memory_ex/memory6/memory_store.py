"""memory6 独立存储层 —— JSON 文件持久化 + 原子写入 + 滚动备份 + 损坏恢复

从 src/memory2/memory_store.py 重新实现，保持行为语义一致。
与 memory5 的区别：每条记忆包含 embedding 向量字段。
"""

import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class Memory6Store:
    """Memory6 的磁盘存储，管理含 Embedding 向量的记忆条目。"""

    def __init__(self, storage_path: str):
        self._storage_dir = Path(storage_path)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._storage_dir / "memories.json"
        self._memories: List[Dict] = []
        self._load()

    @staticmethod
    def _now_iso() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    @staticmethod
    def _normalize_timestamp(value, field_name: str = "") -> str:
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (int, float)) and value > 0:
            try:
                return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))
            except (OSError, ValueError):
                pass
        return Memory6Store._now_iso()

    def add(self, memory: Dict) -> str:
        if not memory.get("id"):
            memory["id"] = uuid.uuid4().hex
        now = self._now_iso()
        memory["timestamp"] = self._normalize_timestamp(memory.get("timestamp", now))
        memory["last_access"] = self._normalize_timestamp(memory.get("last_access", now))
        memory.setdefault("access_count", 0)
        memory.setdefault("importance", 0.5)
        memory.setdefault("tags", [])
        memory.setdefault("metadata", {})
        # vector 字段允许为 None（working 记忆不计算向量）
        if "vector" not in memory:
            memory["vector"] = None

        self._memories.append(memory)
        self._flush()
        logger.info(f"Memory6Store 添加记忆 {memory['id']} (layer={memory.get('layer')})")
        return memory["id"]

    def get(self, memory_id: str) -> Optional[Dict]:
        for mem in self._memories:
            if mem["id"] == memory_id:
                return mem
        return None

    def get_all(self, layer: Optional[str] = None) -> List[Dict]:
        if layer is None:
            return list(self._memories)
        return [mem for mem in self._memories if mem.get("layer") == layer]

    def update(self, memory_id: str, **kwargs) -> bool:
        mem = self.get(memory_id)
        if mem is None:
            return False
        for k, v in kwargs.items():
            if k == "importance":
                v = max(0.0, min(1.0, float(v)))
            if k == "last_access":
                v = self._normalize_timestamp(v)
            mem[k] = v
        self._flush()
        logger.info(f"Memory6Store 更新记忆 {memory_id}")
        return True

    def delete(self, memory_id: str) -> bool:
        for i, mem in enumerate(self._memories):
            if mem["id"] == memory_id:
                self._memories.pop(i)
                self._flush()
                logger.info(f"Memory6Store 删除记忆 {memory_id}")
                return True
        return False

    def delete_batch(self, memory_ids: List[str]) -> int:
        ids_set = set(memory_ids)
        original_len = len(self._memories)
        self._memories = [mem for mem in self._memories if mem["id"] not in ids_set]
        deleted = original_len - len(self._memories)
        if deleted > 0:
            self._flush()
            logger.info(f"Memory6Store 批量删除 {deleted} 条记忆")
        return deleted

    def count(self, layer: Optional[str] = None) -> int:
        return len(self.get_all(layer))

    def _flush(self):
        self._backup()
        temp_path = self._file_path.with_suffix(".json.tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self._memories, f, ensure_ascii=False, indent=2, default=str)
            os.replace(str(temp_path), str(self._file_path))
        except Exception as e:
            logger.error(f"Memory6Store 写入失败: {e}")
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def _backup(self):
        if not self._file_path.exists():
            return
        for i in range(3, 0, -1):
            old_bak = self._storage_dir / f"memories.json.bak.{i}"
            if i == 3:
                old_bak.unlink(missing_ok=True)
            else:
                new_bak = self._storage_dir / f"memories.json.bak.{i + 1}"
                if old_bak.exists():
                    shutil.move(str(old_bak), str(new_bak))
        shutil.copy2(str(self._file_path),
                     str(self._storage_dir / "memories.json.bak.1"))

    def _load(self):
        raw = self._read_with_recovery()
        self._memories = []
        for item in raw:
            try:
                validated = self._validate_item(item)
                self._memories.append(validated)
            except Exception as e:
                logger.warning(f"Memory6Store 跳过损坏条目: {e}")
        logger.info(f"Memory6Store 加载完成: {len(self._memories)} 条记忆")

    def _read_with_recovery(self) -> list:
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                raise ValueError("根元素不是列表")
            return raw
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Memory6Store 文件损坏或不存在: {e}")
            backups = sorted(
                self._storage_dir.glob("memories.json.bak.*"),
                key=lambda p: int(p.suffix.split(".")[-1])
            )
            for bak_path in backups:
                try:
                    with open(bak_path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    if isinstance(raw, list):
                        logger.info(f"Memory6Store 从备份 {bak_path} 恢复成功")
                        return raw
                except Exception:
                    continue
            logger.warning("Memory6Store 所有备份恢复失败，初始化为空列表")
            return []

    def _validate_item(self, item: Dict) -> Dict:
        validated = dict(item)

        if not isinstance(validated.get("id"), str) or not validated["id"].strip():
            validated["id"] = uuid.uuid4().hex

        if "content" not in validated:
            validated["content"] = ""

        if validated.get("layer") not in ("working", "short", "long"):
            validated["layer"] = "short"

        importance = validated.get("importance", 0.5)
        if not isinstance(importance, (int, float)):
            importance = 0.5
        validated["importance"] = max(0.0, min(1.0, float(importance)))

        ts = validated.get("timestamp", 0)
        validated["timestamp"] = self._normalize_timestamp(ts, "timestamp")

        validated.setdefault("access_count", 0)

        la = validated.get("last_access", 0)
        validated["last_access"] = self._normalize_timestamp(la, "last_access")

        if not isinstance(validated.get("tags"), list):
            validated["tags"] = []

        if not isinstance(validated.get("metadata"), dict):
            validated["metadata"] = {}

        if "vector" not in validated:
            validated["vector"] = None

        return validated

    def clear_all(self):
        self._memories = []
        self._flush()
        for bak_path in self._storage_dir.glob("memories.json.bak.*"):
            try:
                bak_path.unlink()
                logger.info(f"Memory6Store 已删除备份: {bak_path}")
            except OSError as e:
                logger.warning(f"Memory6Store 删除备份失败 {bak_path}: {e}")

    def rebuild_from_list(self, memories: List[Dict]):
        self._memories = memories
        self._flush()