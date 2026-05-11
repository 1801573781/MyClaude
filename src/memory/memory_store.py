"""
MemoryStore：基于 JSON 文件的物理存储层。

提供记忆条目的 CRUD、原子写入、滚动备份、损坏恢复等功能。
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


class MemoryStore:
    """JSON 文件持久化存储，管理记忆条目的物理读写。"""

    def __init__(self, storage_path: str):
        """
        初始化 MemoryStore，加载持久化文件。

        参数:
            storage_path: 存储目录的绝对路径（如 D:/AI/MyClaude/.memdir）。
        """
        self._storage_dir = Path(storage_path)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._storage_dir / "memories.json"
        self._memories: List[Dict] = []
        self._load()

    # ==================== 公开 CRUD ====================

    def add(self, memory: Dict) -> str:
        """
        添加一条记忆并持久化。

        参数:
            memory: 经过校验的记忆字典。

        返回:
            新记忆的 id 字符串。
        """
        if not memory.get("id"):
            memory["id"] = uuid.uuid4().hex
        now = int(time.time())
        if not memory.get("timestamp"):
            memory["timestamp"] = now
        if not memory.get("last_access"):
            memory["last_access"] = now
        memory.setdefault("access_count", 0)
        memory.setdefault("importance", 0.5)
        memory.setdefault("tags", [])
        memory.setdefault("metadata", {})

        self._memories.append(memory)
        self._flush()
        logger.info(f"添加记忆 {memory['id']} (type={memory.get('type')})")
        return memory["id"]

    def get(self, memory_id: str) -> Optional[Dict]:
        """
        根据 ID 获取单条记忆（返回引用，修改后需手动 flush）。

        参数:
            memory_id: 记忆 ID。

        返回:
            记忆字典，未找到返回 None。
        """
        for mem in self._memories:
            if mem["id"] == memory_id:
                return mem
        return None

    def get_all(self, mem_type: Optional[str] = None) -> List[Dict]:
        """
        返回所有指定类型的记忆。

        参数:
            mem_type: None 返回全部，或指定 "working"/"short"/"long"。

        返回:
            记忆字典列表（新列表，非内部引用）。
        """
        if mem_type is None:
            return list(self._memories)
        return [mem for mem in self._memories if mem.get("type") == mem_type]

    def update(self, memory_id: str,
               content: Optional[str] = None,
               importance: Optional[float] = None,
               tags: Optional[List[str]] = None) -> bool:
        """
        更新已有记忆的部分字段。

        参数:
            memory_id:  目标记忆 ID。
            content:    新内容（None 表示不修改）。
            importance: 新重要性（None 表示不修改）。
            tags:       新标签列表（None 表示不修改）。

        返回:
            True 表示更新成功，False 表示未找到指定 ID。
        """
        mem = self.get(memory_id)
        if mem is None:
            return False
        if content is not None:
            mem["content"] = content
        if importance is not None:
            mem["importance"] = max(0.0, min(1.0, importance))
        if tags is not None:
            mem["tags"] = tags
        self._flush()
        logger.info(f"更新记忆 {memory_id}")
        return True

    def delete(self, memory_id: str) -> bool:
        """
        永久删除一条记忆。

        参数:
            memory_id: 目标记忆 ID。

        返回:
            True 表示删除成功，False 表示未找到。
        """
        for i, mem in enumerate(self._memories):
            if mem["id"] == memory_id:
                self._memories.pop(i)
                self._flush()
                logger.info(f"删除记忆 {memory_id}")
                return True
        return False

    def delete_batch(self, memory_ids: List[str]) -> int:
        """
        批量删除记忆。

        参数:
            memory_ids: 待删除的记忆 ID 列表。

        返回:
            实际删除的条目数量。
        """
        ids_set = set(memory_ids)
        original_len = len(self._memories)
        self._memories = [mem for mem in self._memories if mem["id"] not in ids_set]
        deleted = original_len - len(self._memories)
        if deleted > 0:
            self._flush()
            logger.info(f"批量删除 {deleted} 条记忆")
        return deleted

    def count(self, mem_type: Optional[str] = None) -> int:
        """返回指定类型的记忆数量。"""
        return len(self.get_all(mem_type))

    # ==================== 持久化 ====================

    def _flush(self):
        """
        全量序列化写入磁盘（原子写入 + 滚动备份）。
        """
        self._backup()

        temp_path = self._file_path.with_suffix(".json.tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self._memories, f, ensure_ascii=False, indent=2, default=str)
            # Windows 上 os.replace 保证原子性
            os.replace(str(temp_path), str(self._file_path))
        except Exception as e:
            logger.error(f"写入记忆文件失败: {e}")
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def _backup(self):
        """滚动备份：保留 .bak.1 ~ .bak.3。"""
        if not self._file_path.exists():
            return

        # 滚动已有的备份文件
        for i in range(3, 0, -1):
            old_bak = self._storage_dir / f"memories.json.bak.{i}"
            if i == 3:
                old_bak.unlink(missing_ok=True)
            else:
                new_bak = self._storage_dir / f"memories.json.bak.{i + 1}"
                if old_bak.exists():
                    shutil.move(str(old_bak), str(new_bak))

        # 复制当前文件为 .bak.1
        shutil.copy2(str(self._file_path),
                     str(self._storage_dir / "memories.json.bak.1"))

    # ==================== 加载与恢复 ====================

    def _load(self):
        """
        启动时全量加载，含损坏恢复逻辑。
        """
        raw = self._read_with_recovery()
        self._memories = []
        for item in raw:
            try:
                validated = self._validate_item(item)
                self._memories.append(validated)
            except Exception as e:
                logger.warning(f"跳过损坏条目: {e}")

        logger.info(f"加载完成：共 {len(self._memories)} 条记忆")

    def _read_with_recovery(self) -> list:
        """尝试读取 JSON 文件，失败时从备份恢复。"""
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                raise ValueError("根元素不是列表")
            return raw
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"记忆文件损坏或不存在: {e}")

            # 尝试从备份恢复
            backups = sorted(
                self._storage_dir.glob("memories.json.bak.*"),
                key=lambda p: int(p.suffix.split(".")[-1])
            )
            for bak_path in backups:
                try:
                    with open(bak_path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    if isinstance(raw, list):
                        logger.info(f"从备份 {bak_path} 恢复成功")
                        return raw
                except Exception:
                    continue

            logger.warning("所有备份恢复失败，初始化为空列表")
            return []

    def _validate_item(self, item: Dict) -> Dict:
        """
        条目级校验，见 4. 数据结构 - 导入时的数据校验规则。

        修复常见问题并返回合法条目。
        """
        validated = dict(item)

        # id: 缺失或空 → 自动生成
        if not isinstance(validated.get("id"), str) or not validated["id"].strip():
            validated["id"] = uuid.uuid4().hex
            logger.warning(f"记忆缺少有效 id，已生成: {validated['id']}")

        # content: 确保存在
        if "content" not in validated:
            validated["content"] = ""
            logger.warning(f"记忆 {validated['id']} 缺少 content 字段")

        # type: 枚举校验
        if validated.get("type") not in ("working", "short", "long"):
            logger.warning(f"记忆 {validated['id']} type 无效 ({validated.get('type')})，设为 'short'")
            validated["type"] = "short"

        # importance: 范围截断
        importance = validated.get("importance", 0.5)
        if not isinstance(importance, (int, float)):
            importance = 0.5
        validated["importance"] = max(0.0, min(1.0, float(importance)))

        # timestamp: 缺失或 0 → 当前时间
        ts = validated.get("timestamp", 0)
        if not isinstance(ts, (int, float)) or ts <= 0:
            validated["timestamp"] = int(time.time())

        # access_count
        validated.setdefault("access_count", 0)

        # last_access
        la = validated.get("last_access", 0)
        if not isinstance(la, (int, float)) or la <= 0:
            validated["last_access"] = validated["timestamp"]

        # tags: 不是列表 → 空列表
        if not isinstance(validated.get("tags"), list):
            validated["tags"] = []
            logger.warning(f"记忆 {validated['id']} tags 不是列表，设为 []")

        # metadata: 不是字典 → 空字典
        if not isinstance(validated.get("metadata"), dict):
            validated["metadata"] = {}
            logger.warning(f"记忆 {validated['id']} metadata 不是字典，设为 {{}}")

        return validated

    def rebuild_from_list(self, memories: List[Dict]):
        """用给定的记忆列表替换内部数据并持久化（用于测试恢复）。"""
        self._memories = memories
        self._flush()