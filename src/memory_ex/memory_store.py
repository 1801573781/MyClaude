"""物理存储管理器。

管理 Layer 0（JSONL）、Layer 1（MEMORY.md）、元数据层（metadata.json）、
归档（archive）的读写操作，提供原子写入、倒排索引维护。
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
    - Layer 0: {base_dir}/memory.jsonl（追加式 JSONL）
    - Layer 1: {base_dir}/MEMORY.md（索引层 Markdown）
    - 元数据: {base_dir}/metadata.json
    - 归档: {base_dir}/archive/（Layer 0 归档文件）
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
        self._layer0_path = self._base_dir / storage.layer0_file
        self._layer0_md_path = self._base_dir / getattr(
            storage, "layer0_md_file", "raw_memory.md"
        )
        self._layer1_path = self._base_dir / storage.layer1_file
        self._metadata_path = self._base_dir / storage.metadata_file
        self._archive_dir = self._base_dir / storage.archive_dir

        # 水位配置
        wm = mem_config.watermarks
        self._wm_warning = int(getattr(wm, "warning", 150))
        self._wm_trigger = int(getattr(wm, "trigger", 180))
        self._wm_hard_limit = int(getattr(wm, "hard_limit", 200))
        self._wm_target_after = int(getattr(wm, "target_after", 160))

        # 归档配置
        arch = mem_config.archive
        self._archive_trigger_lines = int(getattr(arch, "trigger_lines", 1000))
        self._archive_max_files = int(getattr(arch, "max_archive_files", 5))

        # 初始化目录和文件
        self._ensure_dirs()
        self._ensure_files()

        # 清理可能残留的 .tmp 文件
        self._cleanup_tmp_files()

        # 加载元数据到内存缓存
        self._metadata_cache: Dict = self._load_metadata()

        # 全局序号
        self._seq_counter = self._calc_next_seq()

    # ===== 初始化 =====

    def _ensure_dirs(self):
        """创建所需目录。"""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_files(self):
        """创建所需文件（如果不存在），并迁移旧文件名。"""
        # 旧文件名迁移：memory.jsonl → raw_memory.jsonl
        old_layer0 = self._base_dir / "memory.jsonl"
        if old_layer0.exists() and not self._layer0_path.exists():
            old_layer0.rename(self._layer0_path)
            logger.info(f"已迁移 {old_layer0.name} → {self._layer0_path.name}")

        if not self._layer0_path.exists():
            self._layer0_path.write_text("", encoding="utf-8")
        if not self._layer0_md_path.exists():
            self._layer0_md_path.write_text("", encoding="utf-8")
        if not self._layer1_path.exists():
            self._layer1_path.write_text("", encoding="utf-8")
        if not self._metadata_path.exists():
            _atomic_write_json(self._metadata_path, copy.deepcopy(self._DEFAULT_METADATA))

        # 如果 MD 文件为空但 JSONL 有内容，重建 MD
        if self._layer0_md_path.stat().st_size == 0 and self._layer0_path.stat().st_size > 0:
            self._rebuild_layer0_md()

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

    def _calc_next_seq(self) -> int:
        """计算下一个序号（基于现有 Layer 0 的最大序号）。"""
        max_seq = 0
        for entry in self.iter_layer0():
            entry_id = entry.get("id", "")
            # 格式: m_{timestamp}_{seq}
            parts = entry_id.rsplit("_", 1)
            if len(parts) == 2:
                try:
                    seq = int(parts[1])
                    if seq > max_seq:
                        max_seq = seq
                except ValueError:
                    pass
        return max_seq + 1

    # ===== Layer 0 Markdown（人类可读副本）=====

    @staticmethod
    def _format_entry_for_md(entry: Dict) -> str:
        """将 JSONL 条目格式化为人类可读的 Markdown 片段。

        格式设计：
        - 以 --- 分隔不同条目
        - 显示 ID、时间戳、标签、来源、状态、类型、关联错题
        - content 原样输出（保留内部换行）
        """
        lines = []
        eid = entry.get("id", "")
        timestamp = entry.get("timestamp", "")
        tags = entry.get("tags", [])
        source = entry.get("source", "")
        status = entry.get("status", "")
        content = entry.get("content", "")
        entry_type = entry.get("type", "reference")
        source_problem_id = entry.get("source_problem_id")

        lines.append("---")
        lines.append(f"**ID:** {eid}")
        lines.append(f"**时间:** {timestamp}")
        if tags:
            tags_str = ", ".join(f"`{t}`" for t in tags)
            lines.append(f"**标签:** {tags_str}")
        meta_parts = [f"**来源:** {source}", f"**状态:** {status}", f"**类型:** {entry_type}"]
        if source_problem_id:
            meta_parts.append(f"**关联错题:** {source_problem_id}")
        lines.append("  |  ".join(meta_parts))
        lines.append("")
        lines.append(content)
        lines.append("")  # 条目尾部空行，方便阅读
        return "\n".join(lines)

    def _append_layer0_md(self, entry: Dict) -> None:
        """将单条记录追加到 raw_memory.md。"""
        md_line = self._format_entry_for_md(entry)
        try:
            with open(self._layer0_md_path, "a", encoding="utf-8") as f:
                f.write(md_line + "\n")
        except Exception as e:
            logger.error(f"写入 Layer 0 MD 失败: {e}")

    def _rebuild_layer0_md(self) -> None:
        """从 JSONL 重建 raw_memory.md（用于迁移或修复）。"""
        entries = self.iter_layer0()
        if not entries:
            _atomic_write(self._layer0_md_path, "")
            return

        parts = [self._format_entry_for_md(e) for e in entries]
        content = "\n".join(parts)
        _atomic_write(self._layer0_md_path, content)

    # ===== Layer 0 操作 =====

    def add_raw(self, role: str, content: str, metadata: Optional[dict] = None) -> str:
        """将原始对话内容写入 Layer 0（status=raw）。

        不调用 LLM，仅做轻量追加写入。

        Args:
            role: 角色（通常为空字符串或 "user"）
            content: 原始对话内容
            metadata: 附加元数据（turn, has_tools, user_input, llm_response 等）

        Returns:
            新条目的 ID
        """
        now = datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        entry_id = f"m_{timestamp_str}_{self._seq_counter}"
        self._seq_counter += 1

        iso_timestamp = now.isoformat()

        entry = {
            "id": entry_id,
            "timestamp": iso_timestamp,
            "session_id": (metadata or {}).get("session_id", ""),
            "query_id": (metadata or {}).get("query_id", 0),
            "tags": [],
            "content": content,
            "source": "raw_turn",
            "status": "raw",
            "compacted": False,
            "evolved": False,
            "metadata": {
                "created_at": iso_timestamp,
                "last_accessed": iso_timestamp,
                "access_count": 0,
                **(metadata or {}),
            },
        }

        # 追加到 JSONL
        line = json.dumps(entry, ensure_ascii=False)
        try:
            with open(self._layer0_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.error(f"写入 Layer 0 失败: {e}")
            return ""

        # 同步追加到 raw_memory.md（人类可读副本）
        self._append_layer0_md(entry)

        # 更新元数据缓存
        self._metadata_cache.setdefault("entries", {})[entry_id] = {
            "tags": [],
            "status": "raw",
            "is_consumed": False,
            "is_evolved": False,
            "created_at": iso_timestamp,
            "last_accessed": iso_timestamp,
            "access_count": 0,
            "importance_score": None,
        }

        return entry_id

    def get_layer0_entry(self, entry_id: str) -> Optional[Dict]:
        """从 Layer 0 按 ID 获取单条记忆。"""
        for entry in self.iter_layer0():
            if entry.get("id") == entry_id:
                return entry
        return None

    def iter_layer0(self) -> List[Dict]:
        """遍历 Layer 0 全部条目。

        向后兼容：旧记录没有 type 和 source_problem_id 字段，
        读取时默认填充为 "reference" 和 None。
        """
        entries = []
        try:
            content = self._layer0_path.read_text(encoding="utf-8")
            for line in content.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    # 向后兼容：补充 type 和 source_problem_id
                    if "type" not in entry:
                        entry["type"] = "reference"
                    if "source_problem_id" not in entry:
                        entry["source_problem_id"] = None
                    entries.append(entry)
                except json.JSONDecodeError as e:
                    logger.warning(f"Layer 0 JSONL 解析错误: {e}")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(f"读取 Layer 0 失败: {e}")

        return entries

    def get_raw_entries(self) -> List[Dict]:
        """获取所有 status=raw 的条目。"""
        return [e for e in self.iter_layer0() if e.get("status") == "raw"]

    def get_unprocessed_entries(self) -> List[Dict]:
        """获取所有 status=unprocessed 的条目。"""
        return [e for e in self.iter_layer0() if e.get("status") == "unprocessed"]

    def get_unconsumed_entries(self) -> List[Dict]:
        """获取未被整理消费的 unprocessed 条目（is_consumed=False）。"""
        results = []
        for entry in self.iter_layer0():
            if entry.get("status") != "unprocessed":
                continue
            meta = self._metadata_cache.get("entries", {}).get(entry["id"], {})
            if not meta.get("is_consumed", False):
                results.append(entry)
        return results

    def get_unevolved_entries(self) -> List[Dict]:
        """获取未被进化消费的 unprocessed 条目（is_evolved=False）。"""
        results = []
        for entry in self.iter_layer0():
            if entry.get("status") != "unprocessed":
                continue
            meta = self._metadata_cache.get("entries", {}).get(entry["id"], {})
            if not meta.get("is_evolved", False):
                results.append(entry)
        return results

    def update_layer0_entry(self, entry_id: str, updates: Dict) -> bool:
        """更新 Layer 0 中的条目字段。

        由于 JSONL 是追加式的，此操作需要重写整个文件。

        Args:
            entry_id: 条目 ID
            updates: 要更新的字段字典

        Returns:
            True 表示成功
        """
        entries = self.iter_layer0()
        found = False
        for i, entry in enumerate(entries):
            if entry.get("id") == entry_id:
                entries[i].update(updates)
                found = True
                break

        if not found:
            return False

        # 重写整个 Layer 0
        lines = [json.dumps(e, ensure_ascii=False) for e in entries]
        content = "\n".join(lines) + ("\n" if lines else "")
        success = _atomic_write(self._layer0_path, content)
        if success:
            self._rebuild_layer0_md()
        return success

    def batch_update_layer0(self, updates_map: Dict[str, Dict]) -> bool:
        """批量更新 Layer 0 条目。

        Args:
            updates_map: {entry_id: update_dict}

        Returns:
            True 表示成功
        """
        if not updates_map:
            return True

        entries = self.iter_layer0()
        for entry in entries:
            eid = entry.get("id")
            if eid in updates_map:
                entry.update(updates_map[eid])

        lines = [json.dumps(e, ensure_ascii=False) for e in entries]
        content = "\n".join(lines) + ("\n" if lines else "")
        success = _atomic_write(self._layer0_path, content)
        if success:
            self._rebuild_layer0_md()
        return success

    # ===== Layer 0 归档 =====

    def check_and_archive(self) -> Optional[str]:
        """检查 Layer 0 行数，超过阈值则归档。

        Returns:
            归档文件路径（如果执行了归档），否则 None
        """
        line_count = 0
        try:
            line_count = sum(
                1 for line in self._layer0_path.read_text(encoding="utf-8").split("\n")
                if line.strip()
            )
        except Exception:
            pass

        if line_count < self._archive_trigger_lines:
            return None

        return self.archive_layer0()

    def archive_layer0(self) -> str:
        """执行 Layer 0 归档。

        将当前 raw_memory.jsonl 重命名为 raw_memory_archive_{date}.jsonl，
        移入 archive/ 目录，创建新的空 raw_memory.jsonl。
        同时归档 raw_memory.md。
        保留最近 max_archive_files 个归档文件。
        """
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 归档 JSONL
        archive_name = f"raw_memory_archive_{date_str}.jsonl"
        archive_path = self._archive_dir / archive_name
        try:
            self._layer0_path.rename(archive_path)
        except Exception as e:
            logger.error(f"归档 Layer 0 失败: {e}")
            return ""

        # 归档 MD
        if self._layer0_md_path.exists():
            archive_md_name = f"raw_memory_archive_{date_str}.md"
            archive_md_path = self._archive_dir / archive_md_name
            try:
                self._layer0_md_path.rename(archive_md_path)
            except Exception as e:
                logger.warning(f"归档 Layer 0 MD 失败: {e}")

        # 创建新的空文件
        self._layer0_path.write_text("", encoding="utf-8")
        self._layer0_md_path.write_text("", encoding="utf-8")

        # 清理过期归档（同时清理 jsonl 和 md）
        archives = sorted(self._archive_dir.glob("raw_memory_archive_*.jsonl"))
        while len(archives) > self._archive_max_files:
            oldest = archives.pop(0)
            try:
                oldest.unlink()
                logger.info(f"删除过期归档: {oldest}")
            except Exception as e:
                logger.warning(f"删除过期归档失败: {e}")
            # 同步删除对应的 md 归档
            md_to_del = oldest.with_suffix(".md")
            if md_to_del.exists():
                try:
                    md_to_del.unlink()
                except Exception:
                    pass

        logger.info(f"Layer 0 已归档至 {archive_path}")
        return str(archive_path)

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
        all_entries = self.iter_layer0()
        raw_count = sum(1 for e in all_entries if e.get("status") == "raw")
        unprocessed_count = sum(1 for e in all_entries if e.get("status") == "unprocessed")
        processed_count = sum(1 for e in all_entries if e.get("status") == "processed")

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
            "layer0_total": len(all_entries),
            "layer0_raw": raw_count,
            "layer0_unprocessed": unprocessed_count,
            "layer0_processed": processed_count,
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
        """执行轻量维护：检查归档、清理过期条目。

        Returns:
            维护操作的计数
        """
        count = 0

        # 检查 Layer 0 归档
        if self.check_and_archive():
            count += 1

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
        # 在清除前收集所有统计信息（避免清除后数据丢失）
        stats = self.get_stats()

        # 在清除前统计 Layer 1 记忆条数（按 "- " 开头计数，匹配 extract 写入格式）
        layer1_content = self.read_layer1()
        layer1_entries = sum(
            1 for line in layer1_content.split("\n")
            if line.strip().startswith("- ")
        )

        # 清空 Layer 0
        self._layer0_path.write_text("", encoding="utf-8")

        # 清空 Layer 0 MD
        self._layer0_md_path.write_text("", encoding="utf-8")

        # 清空 Layer 1
        self._layer1_path.write_text("", encoding="utf-8")

        # 清空元数据
        self._metadata_cache = copy.deepcopy(self._DEFAULT_METADATA)
        _atomic_write_json(self._metadata_path, self._metadata_cache)

        self._seq_counter = 1

        return {
            "layer0_total": stats.get("layer0_total", 0),
            "layer0_raw": stats.get("layer0_raw", 0),
            "layer0_unprocessed": stats.get("layer0_unprocessed", 0),
            "layer0_processed": stats.get("layer0_processed", 0),
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

        通过统计 raw 条目的 query_id 差值来估算。
        简化实现：统计 unprocessed 条目数。
        """
        unprocessed = self.get_unprocessed_entries()
        if not unprocessed:
            return 0

        # 统计不同的 query_id
        query_ids = set()
        for entry in unprocessed:
            qid = entry.get("query_id", 0)
            if qid:
                query_ids.add(qid)

        return len(query_ids)
