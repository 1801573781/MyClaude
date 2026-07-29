"""BugStore — Bug库存储层。

管理纯 .md 文件存储、按模块分文件存储、归档管理。
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
            file_hashes=data.get("file_hashes", {}),
            created_at=data.get("created_at", ""),
            source_session=data.get("source_session", ""),
            memory_linked=data.get("memory_linked"),
        )


class BugStore:
    """Bug库存储层，管理纯 .md 存储。"""

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
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ========== 公共接口 ==========

    def add(self, record: BugRecord) -> str:
        """新增Bug记录，自动按 module 分文件，只写 .md。

        Returns:
            记录 ID。
        """
        module = record.module
        records = self._read_md(module)
        records.append(record)
        self._write_md(module, records)
        return record.id

    def get(self, record_id: str) -> BugRecord | None:
        """按 ID 查询单条记录（遍历所有模块文件）。"""
        for record in self._iter_all_records():
            if record.id == record_id:
                return record
        return None

    def get_by_module(self, module: str) -> list[BugRecord]:
        """获取指定模块的所有记录。"""
        return self._read_md(module)

    def get_by_file(self, file_path: str) -> list[BugRecord]:
        """获取涉及指定文件的所有记录（路径匹配）。"""
        results = []
        for record in self._iter_all_records():
            for af in record.affected_files:
                if self._path_match(af, file_path):
                    results.append(record)
                    break
        return results

    def update(self, record_id: str, **fields) -> bool:
        """更新记录字段（如 memory_linked）。"""
        for module_file in self._get_module_files():
            records = self._read_md(module_file.stem)
            updated = False
            for r in records:
                if r.id == record_id:
                    for k, v in fields.items():
                        if hasattr(r, k):
                            setattr(r, k, v)
                    updated = True
                    break
            if updated:
                self._write_md(module_file.stem, records)
                return True
        return False

    def get_all(self) -> list[BugRecord]:
        """获取所有记录（遍历所有模块文件）。"""
        return list(self._iter_all_records())

    def get_stats(self) -> dict[str, int]:
        """获取各模块的统计信息。

        Returns:
            {module: count}
        """
        stats: dict[str, int] = {}
        for record in self._iter_all_records():
            module = record.module
            stats[module] = stats.get(module, 0) + 1
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
        """获取所有已存在的模块 .md 文件（排除 bug_ext_record.md）。"""
        return [f for f in self.base_dir.glob("*.md") if f.name != "bug_ext_record.md"]

    def _read_md(self, module: str) -> list[BugRecord]:
        """读取指定模块的 .md 文件并解析为 BugRecord 列表。"""
        md_path = self.base_dir / f"{module}.md"
        if not md_path.exists():
            return []
        content = md_path.read_text(encoding="utf-8")
        if not content.strip():
            return []
        # 按分隔符分割记录
        sections = re.split(r"\n\n---\n\n", content.strip())
        records = []
        for section in sections:
            section = section.strip()
            if not section:
                continue
            record = self._parse_md_record(section)
            if record:
                records.append(record)
        return records

    def _parse_md_record(self, section: str) -> BugRecord | None:
        """从 Markdown 片段解析单条 BugRecord。"""
        try:
            # 提取标题行：## {id} — {title}
            header_match = re.match(r"^##\s+(\S+)\s+—\s+(.+)", section)
            if not header_match:
                return None
            record_id = header_match.group(1)
            title = header_match.group(2).strip()

            def _extract_field(label: str) -> str:
                """提取 - **label**: value 格式的字段。"""
                m = re.search(rf"-\s*\*\*{re.escape(label)}\*\*:\s*(.*)", section)
                return m.group(1).strip() if m else ""

            def _extract_multiline(section_name: str) -> str:
                """提取 ### section_name 下的多行内容。"""
                pattern = rf"###\s+{re.escape(section_name)}\s*\n(.*?)(?=###|\Z)"
                m = re.search(pattern, section, re.DOTALL)
                return m.group(1).strip() if m else ""

            module = _extract_field("模块")
            files_str = _extract_field("文件")
            funcs_str = _extract_field("函数")
            created_at = _extract_field("创建时间")
            source_session = _extract_field("来源会话")

            root_cause = ""
            symptoms = ""
            cause_section = _extract_multiline("前因后果")
            if cause_section:
                root_m = re.search(r"-\s*\*\*根因\*\*:\s*(.*)", cause_section)
                if root_m:
                    root_cause = root_m.group(1).strip()
                symp_m = re.search(r"-\s*\*\*症状\*\*:\s*(.*)", cause_section)
                if symp_m:
                    symptoms = symp_m.group(1).strip()

            fix_pattern = _extract_multiline("修复方式")
            caution = _extract_multiline("注意事项")
            generalization = _extract_multiline("举一反三")

            # 解析文件列表
            affected_files = []
            if files_str and files_str != "无":
                affected_files = [
                    f.strip().strip("`")
                    for f in files_str.split(",")
                    if f.strip()
                ]

            # 解析函数列表
            affected_functions = []
            if funcs_str and funcs_str != "无":
                affected_functions = [
                    f.strip() for f in funcs_str.split(",") if f.strip()
                ]

            return BugRecord(
                id=record_id,
                title=title,
                module=module,
                affected_files=affected_files,
                affected_functions=affected_functions,
                root_cause=root_cause,
                symptoms=symptoms,
                fix_pattern=fix_pattern,
                caution=caution,
                generalization=generalization,
                file_hashes={},
                created_at=created_at,
                source_session=source_session,
                memory_linked=None,
            )
        except Exception:
            return None

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
            f"## {r.id} — {r.title}",
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

    # ========== 迭代 ==========

    def _iter_all_records(self) -> Iterator[BugRecord]:
        """遍历所有模块文件中的所有记录。"""
        for module_file in self._get_module_files():
            module = module_file.stem
            for record in self._read_md(module):
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

    def get_extracted_md_files(self) -> dict[str, int]:
        """获取已提取过 Bug 的 MD 文件及字节偏移量。

        读取 bug_ext_record.json，返回文件名→字节偏移量字典。
        下次提取时，从该偏移量之后读取新增内容。
        """
        record_path = self.base_dir / "bug_ext_record.json"
        if not record_path.exists():
            return {}
        try:
            data = json.loads(record_path.read_text(encoding="utf-8"))
            return {k: int(v) for k, v in data.items()}
        except Exception:
            return {}

    def get_md_file_offset(self, filename: str) -> int:
        """获取指定 MD 文件的字节偏移量。"""
        records = self.get_extracted_md_files()
        return records.get(filename, 0)

    def mark_md_file_extracted(self, filename: str, byte_offset: int):
        """标记 MD 文件已提取到指定字节偏移量。

        Args:
            filename: MD 会话日志文件名
            byte_offset: 提取完成时的文件字节大小
        """
        record_path = self.base_dir / "bug_ext_record.json"
        records = self.get_extracted_md_files()
        records[filename] = byte_offset
        try:
            record_path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"写入 bug_ext_record.json 失败: {e}")

    def read_incremental_content(self, file_path: Path, byte_offset: int) -> str:
        """从指定字节偏移读取文件内容（增量或全量）。

        处理偏移落在行中间或多字节字符中间的情况：
        跳到下一个完整行的起始，确保不截断 UTF-8 字符。

        Args:
            file_path: MD 文件路径
            byte_offset: 起始字节偏移（0 表示全量读取）

        Returns:
            读取到的文本内容
        """
        if byte_offset == 0:
            return file_path.read_text(encoding="utf-8")

        with open(file_path, "rb") as f:
            f.seek(byte_offset)
            raw_bytes = f.read()

        if not raw_bytes:
            return ""

        try:
            content = raw_bytes.decode("utf-8")
            if not content.startswith("\n"):
                first_newline = content.find("\n")
                if first_newline >= 0:
                    content = content[first_newline + 1:]
        except UnicodeDecodeError:
            first_newline = raw_bytes.find(b"\n")
            if first_newline >= 0:
                content = raw_bytes[first_newline + 1:].decode("utf-8", errors="replace")
            else:
                content = ""

        return content
