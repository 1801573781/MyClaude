"""物理存储管理器。

管理 Layer 1（MEMORY.md）和元数据层（metadata.json）的读写操作，
提供原子写入、倒排索引维护。
"""

import copy
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _atomic_write(filepath: Path, content: str, encoding: str = "utf-8") -> bool:
    """原子写入文件（tmp → rename）。

    在 Windows 上 os.replace() 可能因文件锁定失败，采用重试 + 降级策略。

    Args:
        filepath: 目标文件路径
        content: 文件内容
        encoding: 文件编码

    Returns:
        True 表示成功
    """
    tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")

    # 写入临时文件
    try:
        tmp_path.write_text(content, encoding=encoding)
    except Exception as e:
        logger.error(f"写入临时文件失败 {tmp_path}: {e}")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False

    # 原子替换（带重试）
    for attempt in range(3):
        try:
            os.replace(str(tmp_path), str(filepath))
            return True
        except PermissionError as e:
            logger.warning(f"os.replace 重试 {attempt + 1}/3: {e}")
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"os.replace 异常: {e}")
            break

    # 降级：先删除目标，再重命名
    logger.warning(f"原子替换失败，降级为先删后改: {filepath}")
    try:
        if filepath.exists():
            filepath.unlink()
        tmp_path.rename(filepath)
        return True
    except Exception as e:
        logger.error(f"降级写入也失败: {e}")
        return False


