"""
MemoryStore - JSON 文件持久化存储

负责记忆的物理存储：基于 JSON 文件的持久化，支持 CRUD 与元数据管理。
包含原子写入、滚动备份、数据校验与损坏恢复。
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryStore:
    """
    基于 JSON 文件的记忆持久化存储。

    特性:
        - 全量写入 + 原子替换（临时文件 → os.replace）
        - 滚动备份（最多 3 个 .bak.N 文件）
        - 启动时全量加载 + 数据校验
        - 损坏文件自动回退到备份
    """

    def __init__(self, storage_path: str):
        """
        初始化 MemoryStore，立即加载持久化文件。

        参数:
            storage_path: 持久化文件目录的绝对路径（如 D:/AI/MyClaude/.memdir）。
        """
        self._storage_dir = Path(storage_path)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._storage_dir / "memories.json"
        self._memories: List[Dict] = []
        self._load()

    # ========== CRUD ==========

    def add(self, memory: Dict) -> None:
        """
        添加一条记忆并持久化。

        参数:
            memory: 记忆字典（必须包含所有必填字段）。
        """
        self._memories.append(memory)
        self._flush()

    def get_by_id(self, memory_id: str) -> Optional[Dict]:
        """
        按 ID 查找记忆。

        返回:
            记忆字典，若未找到则返回 None。
        """
        for mem in self._memories:
            if mem["id"] == memory_id:
                return mem
        return None

    def get_all(self, mem_type: Optional[str] = None) -> List[Dict]:
        """
        返回所有指定类型的记忆。

        参数:
            mem_type: None 返回全部，或指定 "short" / "long" / "working"。

        返回:
            记忆字典列表（副本，调用方修改不影响内部状态）。
        """
        if mem_type is None:
            return list(self._memories)
        return [mem for mem in self._memories if mem["type"] == mem_type]

    def get_by_types(self, types: List[str]) -> List[Dict]:
        """
        返回所有匹配类型的记忆。

        参数:
            types: 类型列表，如 ["short", "long"]。

        返回:
            记忆字典列表（副本）。
        """
        return [mem for mem in self._memories if mem["type"] in types]

    def update(self, memory_id: str, updates: Dict) -> bool:
        """
        更新记忆的部分字段。

        参数:
            memory_id: 目标记忆 ID。
            updates: 要更新的字段字典（key 为字段名，value 为新值）。

        返回:
            True 表示更新成功，False 表示未找到指定 ID。
        """
        mem = self.get_by_id(memory_id)
        if mem is None:
            return False
        for key, value in updates.items():
            if key in mem and key != "id":
                mem[key] = value
        self._flush()
        return True

    def delete(self, memory_id: str) -> bool:
        """
        永久删除一条记忆。

        返回:
            True 表示删除成功，False 表示未找到指定 ID。
        """
        for i, mem in enumerate(self._memories):
            if mem["id"] == memory_id:
                self._memories.pop(i)
                self._flush()
                return True
        return False

    def delete_many(self, memory_ids: List[str]) -> int:
        """
        批量删除记忆。

        返回:
            实际删除的条目数。
        """
        ids_set = set(memory_ids)
        before = len(self._memories)
        self._memories = [mem for mem in self._memories if mem["id"] not in ids_set]
        after = len(self._memories)
        deleted = before - after
        if deleted > 0:
            self._flush()
        return deleted

    def count(self, mem_type: Optional[str] = None) -> int:
        """
        返回指定类型的记忆数量。

        参数:
            mem_type: None 返回总数，或指定类型。
        """
        return len(self.get_all(mem_type))

    # ========== 内部持久化 ==========

    def _flush(self) -> None:
        """
        全量写入 self._memories 到 JSON 文件（原子写入 + 滚动备份）。
        """
        try:
            # 保留已有备份（如果存在）
            if self._file_path.exists():
                self._rotate_backups()

            # 原子写入
            tmp_path = self._file_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._memories, f, ensure_ascii=False, indent=2, default=str)

            os.replace(str(tmp_path), str(self._file_path))
            logger.debug(f"已持久化 {len(self._memories)} 条记忆到 {self._file_path}")
        except Exception as e:
            logger.error(f"持久化失败: {e}")

    def _rotate_backups(self) -> None:
        """
        滚动备份：.bak.2 → .bak.3，.bak.1 → .bak.2，当前文件 → .bak.1。
        """
        bak_pattern = str(self._file_path) + ".bak."
        # 删除最旧的 bak.3
        bak3 = Path(str(self._file_path) + ".bak.3")
        if bak3.exists():
            bak3.unlink()
        # 滚动
        for i in range(2, 0, -1):
            old_bak = Path(str(self._file_path) + f".bak.{i}")
            new_bak = Path(str(self._file_path) + f".bak.{i + 1}")
            if old_bak.exists():
                os.replace(str(old_bak), str(new_bak))
        # 将当前文件复制为 bak.1
        bak1 = Path(str(self._file_path) + ".bak.1")
        import shutil
        shutil.copy2(str(self._file_path), str(bak1))

    def _load(self) -> None:
        """
        从 JSON 文件加载记忆，包含损坏恢复和条目级校验。
        """
        raw = self._read_with_recovery()

        valid_memories = []
        for item in raw:
            try:
                validated = self._validate_item(item)
                valid_memories.append(validated)
            except Exception as e:
                logger.warning(f"跳过损坏条目: {e} | 条目内容: {str(item)[:200]}")

        self._memories = valid_memories
        logger.info(f"已加载 {len(self._memories)} 条记忆")

    def _read_with_recovery(self) -> list:
        """
        尝试读取 JSON 文件，失败时从备份恢复。
        """
        # 尝试主文件
        raw = self._try_read_json(self._file_path)
        if raw is not None:
            return raw

        logger.warning(f"记忆文件损坏或不存在: {self._file_path}，尝试从备份恢复")

        # 尝试备份文件（按优先级：bak.1 → bak.2 → bak.3）
        for i in range(1, 4):
            bak_path = Path(str(self._file_path) + f".bak.{i}")
            raw = self._try_read_json(bak_path)
            if raw is not None:
                logger.info(f"从备份 {bak_path} 恢复成功")
                return raw

        logger.warning("所有备份均无法恢复，初始化为空列表")
        return []

    @staticmethod
    def _try_read_json(path: Path) -> Optional[list]:
        """
        尝试读取并解析 JSON 文件。

        返回:
            解析后的列表，失败返回 None。
        """
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                raise ValueError("根元素不是列表")
            return raw
        except (json.JSONDecodeError, ValueError, OSError) as e:
            logger.warning(f"读取 {path} 失败: {e}")
            return None

    @staticmethod
    def _validate_item(item: Dict) -> Dict:
        """
        校验单条记忆数据，修复或补充缺失字段。

        校验规则（来自 memory_spec.md 第 4 节）:
            - id 缺失或为空 → 自动生成
            - type 不在枚举值 → 默认 "short"
            - importance 超出 0.0~1.0 → 截断到边界
            - timestamp 缺失或为 0 → 使用当前时间
            - tags 不是列表 → 默认 []

        返回:
            校验并修复后的记忆字典。

        异常:
            若 item 不是 dict，抛出 TypeError。
        """
        import uuid

        if not isinstance(item, dict):
            raise TypeError(f"记忆条目必须是字典，实际为 {type(item)}")

        # id
        if not isinstance(item.get("id"), str) or not item["id"]:
            item["id"] = uuid.uuid4().hex
            logger.debug(f"为条目生成新 ID: {item['id']}")

        # type
        valid_types = {"working", "short", "long"}
        if item.get("type") not in valid_types:
            logger.debug(f"修正无效 type: {item.get('type')} → 'short'")
            item["type"] = "short"

        # importance
        imp = item.get("importance", 0.5)
        if not isinstance(imp, (int, float)):
            imp = 0.5
        item["importance"] = max(0.0, min(1.0, float(imp)))

        # timestamp
        if not isinstance(item.get("timestamp"), (int, float)) or item["timestamp"] == 0:
            item["timestamp"] = int(time.time())

        # access_count
        if "access_count" not in item or not isinstance(item["access_count"], (int, float)):
            item["access_count"] = 0

        # last_access
        if not isinstance(item.get("last_access"), (int, float)):
            item["last_access"] = item["timestamp"]

        # tags
        if not isinstance(item.get("tags"), list):
            item["tags"] = []

        # metadata
        if not isinstance(item.get("metadata"), dict):
            item["metadata"] = {}

        return item