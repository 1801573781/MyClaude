"""BugStore — Bug库存储层。

管理 .jsonl 和 .md 文件的双写、按模块分文件存储、归档管理。
"""

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterator


@dataclass
class BugRecord:
    """Bug记录数据模型。"""
    id: str
    title: str
    module: str
    affected_files: list[str]
    root_cause: str
    symptoms: str
    fix_pattern: str
    caution: str
    affected_functions: list[str] = field(default_factory=list)
    generalization: str = ""
    status: str = "open"  # "open" | "fixed" | "archived"
    file_hashes: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    source_session: str = ""
    memory_linked: str | None = None

    def to_dict(self) -> dict:
        """转为字典（用于 JSON 序列化）。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BugRecord":
        """从字典构造记录。"""
        return cls(
            id=data["id"],
            title=data["title"],
            module=data["module"],
            affected_files=data.get("affected_files", []),
            affected_functions=data.get("affected_functions", []),
            root_cause=data["root_cause"],
            symptoms=data["symptoms"],
            fix_pattern=data["fix_pattern"],
            caution=data["caution"],
            generalization=data.get("generalization", ""),
            status=data.get("status", "open"),
            file_hashes=data.get("file_hashes", {}),
            created_at=data.get("created_at", ""),
            source_session=data.get("source_session", ""),
            memory_linked=data.get("memory_linked"),
        )


class BugStore:
    """Bug库存储层，管理 .jsonl 和 .md 双写。"""

    # 模块映射表：代码目录前缀 -> Bug文件前缀
    MODULE_MAP = {
        "cli": "cli",
        "memory_ex": "memory_ex",
        "memory": "memory",
        "query": "query",
        "llm_tool": "llm_tool",
        "utility": "utility",
        "tools": "tools",
        "command": "command",
        "A2A": "a2a",
    }

    def __init__(self, base_dir: Path):
        """初始化存储层。

        Args:
            base_dir: Bug库根目录，指向 memory_storage/memory_ex/bug_base/
        """
        self.base_dir = Path(base_dir)
        self.archive_dir = self.base_dir / "_archive"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    # ========== 公共接口 ==========

    def add(self, record: BugRecord) -> str:
        """新增Bug记录，自动按 module 分文件，同时写 .jsonl 和 .md。

        Returns:
            记录 ID。
        """
        module = record.module
        records = self._read_jsonl(module)
        records.append(record)
        self._write_jsonl(module, records)
        self._write_md(module, records)
        return record.id

    def get(self, record_id: str) -> BugRecord | None:
        """按 ID 查询单条记录（遍历所有模块文件）。"""
        for record in self._iter_all_records():
            if record.id == record_id:
                return record
        # 查归档
        for record in self._iter_archive_records():
            if record.id == record_id:
                return record
        return None

    def get_by_module(self, module: str) -> list[BugRecord]:
        """获取指定模块的所有 open 状态记录。"""
        records = self._read_jsonl(module)
        return [r for r in records if r.status == "open"]

    def get_by_file(self, file_path: str) -> list[BugRecord]:
        """获取涉及指定文件的所有 open 状态记录（路径匹配）。"""
        results = []
        for record in self._iter_all_records():
            if record.status != "open":
                continue
            for af in record.affected_files:
                if self._path_match(af, file_path):
                    results.append(record)
                    break
        return results

    def update(self, record_id: str, **fields) -> bool:
        """更新记录字段（如 status、memory_linked）。"""
        for module_file in self._get_module_files():
            records = self._read_jsonl(module_file.stem)
            updated = False
            for r in records:
                if r.id == record_id:
                    for k, v in fields.items():
                        if hasattr(r, k):
                            setattr(r, k, v)
                    updated = True
                    break
            if updated:
                self._write_jsonl(module_file.stem, records)
                self._write_md(module_file.stem, records)
                return True
        return False

    def archive(self, record_id: str) -> bool:
        """将记录从模块文件移至 _archive/，状态改为 archived。"""
        for module_file in self._get_module_files():
            module = module_file.stem
            records = self._read_jsonl(module)
            found_idx = None
            for i, r in enumerate(records):
                if r.id == record_id:
                    found_idx = i
                    break
            if found_idx is not None:
                record = records.pop(found_idx)
                record.status = "archived"
                self._write_jsonl(module, records)
                self._write_md(module, records)
                # 写入归档文件
                archived = self._read_archive_jsonl()
                archived.append(record)
                self._write_archive_jsonl(archived)
                self._write_archive_md(archived)
                return True
        return False

    def get_all_open(self) -> list[BugRecord]:
        """获取所有 open 状态的记录（遍历所有模块文件）。"""
        return [r for r in self._iter_all_records() if r.status == "open"]

    def get_all_fixed(self) -> list[BugRecord]:
        """获取所有 fixed 状态的记录。"""
        return [r for r in self._iter_all_records() if r.status == "fixed"]

    def get_stats(self) -> dict[str, dict[str, int]]:
        """获取各模块的统计信息。

        Returns:
            {module: {"open": N, "fixed": N, "archived": N}}
        """
        stats: dict[str, dict[str, int]] = {}
        for record in self._iter_all_records():
            module = record.module
            if module not in stats:
                stats[module] = {"open": 0, "fixed": 0, "archived": 0}
            stats[module][record.status] = stats[module].get(record.status, 0) + 1
        # 归档统计
        archived_count = 0
        for _ in self._iter_archive_records():
            archived_count += 1
        if archived_count > 0:
            if "_archive" not in stats:
                stats["_archive"] = {"open": 0, "fixed": 0, "archived": 0}
            stats["_archive"]["archived"] = archived_count
        return stats

    # ========== 模块推断 ==========

    def _resolve_module(self, file_path: str) -> str:
        """根据文件路径推断所属模块名。

        规则：
        1. 提取 src/ 后的第一级子目录名
        2. 如果路径不含 src/，返回 'misc'
        """
        # 标准化路径分隔符
        fp = file_path.replace("\\", "/")
        parts = fp.split("/")
        for i, part in enumerate(parts):
            if part == "src" and i + 1 < len(parts):
                subdir = parts[i + 1]
                return self.MODULE_MAP.get(subdir, subdir)
        return "misc"

    # ========== 文件读写 ==========

    def _get_module_files(self) -> list[Path]:
        """获取所有已存在的模块 .jsonl 文件。"""
        return list(self.base_dir.glob("*.jsonl"))

    def _read_jsonl(self, module: str) -> list[BugRecord]:
        """读取指定模块的 .jsonl 文件。"""
        jsonl_path = self.base_dir / f"{module}.jsonl"
        if not jsonl_path.exists():
            return []
        records = []
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                records.append(BugRecord.from_dict(data))
            except json.JSONDecodeError:
                continue
        return records

    def _write_jsonl(self, module: str, records: list[BugRecord]):
        """写入 .jsonl 文件（全量重写）。"""
        jsonl_path = self.base_dir / f"{module}.jsonl"
        lines = [json.dumps(r.to_dict(), ensure_ascii=False) for r in records]
        jsonl_path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")

    def _write_md(self, module: str, records: list[BugRecord]):
        """写入 .md 文件（全量重写，人类可读格式）。"""
        md_path = self.base_dir / f"{module}.md"
        sections = []
        for r in records:
            sections.append(self._format_md_record(r))
        md_path.write_text("\n\n---\n\n".join(sections) + "\n" if sections else "", encoding="utf-8")

    def _format_md_record(self, r: BugRecord) -> str:
        """格式化单条记录为 Markdown。"""
        files_str = ", ".join(f"`{f}`" for f in r.affected_files) if r.affected_files else "无"
        funcs_str = ", ".join(r.affected_functions) if r.affected_functions else "无"
        lines = [
            f"## [{r.status}] {r.id} — {r.title}",
            "",
            f"- **模块**: {r.module}",
            f"- **文件**: {files_str}",
            f"- **函数**: {funcs_str}",
            f"- **创建时间**: {r.created_at}",
            f"- **来源会话**: {r.source_session}",
            "",
            "### 前因后果",
            f"- **根因**: {r.root_cause}",
            f"- **症状**: {r.symptoms}",
            "",
            "### 修复方式",
            r.fix_pattern,
            "",
            "### 注意事项",
            r.caution,
        ]
        if r.generalization:
            lines.extend(["", "### 举一反三", r.generalization])
        return "\n".join(lines)

    # ========== 归档读写 ==========

    def _read_archive_jsonl(self) -> list[BugRecord]:
        """读取归档文件。"""
        archive_path = self.archive_dir / "archived.jsonl"
        if not archive_path.exists():
            return []
        records = []
        for line in archive_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                records.append(BugRecord.from_dict(data))
            except json.JSONDecodeError:
                continue
        return records

    def _write_archive_jsonl(self, records: list[BugRecord]):
        """写入归档 .jsonl。"""
        archive_path = self.archive_dir / "archived.jsonl"
        lines = [json.dumps(r.to_dict(), ensure_ascii=False) for r in records]
        archive_path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")

    def _write_archive_md(self, records: list[BugRecord]):
        """写入归档 .md。"""
        md_path = self.archive_dir / "archived.md"
        sections = []
        for r in records:
            sections.append(self._format_md_record(r))
        md_path.write_text("\n\n---\n\n".join(sections) + "\n" if sections else "", encoding="utf-8")

    def _iter_archive_records(self) -> Iterator[BugRecord]:
        """遍历归档记录。"""
        for r in self._read_archive_jsonl():
            yield r

    # ========== 迭代 ==========

    def _iter_all_records(self) -> Iterator[BugRecord]:
        """遍历所有模块文件中的所有记录。"""
        for module_file in self._get_module_files():
            module = module_file.stem
            for record in self._read_jsonl(module):
                yield record

    # ========== 工具方法 ==========

    def _compute_file_hash(self, file_path: str) -> str:
        """计算文件内容的 MD5 哈希。"""
        p = Path(file_path)
        if not p.exists():
            return ""
        return hashlib.md5(p.read_bytes()).hexdigest()

    @staticmethod
    def _path_match(pattern: str, path: str) -> bool:
        """路径匹配：支持精确匹配和后缀匹配。"""
        pattern = pattern.replace("\\", "/").lower()
        path = path.replace("\\", "/").lower()
        if pattern == path:
            return True
        if path.endswith(pattern) or pattern.endswith(path):
            return True
        # 目录前缀匹配
        if pattern.endswith("/") and path.startswith(pattern):
            return True
        return False

    def generate_id(self) -> str:
        """生成 Bug ID：bug_YYYYMMDD_NNN。"""
        date_str = datetime.now().strftime("%Y%m%d")
        prefix = f"bug_{date_str}_"
        # 找到当天最大的序号
        max_seq = 0
        for record in self._iter_all_records():
            if record.id.startswith(prefix):
                try:
                    seq = int(record.id.split("_")[-1])
                    max_seq = max(max_seq, seq)
                except ValueError:
                    continue
        for record in self._iter_archive_records():
            if record.id.startswith(prefix):
                try:
                    seq = int(record.id.split("_")[-1])
                    max_seq = max(max_seq, seq)
                except ValueError:
                    continue
        return f"{prefix}{max_seq + 1:03d}"

    def get_extracted_entry_ids(self) -> set[str]:
        """获取已提取过 Bug 的原始记忆条目 ID 集合。

        读取 bug_base/extraction_metadata.json，返回已处理过的
        raw_memory 条目 ID 集合，避免重复提取。
        """
        meta_path = self.base_dir / "extraction_metadata.json"
        if not meta_path.exists():
            return set()
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return set(data.get("extracted_entry_ids", []))
        except Exception:
            return set()

    def mark_entry_extracted(self, entry_id: str):
        """标记原始记忆条目已完成 Bug 提取。

        Args:
            entry_id: raw_memory.jsonl 中的条目 ID
        """
        meta_path = self.base_dir / "extraction_metadata.json"
        ids = self.get_extracted_entry_ids()
        ids.add(entry_id)
        data = {"extracted_entry_ids": list(ids)}
        meta_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_extracted_md_files(self) -> set:
        """获取已提取过 Bug 的 MD 文件名集合。

        读取 bug_ext_record.md，返回已处理过的
        MD 会话日志文件名集合，避免重复提取。
        """
        record_path = self.base_dir / "bug_ext_record.md"
        if not record_path.exists():
            return set()
        try:
            content = record_path.read_text(encoding="utf-8")
            return set(line.strip() for line in content.splitlines() if line.strip())
        except Exception:
            return set()

    def mark_md_file_extracted(self, filename: str):
        """标记 MD 文件已完成 Bug 提取。

        Args:
            filename: MD 会话日志文件名（如 MyClaude_2026-07-28_22-21-56.md）
        """
        record_path = self.base_dir / "bug_ext_record.md"
        try:
            with open(record_path, "a", encoding="utf-8") as f:
                f.write(filename + "\n")
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"写入 bug_ext_record.md 失败: {e}")