def _atomic_write_json(filepath: Path, data: Any) -> bool:
    """原子写入 JSON 文件。"""
    content = json.dumps(data, ensure_ascii=False, indent=2)
    return _atomic_write(filepath, content)


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（字符数 / 2.5）。"""
    return int(len(text) / 2.5)


def _count_lines(text: str) -> int:
    """统计行数。"""
    if not text:
        return 0
    return text.count("\n") + 1


class MemoryStore:
    """物理存储管理器。

    管理以下文件：
    - Layer 1: {base_dir}/memory/MEMORY.md（索引层 Markdown）
    - 元数据: {base_dir}/memory/metadata.json
    """

    # metadata.json 的默认结构
    _DEFAULT_METADATA = {
        "version": 1,
        "entries": {},
        "inverted_index": {"tags": {}, "files": {}, "entities": {}},
        "entity_aliases": {},
        "compaction_logs": [],
        "evolution_logs": [],
        "session_checkpoints": [],
    }

    def __init__(self, mem_config: Any):
        """初始化存储管理器。

        Args:
            mem_config: memory_ex.yaml 配置对象
        """
        storage = mem_config.storage
        self._base_dir = Path(storage.base_dir)
        self._layer1_path = self._base_dir / storage.layer1_file
        self._metadata_path = self._base_dir / storage.metadata_file

        # 水位配置
        wm = mem_config.watermarks
        self._wm_warning = int(getattr(wm, "warning", 150))
        self._wm_trigger = int(getattr(wm, "trigger", 180))
        self._wm_hard_limit = int(getattr(wm, "hard_limit", 200))
        self._wm_target_after = int(getattr(wm, "target_after", 160))

        # 初始化目录和文件
        self._ensure_dirs()
        self._ensure_files()

        # 清理可能残留的 .tmp 文件
        self._cleanup_tmp_files()

        # 加载元数据到内存缓存
        self._metadata_cache: Dict = self._load_metadata()

    # ===== 初始化 =====

    def _ensure_dirs(self):
        """创建所需目录。"""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        # 确保 layer1_file 和 metadata_file 的父目录（memory/）存在
        self._layer1_path.parent.mkdir(parents=True, exist_ok=True)
        self._metadata_path.parent.mkdir(parents=True, exist_ok=True)

    def _ensure_files(self):
        """创建所需文件（如果不存在）。"""
        if not self._layer1_path.exists():
            self._layer1_path.write_text("", encoding="utf-8")
        if not self._metadata_path.exists():
            _atomic_write_json(self._metadata_path, copy.deepcopy(self._DEFAULT_METADATA))

    def _cleanup_tmp_files(self):
        """清理上次崩溃可能残留的 .tmp 文件。"""
        for p in self._base_dir.glob("*.tmp"):
            try:
                p.unlink()
                logger.info(f"清理残留临时文件: {p}")
            except Exception as e:
                logger.warning(f"清理临时文件失败 {p}: {e}")

    def _load_metadata(self) -> Dict:
        """加载 metadata.json 到内存。"""
        try:
            content = self._metadata_path.read_text(encoding="utf-8")
            if content.strip():
                return json.loads(content)
        except Exception as e:
            logger.warning(f"加载 metadata.json 失败，使用默认值: {e}")

        return copy.deepcopy(self._DEFAULT_METADATA)

    # ===== Layer 1 操作 =====

    def read_layer1(self) -> str:
        """读取 Layer 1（MEMORY.md）内容。"""
        try:
            return self._layer1_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"读取 Layer 1 失败: {e}")
            return ""

    def write_layer1(self, content: str) -> bool:
        """原子写入 Layer 1（先备份再替换）。"""
        # 备份
        bak_path = self._layer1_path.with_suffix(".md.bak")
        if self._layer1_path.exists():
            try:
                bak_path.write_text(
                    self._layer1_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
            except Exception as e:
                logger.warning(f"Layer 1 备份失败: {e}")

        return _atomic_write(self._layer1_path, content)

    def get_layer1_stats(self) -> Dict[str, int]:
        """获取 Layer 1 的条数、行数和 token 估算。"""
        content = self.read_layer1()
        entries = sum(1 for line in content.split("\n") if line.strip().startswith("- "))
        return {
            "entries": entries,
            "lines": _count_lines(content),
            "tokens": _estimate_tokens(content),
        }

    def check_water_level(self) -> Tuple[bool, bool, bool]:
        """检查 Layer 1 水位。

        Returns:
            (warning, trigger, hard_limit) 三个布尔值
        """
        stats = self.get_layer1_stats()
        lines = stats["lines"]
        tokens = stats["tokens"]

        warning = lines >= self._wm_warning or tokens >= _estimate_tokens("x" * 1500)
        trigger = lines >= self._wm_trigger or tokens >= _estimate_tokens("x" * 1700)
        hard_limit = lines >= self._wm_hard_limit or tokens >= _estimate_tokens("x" * 2000)

        # 更精确的 token 比较
        warning = lines >= self._wm_warning or tokens >= int(1500)
        trigger = lines >= self._wm_trigger or tokens >= int(1700)
        hard_limit = lines >= self._wm_hard_limit or tokens >= int(2000)

        return warning, trigger, hard_limit

    # ===== 元数据操作 =====

    def save_metadata(self) -> bool:
        """持久化元数据到磁盘（原子写入）。"""
        return _atomic_write_json(self._metadata_path, self._metadata_cache)

    def get_metadata_entry(self, entry_id: str) -> Optional[Dict]:
        """获取条目的元数据。"""
        return self._metadata_cache.get("entries", {}).get(entry_id)

    def update_metadata_entry(self, entry_id: str, **fields) -> bool:
        """更新条目的元数据字段。"""
        entries = self._metadata_cache.setdefault("entries", {})
        if entry_id not in entries:
            entries[entry_id] = {
                "tags": [],
                "status": "unprocessed",
                "is_consumed": False,
                "is_evolved": False,
                "created_at": datetime.now().isoformat(),
                "last_accessed": datetime.now().isoformat(),
                "access_count": 0,
                "importance_score": None,
            }
        entries[entry_id].update(fields)
        return True

    def remove_metadata_entry(self, entry_id: str) -> bool:
        """从元数据中移除条目（归档时调用）。"""
        entries = self._metadata_cache.get("entries", {})
        if entry_id in entries:
            del entries[entry_id]
            return True
        return False

    def add_compaction_log(self, log_entry: Dict) -> None:
        """添加整理日志（保留最近 20 条）。"""
        logs = self._metadata_cache.setdefault("compaction_logs", [])
        logs.append(log_entry)
        if len(logs) > 20:
            self._metadata_cache["compaction_logs"] = logs[-20:]

    def add_evolution_log(self, log_entry: Dict) -> None:
        """添加进化日志（保留最近 20 条）。"""
        logs = self._metadata_cache.setdefault("evolution_logs", [])
        logs.append(log_entry)
        if len(logs) > 20:
            self._metadata_cache["evolution_logs"] = logs[-20:]

    def add_session_checkpoint(self, checkpoint: Dict) -> None:
        """添加 Session 检查点。"""
        checkpoints = self._metadata_cache.setdefault("session_checkpoints", [])
        checkpoints.append(checkpoint)

    # ===== 倒排索引 =====

    def update_inverted_index(self, entry_id: str, tags: List[str], content: str) -> None:
        """更新倒排索引。

        Args:
            entry_id: 条目 ID
            tags: 主题标签列表
            content: 条目内容（用于提取文件名和实体）
        """
        inv_index = self._metadata_cache.setdefault("inverted_index", {})
        tags_index = inv_index.setdefault("tags", {})
        files_index = inv_index.setdefault("files", {})
        entities_index = inv_index.setdefault("entities", {})

        # tags 索引
        for tag in tags:
            if tag not in tags_index:
                tags_index[tag] = []
            if entry_id not in tags_index[tag]:
                tags_index[tag].append(entry_id)

        # files 索引（从 content 中提取文件名）
        file_pattern = re.compile(r"[\w/\\]+\.\w{1,5}")
        for match in file_pattern.finditer(content):
            filename = match.group()
            if filename not in files_index:
                files_index[filename] = []
            if entry_id not in files_index[filename]:
                files_index[filename].append(entry_id)

        # entities 索引（从 content 中提取技术实体）
        entities = self._extract_entities(content)
        for entity in entities:
            if entity not in entities_index:
                entities_index[entity] = []
            if entry_id not in entities_index[entity]:
                entities_index[entity].append(entry_id)

    def search_inverted_index(self, keywords: List[str]) -> List[str]:
        """通过倒排索引搜索匹配的条目 ID。

        Args:
            keywords: 关键词列表

        Returns:
            匹配的条目 ID 列表
        """
        inv_index = self._metadata_cache.get("inverted_index", {})
        matched_ids = set()

        for keyword in keywords:
            # 搜索 tags
            for tag, ids in inv_index.get("tags", {}).items():
                if keyword.lower() in tag.lower():
                    matched_ids.update(ids)

            # 搜索 files
            for filename, ids in inv_index.get("files", {}).items():
                if keyword.lower() in filename.lower():
                    matched_ids.update(ids)

            # 搜索 entities
            for entity, ids in inv_index.get("entities", {}).items():
                if keyword.lower() in entity.lower():
                    matched_ids.update(ids)

            # 实体别名解析
            aliases = self._metadata_cache.get("entity_aliases", {})
            for alias, canonical in aliases.items():
                if keyword.lower() in alias.lower():
                    matched_ids.update(inv_index.get("entities", {}).get(canonical, []))

        return list(matched_ids)

    def _extract_entities(self, content: str) -> List[str]:
        """从内容中提取技术实体。

        简化实现：提取大写开头的英文单词（≥3 字符）和常见技术名词。
        """
        entities = set()

        # 大写开头的英文词
        for match in re.finditer(r"\b[A-Z][a-zA-Z]{2,}\b", content):
            entities.add(match.group())

        # 常见技术名词
        tech_keywords = [
            "PostgreSQL", "MySQL", "SQLite", "Redis", "MongoDB",
            "FastAPI", "Flask", "Django", "OpenAI", "MiniMax", "DeepSeek",
            "Python", "JavaScript", "TypeScript", "Docker", "Kubernetes",
            "Rich", "PyYAML", "pytest", "numpy", "pandas",
        ]
        for kw in tech_keywords:
            if kw in content:
                entities.add(kw)

        return list(entities)

    # ===== 实体别名 =====

    def resolve_entity(self, name: str) -> str:
        """解析实体别名，返回标准名称。

        用于提取器在写入时进行实体规范化。
        """
        aliases = self._metadata_cache.get("entity_aliases", {})
        return aliases.get(name, name)

    def add_entity_alias(self, alias: str, canonical: str) -> None:
        """添加实体别名映射（由进化模块的 RESOLVED 操作维护）。"""
        aliases = self._metadata_cache.setdefault("entity_aliases", {})
        aliases[alias] = canonical

    # ===== 统计与维护 =====

    def get_stats(self) -> Dict:
        """获取记忆系统统计信息。"""
        layer1_stats = self.get_layer1_stats()

        metadata_entries = self._metadata_cache.get("entries", {})
        unconsumed = sum(
            1 for m in metadata_entries.values()
            if m.get("status") == "unprocessed" and not m.get("is_consumed", False)
        )
        unevolved = sum(
            1 for m in metadata_entries.values()
            if m.get("status") == "unprocessed" and not m.get("is_evolved", False)
        )

        return {
            "backend": "memory_ex",
            "layer1_entries": layer1_stats["entries"],
            "layer1_lines": layer1_stats["lines"],
            "layer1_tokens": layer1_stats["tokens"],
            "metadata_entries": len(metadata_entries),
            "unconsumed": unconsumed,
            "unevolved": unevolved,
            "compaction_logs": len(self._metadata_cache.get("compaction_logs", [])),
            "evolution_logs": len(self._metadata_cache.get("evolution_logs", [])),
        }

    def maintain(self) -> int:
        """执行轻量维护：清理过期条目。

        Returns:
            维护操作的计数
        """
        count = 0

        # 清理过期条目（status=unprocessed 且 is_consumed=True 且 is_evolved=True
        # 且超过 30 天未访问）
        now = datetime.now()
        entries_to_remove = []
        for entry_id, meta in self._metadata_cache.get("entries", {}).items():
            if (
                meta.get("status") == "unprocessed"
                and meta.get("is_consumed", False)
                and meta.get("is_evolved", False)
            ):
                last_accessed_str = meta.get("last_accessed", "")
                try:
                    last_accessed = datetime.fromisoformat(last_accessed_str)
                    if (now - last_accessed).days > 30:
                        entries_to_remove.append(entry_id)
                except (ValueError, TypeError):
                    pass

        for entry_id in entries_to_remove:
            self.update_metadata_entry(entry_id, status="processed")
            self.remove_metadata_entry(entry_id)
            count += 1

        if entries_to_remove:
            self.save_metadata()

        # 检查 metadata.json 体积
        try:
            meta_size = self._metadata_path.stat().st_size
            if meta_size > 500 * 1024:  # 500KB
                logger.warning(f"metadata.json 超过 500KB ({meta_size} bytes)，建议全量压缩")
        except Exception:
            pass

        return count

    def clear_all(self) -> dict:
        """清空所有层。

        Returns:
            清除统计字典，包含各层清除的条目数等信息
        """
        # 在清除前统计 Layer 1 记忆条数（按 "- " 开头计数，匹配 extract 写入格式）
        layer1_content = self.read_layer1()
        layer1_entries = sum(
            1 for line in layer1_content.split("\n")
            if line.strip().startswith("- ")
        )

        # 清空 Layer 1
        self._layer1_path.write_text("", encoding="utf-8")

        # 清空元数据
        self._metadata_cache = copy.deepcopy(self._DEFAULT_METADATA)
        _atomic_write_json(self._metadata_path, self._metadata_cache)

        return {
            "layer1_entries": layer1_entries,
        }

    def get_watermarks(self) -> Dict[str, int]:
        """获取水位配置。"""
        return {
            "warning": self._wm_warning,
            "trigger": self._wm_trigger,
            "hard_limit": self._wm_hard_limit,
            "target_after": self._wm_target_after,
        }

    def get_query_count_since_last_compaction(self) -> int:
        """获取自上次整理以来的 Query 数量。

        Returns:
            0（Layer 0 已废弃，此方法保留仅为接口兼容）
        """
        return 0
