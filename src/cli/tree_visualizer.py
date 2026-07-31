#!/usr/bin/env python3
"""
MyClaude 目录地图可视化生成器
递归扫描项目目录，调用 DeepSeek LLM 生成文件概述，输出标准树形目录。
通过 CLI 命令 /pt 调用 create_project_tree()。
"""

import ast
import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI
from rich.tree import Tree
from datetime import datetime

from src.utility.config_loader import global_cfg
from src.cli.cli_print import console


api_key = global_cfg.DeepSeek.api_key
base_url = global_cfg.DeepSeek.base_url
model_name = global_cfg.DeepSeek.model_name
summary_len = 50


# ============================================================
# .gitignore 解析
# ============================================================

def parse_gitignore(root_path: Path) -> list[dict]:
    """
    解析项目根目录的 .gitignore 文件，返回规则列表。
    每条规则为 dict：{"pattern": str, "is_negate": bool, "is_dir_only": bool}
    """
    gitignore_path = root_path / ".gitignore"
    if not gitignore_path.is_file():
        return []

    rules = []
    try:
        content = gitignore_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    for line in content.splitlines():
        line = line.strip()
        # 跳过空行和注释
        if not line or line.startswith("#"):
            continue

        is_negate = False
        if line.startswith("!"):
            is_negate = True
            line = line[1:].strip()

        if not line:
            continue

        is_dir_only = line.endswith("/")
        if is_dir_only:
            line = line[:-1]

        rules.append({
            "pattern": line,
            "is_negate": is_negate,
            "is_dir_only": is_dir_only,
        })

    return rules


def is_ignored_by_gitignore(
        rel_path: str,
        is_dir: bool,
        rules: list[dict],
) -> bool:
    """
    判断相对路径是否被 .gitignore 规则忽略。
    按规则顺序匹配，最后匹配的规则决定结果。
    """
    ignored = False
    for rule in rules:
        pattern = rule["pattern"]
        is_negate = rule["is_negate"]
        is_dir_only = rule["is_dir_only"]

        # 如果规则仅匹配目录，但当前不是目录，跳过
        if is_dir_only and not is_dir:
            continue

        # 匹配逻辑
        matched = False
        # 尝试匹配完整相对路径
        if _match_pattern(rel_path, pattern):
            matched = True
        # 也尝试仅匹配文件名
        elif _match_pattern(os.path.basename(rel_path), pattern):
            matched = True
        # 对于目录，也尝试匹配目录名
        if is_dir and not matched:
            dir_name = os.path.basename(rel_path.rstrip("/").rstrip("\\"))
            if _match_pattern(dir_name, pattern):
                matched = True
            # 也匹配带斜杠的目录名
            if _match_pattern(dir_name + "/", pattern):
                matched = True

        if matched:
            ignored = not is_negate

    return ignored


def _match_pattern(path: str, pattern: str) -> bool:
    """使用 fnmatch 进行 glob 模式匹配。"""
    # 处理 ** 模式
    if "**" in pattern:
        return _match_globstar(path, pattern)
    return fnmatch.fnmatch(path, pattern)


def _match_globstar(path: str, pattern: str) -> bool:
    """支持 ** 的简单 glob 匹配。"""
    # 使用更简单的方法：将 ** 替换为占位符，构建正则
    temp_pattern = pattern
    # 先保护 **
    temp_pattern = temp_pattern.replace("**", "\x00GLOBSTAR\x00")
    # 转义
    regex_str = re.escape(temp_pattern)
    # 还原 ** 为 .*
    regex_str = regex_str.replace("\x00GLOBSTAR\x00", ".*")
    # 还原 * 为 [^/]*
    regex_str = regex_str.replace(r"\*", "[^/]*")
    # 处理 ? 通配符
    regex_str = regex_str.replace(r"\?", "[^/]")

    try:
        return bool(re.match("^" + regex_str + "$", path))
    except re.error:
        return fnmatch.fnmatch(path, pattern)


# ============================================================
# 二进制扩展名排除
# ============================================================

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
    ".mp4", ".avi", ".mov", ".mp3", ".wav",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".exe", ".dll", ".so", ".dylib",
    ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".pyc", ".pyo", ".class", ".o", ".a",
}


def is_binary_extension(file_path: Path) -> bool:
    """检查文件扩展名是否在二进制排除列表中（大小写不敏感）。"""
    return file_path.suffix.lower() in BINARY_EXTENSIONS


# ============================================================
# 函数级摘要数据结构
# ============================================================

@dataclass
class FuncInfo:
    """函数信息"""
    name: str           # 函数/方法名
    start_line: int     # 起始行号
    end_line: int       # 结束行号
    docstring: str = "" # docstring（如有）
    summary: str = ""   # LLM 生成的概述（初始为空，后填充）


@dataclass
class ClassInfo:
    """类信息"""
    name: str           # 类名
    start_line: int     # 起始行号
    end_line: int       # 结束行号
    docstring: str = "" # docstring（如有）
    summary: str = ""   # LLM 生成的概述
    methods: list = field(default_factory=list)  # List[FuncInfo]，类内方法


@dataclass
class FileInfo:
    """文件信息"""
    file_path: str      # 相对路径
    file_summary: str = ""   # 文件级概述（在本次执行中由 LLM 生成，与 /init 一致）
    functions: list = field(default_factory=list)  # List[FuncInfo]，模块级函数
    classes: list = field(default_factory=list)    # List[ClassInfo]，类定义
    source_code: str = ""  # 完整源代码，传给 LLM 做函数级摘要


# ============================================================
# 缓存机制
# ============================================================

def load_cache(cache_path: Path) -> dict[str, tuple[int, str]]:
    """
    从缓存表格文件加载缓存。
    返回 dict：key = 文件绝对路径，value = (mtime, overview)
    """
    cache: dict[str, tuple[int, str]] = {}
    if not cache_path.is_file():
        return cache

    try:
        content = cache_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return cache

    lines = content.splitlines()
    in_table = False
    for line in lines:
        line = line.strip()
        # 跳过标题和非表格行
        if line.startswith("#"):
            continue
        if line.startswith("|") and "文件路径" in line:
            in_table = True
            continue
        if line.startswith("|---"):
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            continue

        # 解析表格行：| 文件路径 | 修改时间 | 概述 |
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4:  # 含首尾空元素
            file_path = parts[1]
            try:
                mtime = int(datetime.strptime(parts[2], "%Y-%m-%d %H:%M:%S").timestamp())
            except (ValueError, IndexError):
                continue
            overview = parts[3] if len(parts) > 3 else ""
            cache[file_path] = (mtime, overview)

    return cache


def save_cache(cache_path: Path, cache: dict[str, tuple[int, str]], root_path: Path) -> None:
    """保存缓存表格到独立文件（不影响树形结构文件）。"""
    lines = [
        f"# 项目文件概述，目录地图 for {root_path}\n",
        "| 文件路径 | 修改时间 | 概述 |",
        "|---------|---------|------|",
    ]
    for file_path, (mtime, overview) in sorted(cache.items()):
        dt_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"| {file_path} | {dt_str} | {overview} |")
    try:
        cache_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass  # 缓存写入失败不阻塞主流程


# ============================================================
# LLM 概述生成
# ============================================================

MAX_FILE_SIZE = 50 * 1024  # 50KB

PROMPT_TEMPLATE = """\
请为文件 "{file_name}" 生成一句不超过{summary_len}字的"核心功能总结"。
文件内容片段：
{content_snippet}
规则：
1. 必须输出总结，不能为空。
2. 总结应直接描述该文件在整个项目中的作用（例如"实现斐波那契数列计算"）。
3. 禁止使用"文件用于"、"此文件"、"本文件"等废话，直接说功能。
4. 如果文件内容极少或全是注释，输出"辅助模块"或"配置文件"。
5. 输出总结可以使用自然标点（句号/逗号等），但不要添加无关的解释或引号。
只输出总结文本。"""


def get_file_overview(
        file_path: Path,
        cache: dict[str, tuple[int, str]],
        cache_path: Path,
        client: OpenAI | None,
        root_path: Path,
        record: dict[str, float] | None = None,
        stats: dict | None = None,
) -> str:
    """
    获取文件概述。优先使用缓存，缓存未命中则调用 LLM。
    返回概述文本。每次获取概述后打印到 CLI 终端（原 save_cache 逻辑改为打印）。
    增量检查：缓存 mtime 匹配 + .init_record.json 中 mtime 匹配 → 跳过。
    """
    abs_path = str(file_path.resolve())
    mtime = int(file_path.stat().st_mtime)

    if stats is not None:
        stats["total_files"] = stats.get("total_files", 0) + 1

    # 检查缓存 + 增量记录
    if abs_path in cache:
        cached_mtime, cached_overview = cache[abs_path]
        if cached_mtime == mtime:
            # 检查 .init_record.json 记录
            try:
                rel_path = str(file_path.relative_to(root_path)).replace("\\", "/")
            except ValueError:
                rel_path = file_path.name
            if record is None or (rel_path in record and mtime <= int(record[rel_path])):
                # 命中缓存，打印到终端
                console.print(f"  [dim]📄 {file_path.name} → {cached_overview} [缓存][/dim]")
                if stats is not None:
                    stats["reused"] = stats.get("reused", 0) + 1
                return cached_overview

    # 读取文件内容
    file_size = file_path.stat().st_size
    is_truncated = file_size > MAX_FILE_SIZE
    try:
        if is_truncated:
            with open(file_path, "rb") as f:
                raw = f.read(MAX_FILE_SIZE)
            try:
                content = raw.decode("utf-8", errors="replace")
            except UnicodeDecodeError:
                content = raw.decode("latin-1", errors="replace")
        else:
            content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError, PermissionError):
        overview = "(无法读取)"
        cache[abs_path] = (mtime, overview)
        console.print(f"  [red]📄 {file_path.name} → {overview}[/red]")
        return overview

    # 调用 LLM 生成概述
    if client is None:
        overview = "(无 API Key)"
    else:
        prompt = PROMPT_TEMPLATE.format(
            file_name=file_path.name,
            summary_len=summary_len,
            content_snippet=content[:16000]
        )
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=global_cfg.model_chat.initial_max_tokens,
                temperature=0.3,
            )
            overview = response.choices[0].message.content.strip()
            overview = overview.strip('"\'').strip()
        except Exception as e:
            overview = f"(API错误: {str(e)})"

    if is_truncated:
        overview += " (截断)"

    # 更新缓存
    cache[abs_path] = (mtime, overview)
    # 原来 save_cache 在这里，现在改为打印到终端
    console.print(f"  [green]📄 {file_path.name} → {overview}[/green]")

    if stats is not None:
        stats["generated"] = stats.get("generated", 0) + 1

    return overview


# ============================================================
# 树形构建与输出
# ============================================================

def build_tree(
        root_path: Path,
        current_path: Path,
        gitignore_rules: list[dict],
        cache: dict[str, tuple[int, str]],
        cache_path: Path,
        client: OpenAI | None,
        record: dict[str, float] | None = None,
        stats: dict | None = None,
) -> Tree | None:
    """
    递归构建 rich Tree 结构。
    返回 Tree 节点，如果当前目录为空（所有内容被忽略）则返回 None。
    """
    # 计算相对路径用于 gitignore 匹配
    try:
        rel_path = str(current_path.relative_to(root_path))
        if rel_path == ".":
            rel_path = ""
    except ValueError:
        rel_path = str(current_path)

    dir_name = current_path.name or str(current_path)
    tree = Tree(f"{dir_name}/")

    # 收集子条目
    try:
        entries = sorted(
            current_path.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except (PermissionError, OSError):
        return tree  # 无法访问的目录返回空节点

    has_visible_children = False

    for entry in entries:
        entry_name = entry.name

        # 跳过所有以点开头的隐藏文件和目录
        if entry_name.startswith('.'):
            continue

        # 跳过 .docx 文件
        if entry_name.endswith('.docx'):
            continue

        # 跳过 __init__.py 文件
        if entry_name == '__init__.py':
            continue

        # 计算条目的相对路径
        if rel_path:
            entry_rel = f"{rel_path}/{entry_name}"
        else:
            entry_rel = entry_name

        if entry.is_dir():
            entry_rel_dir = entry_rel + "/"
            # 检查是否被 gitignore 忽略
            if is_ignored_by_gitignore(entry_rel_dir, True, gitignore_rules):
                continue
            # 递归构建子树
            sub_tree = build_tree(
                root_path, entry, gitignore_rules, cache,
                cache_path, client,
                record=record, stats=stats,
            )
            if sub_tree is not None:
                tree.add(sub_tree)
                has_visible_children = True
        else:
            # 文件
            # 检查二进制扩展名
            if is_binary_extension(entry):
                continue
            # 检查 gitignore
            if is_ignored_by_gitignore(entry_rel, False, gitignore_rules):
                continue

            # 获取概述
            overview = get_file_overview(entry, cache, cache_path, client, root_path,
                                         record=record, stats=stats)

            # 格式化 label：文件名 + 填充空格 + # 概述
            label = _format_label(entry_name, overview)
            tree.add(label)
            has_visible_children = True

    # 如果没有任何可见子节点（且不是根目录），返回 None
    if not has_visible_children and rel_path != "":
        return None

    return tree


def _format_label(file_name: str, overview: str) -> str:
    """格式化树节点标签，使概述对齐。"""
    target_col = 45
    current_len = len(file_name)
    padding = max(2, target_col - current_len)
    return f"{file_name}{' ' * padding}# {overview}"


# ============================================================
# 保存树形结构到文件
# ============================================================

def _save_tree_to_file(tree: Tree, cache_path: Path, root_path: Path) -> None:
    """
    将 rich Tree 渲染为纯文本并保存到 .tree_cache.md。
    原来 console.print(tree) 的打印逻辑改为 save 到文件。
    """
    from io import StringIO
    from rich.console import Console

    # 使用临时 Console 捕获 rich Tree 的文本输出
    capture_console = Console(file=StringIO(), force_terminal=True, width=120)
    capture_console.print(tree)
    tree_text = capture_console.file.getvalue()

    lines = [
        f"# 项目目录树 for {root_path}\n",
        f"# 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        "",
        "```",
        tree_text.strip(),
        "```",
    ]
    try:
        cache_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass  # 写入失败不阻塞主流程


# ============================================================
# AST 解析与函数级摘要
# ============================================================

def extract_ast_info(file_path: str) -> FileInfo | None:
    """
    使用 ast 模块解析 Python 文件，提取函数/类/方法的名称与行范围。
    不依赖 LLM，纯静态分析。
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except (OSError, PermissionError):
        return None

    if not source.strip():
        return None

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    abs_path = Path(file_path).resolve()
    # FileInfo 的 file_path 存储相对路径，由调用者设置
    file_info = FileInfo(file_path="")
    file_info.source_code = source

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = FuncInfo(
                name=node.name,
                start_line=node.lineno,
                end_line=node.end_lineno if hasattr(node, "end_lineno") and node.end_lineno else node.lineno,
                docstring=ast.get_docstring(node) or "",
                summary="",
            )
            file_info.functions.append(func)
        elif isinstance(node, ast.ClassDef):
            class_info = ClassInfo(
                name=node.name,
                start_line=node.lineno,
                end_line=node.end_lineno if hasattr(node, "end_lineno") and node.end_lineno else node.lineno,
                docstring=ast.get_docstring(node) or "",
                summary="",
                methods=[],
            )
            # 遍历类内方法
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method = FuncInfo(
                        name=child.name,
                        start_line=child.lineno,
                        end_line=child.end_lineno if hasattr(child, "end_lineno") and child.end_lineno else child.lineno,
                        docstring=ast.get_docstring(child) or "",
                        summary="",
                    )
                    class_info.methods.append(method)
            file_info.classes.append(class_info)

    return file_info


FUNC_SUMMARY_PROMPT_TEMPLATE = """\
请为以下 Python 文件中的函数/方法/类生成概述，每个不超过30字。
直接描述其功能，禁止使用"此函数"、"该方法"等废话。

文件：{file_path}

以下是 AST 提取的函数/类列表（含行范围，供你定位参考）：
{func_list}

以下是该文件的完整源代码：
```python
{source_code}
```

请返回 JSON 数组，每个元素格式为：{{"name": "函数名", "summary": "概述"}}
注意：只返回 JSON，不要添加 markdown 代码块标记或其他文字。"""


def batch_generate_summaries(
        file_info: FileInfo,
        abs_file_path: str,
        client: OpenAI | None,
) -> None:
    """
    批量调用 LLM 为函数/类生成概述。
    策略：将单个文件的所有函数签名 + docstring 打包为一次 LLM 请求，
    要求 LLM 返回 JSON 数组，每个元素含 name + summary。
    """
    if client is None:
        _fill_failed_summaries(file_info)
        return

    # 构造函数列表文本
    func_items: list[str] = []
    all_names: list[str] = []

    for func in file_info.functions:
        func_items.append(
            f"- {func.name} (L{func.start_line}-L{func.end_line})\n  docstring: {func.docstring or '无'}"
        )
        all_names.append(func.name)

    for cls in file_info.classes:
        func_items.append(
            f"- class {cls.name} (L{cls.start_line}-L{cls.end_line})\n  docstring: {cls.docstring or '无'}"
        )
        all_names.append(cls.name)
        for method in cls.methods:
            func_items.append(
                f"  - {cls.name}.{method.name} (L{method.start_line}-L{method.end_line})\n    docstring: {method.docstring or '无'}"
            )
            all_names.append(method.name)

    if not func_items:
        return

    func_list_text = "\n".join(func_items)
    prompt = FUNC_SUMMARY_PROMPT_TEMPLATE.format(
        file_path=file_info.file_path,
        func_list=func_list_text,
        source_code=file_info.source_code,
    )

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=global_cfg.model_chat.initial_max_tokens,
            temperature=0.3,
        )
        raw_content = response.choices[0].message.content.strip()

        # 去除可能的 markdown 代码块标记
        if raw_content.startswith("```"):
            raw_content = re.sub(r"^```(?:json)?\s*\n?", "", raw_content)
            raw_content = re.sub(r"\n?```\s*$", "", raw_content)

        summaries = json.loads(raw_content)

        # 构建 name -> summary 映射
        name_to_summary: dict[str, str] = {}
        for item in summaries:
            if isinstance(item, dict) and "name" in item and "summary" in item:
                name_to_summary[item["name"]] = item["summary"]

        # 回填概述
        _fill_summaries(file_info, name_to_summary)

        # 检查是否有未匹配的，降级为逐函数请求
        missing_names = [n for n in all_names if n not in name_to_summary]
        if missing_names:
            _fallback_individual_summaries(file_info, missing_names, abs_file_path, client)

    except (json.JSONDecodeError, Exception):
        # LLM 返回格式异常，降级为逐函数请求
        _fallback_individual_summaries(file_info, all_names, abs_file_path, client)


def _fill_summaries(file_info: FileInfo, name_to_summary: dict[str, str]) -> None:
    """将 LLM 返回的概述回填到 FileInfo 中。"""
    for func in file_info.functions:
        if func.name in name_to_summary:
            func.summary = name_to_summary[func.name]
    for cls in file_info.classes:
        if cls.name in name_to_summary:
            cls.summary = name_to_summary[cls.name]
        for method in cls.methods:
            if method.name in name_to_summary:
                method.summary = name_to_summary[method.name]


def _fill_failed_summaries(file_info: FileInfo) -> None:
    """填充失败的概述文本。"""
    for func in file_info.functions:
        if not func.summary:
            func.summary = "概述生成失败"
    for cls in file_info.classes:
        if not cls.summary:
            cls.summary = "概述生成失败"
        for method in cls.methods:
            if not method.summary:
                method.summary = "概述生成失败"


INDIVIDUAL_PROMPT_TEMPLATE = """\
请为以下 Python 函数/方法生成不超过30字的概述。
直接描述功能，禁止使用"此函数"等废话。

函数名：{func_name}
行范围：L{start_line}-L{end_line}

以下是该函数/方法所在文件的完整源代码，请根据行范围定位并分析：
```python
{source_code}
```

只输出概述文本。"""


def _fallback_individual_summaries(
        file_info: FileInfo,
        missing_names: list[str],
        abs_file_path: str,
        client: OpenAI | None,
) -> None:
    """降级为逐函数请求 LLM 概述。传入完整源码供 LLM 定位分析。"""
    if client is None:
        _fill_failed_summaries(file_info)
        return

    source_code = file_info.source_code

    for func in file_info.functions:
        if func.name in missing_names and not func.summary:
            func.summary = _request_single_summary(
                func.name, func.start_line, func.end_line, source_code, client
            )
    for cls in file_info.classes:
        if cls.name in missing_names and not cls.summary:
            cls.summary = _request_single_summary(
                cls.name, cls.start_line, cls.end_line, source_code, client
            )
        for method in cls.methods:
            if method.name in missing_names and not method.summary:
                method.summary = _request_single_summary(
                    method.name, method.start_line, method.end_line, source_code, client
                )


def _request_single_summary(
        name: str,
        start_line: int,
        end_line: int,
        source_code: str,
        client: OpenAI,
) -> str:
    """请求单个函数/类的概述。传入完整源码供 LLM 定位。"""
    prompt = INDIVIDUAL_PROMPT_TEMPLATE.format(
        func_name=name,
        start_line=start_line,
        end_line=end_line,
        source_code=source_code,
    )
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip().strip('"\'')
    except Exception:
        return "概述生成失败"


def format_file_detail(file_info: FileInfo) -> str:
    """
    将单个 FileInfo 格式化为函数级 Markdown 片段。
    """
    lines: list[str] = []
    lines.append(f"## 文件：{file_info.file_path}")
    lines.append("")
    lines.append(f"> 文件概述：{file_info.file_summary}")
    lines.append("")

    has_content = False

    # 函数列表
    if file_info.functions:
        has_content = True
        lines.append("### 函数列表")
        lines.append("")
        lines.append("| 函数名 | 行范围 | 函数概述 |")
        lines.append("|--------|--------|----------|")
        for func in file_info.functions:
            lines.append(f"| `{func.name}` | L{func.start_line}-L{func.end_line} | {func.summary} |")
        lines.append("")

    # 类列表
    if file_info.classes:
        has_content = True
        lines.append("### 类列表")
        lines.append("")
        for cls in file_info.classes:
            lines.append(f"#### 类：`{cls.name}` (L{cls.start_line}-L{cls.end_line})")
            lines.append("")
            lines.append(f"> 类概述：{cls.summary}")
            lines.append("")
            if cls.methods:
                lines.append("| 方法名 | 行范围 | 方法概述 |")
                lines.append("|--------|--------|----------|")
                for method in cls.methods:
                    lines.append(f"| `{method.name}` | L{method.start_line}-L{method.end_line} | {method.summary} |")
                lines.append("")

    if not has_content:
        lines.append("(无函数或类定义)")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


# ============================================================
# 增量记录管理
# ============================================================

def load_init_record(record_path: Path) -> dict[str, float]:
    """
    加载 .init_record.json，返回文件路径到 mtime 的映射。
    兼容日期字符串和数字时间戳两种格式。
    文件不存在或格式损坏时返回空 dict。
    """
    if not record_path.is_file():
        return {}
    try:
        content = record_path.read_text(encoding="utf-8")
        record = json.loads(content)
        if not isinstance(record, dict):
            return {}
        result: dict[str, float] = {}
        for k, v in record.items():
            if isinstance(v, (int, float)):
                result[k] = float(v)
            elif isinstance(v, str):
                try:
                    result[k] = datetime.strptime(v, "%Y-%m-%d %H:%M:%S").timestamp()
                except ValueError:
                    continue
        return result
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def save_init_record(record_path: Path, record: dict[str, float]) -> None:
    """保存 .init_record.json，时间戳转为日期字符串方便人类阅读。"""
    str_record = {
        k: datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S")
        for k, v in record.items()
    }
    try:
        record_path.write_text(
            json.dumps(str_record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def should_generate(
        file_path: Path,
        root_path: Path,
        record: dict[str, float],
        summary_file_exists: bool,
) -> tuple[bool, str, float]:
    """
    判断单个文件是否需要重新生成摘要。
    返回 (need_generate, rel_path, current_mtime)。
    """
    # 第一步：摘要文件不存在或为空 → 必须生成
    if not summary_file_exists:
        try:
            current_mtime = float(int(os.path.getmtime(str(file_path))))
        except OSError:
            return True, "", 0.0
        rel_path = str(file_path.relative_to(root_path)).replace("\\", "/")
        return True, rel_path, current_mtime

    # 第二步：文件不在记录中 → 必须生成
    rel_path = str(file_path.relative_to(root_path)).replace("\\", "/")
    try:
        current_mtime = float(int(os.path.getmtime(str(file_path))))
    except OSError:
        return True, rel_path, 0.0

    if rel_path not in record:
        return True, rel_path, current_mtime

    # 第三步：mtime 变更检测
    if current_mtime > record[rel_path]:
        return True, rel_path, current_mtime

    return False, rel_path, current_mtime


def parse_old_summary_file(summary_path: Path) -> dict[str, str]:
    """
    解析旧的摘要输出文件，提取每个文件的旧摘要内容。
    返回 dict：key = 文件相对路径，value = 该文件的 Markdown 片段（含标题到---）。
    """
    if not summary_path.is_file():
        return {}

    try:
        content = summary_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    result: dict[str, str] = {}
    # 按 "## 文件：" 分割
    pattern = r"(## 文件：.+?\n(?:.*?\n)*?---)"
    matches = re.findall(pattern, content)
    for match in matches:
        # 提取文件路径
        path_match = re.match(r"## 文件：(.+?)\n", match)
        if path_match:
            file_path = path_match.group(1).strip()
            result[file_path] = match

    return result


# ============================================================
# 目录过滤辅助
# ============================================================

# 常见虚拟环境/依赖目录名，始终跳过
SKIP_DIR_NAMES = {"venv", ".venv", "virtualenv", "site-packages", "dist-packages", "node_modules"}


def _is_under_ignored_dir(file_path: Path, root_path: Path, gitignore_rules: list[dict]) -> bool:
    """检查文件是否位于被 gitignore 忽略的目录内。"""
    try:
        rel = file_path.relative_to(root_path)
    except ValueError:
        return False
    parts = list(rel.parent.parts)
    for i in range(len(parts)):
        dir_rel = "/".join(parts[:i + 1]) + "/"
        if is_ignored_by_gitignore(dir_rel, True, gitignore_rules):
            return True
    return False


# ============================================================
# 主函数 create_project_tree（原 main）
# ============================================================

def _format_duration_s(seconds: float) -> str:
    """将秒数格式化为人类可读的时间字符串。"""
    if seconds < 60:
        return f"{int(seconds)}秒"
    elif seconds < 3600:
        return f"{int(seconds // 60)}分{int(seconds % 60)}秒"
    else:
        return f"{int(seconds // 3600)}时{int((seconds % 3600) // 60)}分{int(seconds % 60)}秒"


def create_project_tree(root_path: Path | None = None, mode: str = "init") -> bool:
    """
    创建 MyClaude 项目工程树。

    Args:
        root_path: 项目根目录路径，None 则使用当前执行目录。

    Returns:
        bool: 成功返回 True，失败返回 False。
    """
    if root_path is None:
        root_path = Path.cwd()
    else:
        root_path = root_path.resolve()

    if not root_path.is_dir():
        console.print(f"[red]错误：目录不存在 - {root_path}[/red]")
        return False

    # 初始化 OpenAI 客户端
    client: OpenAI | None = None
    if api_key:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
    else:
        console.print("[yellow][警告] 未设置 DEEPSEEK_API_KEY 环境变量，将跳过 LLM 概述生成[/yellow]")

    console.print(f"\n[bold]🔍 正在扫描项目: {root_path}[/bold]\n")

    # 解析 .gitignore
    gitignore_rules = parse_gitignore(root_path)

    # 加载缓存和增量记录
    cache_data_path = root_path / ".tree_cache_data.md"
    tree_output_path = root_path / ".tree_cache.md"
    record_path = root_path / ".init_record.json"

    cache: dict[str, tuple[int, str]] = load_cache(cache_data_path)
    record: dict[str, float] = load_init_record(record_path)

    # 检查摘要输出文件是否存在且非空
    tree_cache_exists = tree_output_path.is_file() and tree_output_path.stat().st_size > 0

    # 如果摘要文件不存在，清空缓存强制全量生成
    if not tree_cache_exists:
        cache = {}

    stats: dict = {"total_files": 0, "reused": 0, "generated": 0}
    start_time = datetime.now()

    # 构建树
    tree = build_tree(
        root_path, root_path, gitignore_rules, cache,
        cache_data_path, client,
        record=record, stats=stats,
    )

    if tree is not None:
        # 原来 console.print(tree) 的地方，改为保存到树形文件
        console.print("\n[bold]📁 项目目录树：[/bold]")
        console.print(tree)
        _save_tree_to_file(tree, tree_output_path, root_path)
        console.print(f"\n[dim]树形结构已保存到 {tree_output_path}[/dim]")
    else:
        console.print("[dim](空目录或所有内容已被忽略)[/dim]")

    # 保存缓存（概述信息表格到独立文件）
    save_cache(cache_data_path, cache, root_path)

    # 更新并保存增量记录
    new_record: dict[str, float] = dict(record)  # 保留旧记录
    for abs_path, (mtime, _) in cache.items():
        try:
            rel = str(Path(abs_path).relative_to(root_path)).replace("\\", "/")
        except ValueError:
            rel = Path(abs_path).name
        new_record[rel] = float(mtime)
    save_init_record(record_path, new_record)

    # /init 模式执行总结
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    generated = stats["total_files"] - stats["reused"]
    console.print(f"\n[bold]📊 执行总结[/bold]")
    console.print(f"  开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(f"  结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(f"  总共耗时: {_format_duration_s(duration)}")
    console.print(f"  总共处理文件: {stats['total_files']} 个")
    console.print(f"  新增/更新: {generated} 个, 缓存复用: {stats['reused']} 个")

    # === 函数级摘要模式（init_file） ===
    if mode == "init_file":
        summary_output_path = root_path / ".project_function.md"

        # 使用独立的增量记录文件，避免被 /init 模式的记录覆盖
        file_record_path = root_path / ".init_file_record.json"

        # 加载增量记录
        record = load_init_record(file_record_path)

        # 解析旧摘要文件（用于保留未变更文件的内容）
        old_summaries = parse_old_summary_file(summary_output_path)

        # 收集所有 Python 文件
        py_files: list[Path] = []
        for entry in root_path.rglob("*.py"):
            entry_rel = str(entry.relative_to(root_path)).replace("\\", "/")
            # 跳过隐藏目录中的文件
            if any(part.startswith('.') for part in entry.parts):
                continue
            # 跳过 __pycache__
            if "__pycache__" in entry.parts:
                continue
            # 跳过虚拟环境/依赖目录
            if any(part in SKIP_DIR_NAMES for part in entry.parts):
                continue
            # 跳过位于 gitignore 忽略目录内的文件
            if _is_under_ignored_dir(entry, root_path, gitignore_rules):
                continue
            # 跳过 gitignore 忽略的文件
            if is_ignored_by_gitignore(entry_rel, False, gitignore_rules):
                continue
            py_files.append(entry)

        py_files.sort()

        new_record: dict[str, float] = dict(record)  # 保留旧记录（来自 .init_file_record.json）
        generated_count = 0
        reused_count = 0
        skipped_count = 0
        total_funcs = 0
        file_start_time = datetime.now()

        console.print(f"\n[bold]📝 正在生成函数级摘要（共 {len(py_files)} 个 Python 文件）...[/bold]\n")

        # 在写入 header 之前记录摘要文件是否存在（非空）
        summary_file_exists = summary_output_path.is_file() and summary_output_path.stat().st_size > 0

        # 先写入文件头部，后续逐文件追加
        header_lines = [
            f"# 项目函数级摘要 for {root_path}",
            f"",
            f"# 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
        ]
        summary_output_path.write_text("\n".join(header_lines) + "\n", encoding="utf-8")

        for py_file in py_files:
            need_gen, rel_path, current_mtime = should_generate(
                py_file, root_path, record, summary_file_exists
            )

            if not need_gen and rel_path in old_summaries:
                # 复用旧摘要 —— 直接追加写入文件
                with open(summary_output_path, "a", encoding="utf-8") as f:
                    f.write(old_summaries[rel_path] + "\n")
                new_record[rel_path] = current_mtime
                reused_count += 1
                # 统计旧摘要中的函数/方法数
                old_section = old_summaries[rel_path]
                total_funcs += old_section.count("| `")
            else:
                # 生成新摘要
                file_info = extract_ast_info(str(py_file))
                if file_info is None:
                    new_record[rel_path] = current_mtime
                    skipped_count += 1
                    console.print(f"  [yellow]⏭️ {rel_path} — 已跳过（空文件）[/yellow]")
                    continue

                file_info.file_path = rel_path
                # 从缓存中获取文件级概述
                abs_path = str(py_file.resolve())
                file_info.file_summary = cache.get(abs_path, (0, ""))[1] or ""

                batch_generate_summaries(file_info, abs_path, client)
                section = format_file_detail(file_info)

                # 统计函数/方法数
                total_funcs += len(file_info.functions)
                for cls in file_info.classes:
                    total_funcs += len(cls.methods)

                # 立即追加写入文件，方便快速调试
                with open(summary_output_path, "a", encoding="utf-8") as f:
                    f.write(section + "\n")

                new_record[rel_path] = current_mtime
                generated_count += 1

                console.print(f"  [green]📝 {rel_path} — 函数级摘要已生成[/green]")

        # 保存增量记录（使用独立文件）
        save_init_record(file_record_path, new_record)

        file_end_time = datetime.now()
        file_duration = (file_end_time - file_start_time).total_seconds()

        console.print(f"\n[bold]📝 函数级摘要已保存到 {summary_output_path}[/bold]")
        console.print(f"[dim]新增 {generated_count} 个文件, 复用 {reused_count} 个文件[/dim]")

        # /init file 模式执行总结
        console.print(f"\n[bold]📊 执行总结[/bold]")
        console.print(f"  开始时间: {file_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        console.print(f"  结束时间: {file_end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        console.print(f"  总共耗时: {_format_duration_s(file_duration)}")
        console.print(f"  总共处理文件: {len(py_files)} 个")
        console.print(f"  总共处理函数: {total_funcs} 个")
        console.print(f"  新增/更新: {generated_count} 个文件, 缓存复用: {reused_count} 个文件, 跳过: {skipped_count} 个文件（空文件）")

    return True


# 保留 main 作为独立运行的兼容入口
def main() -> None:
    """独立命令行运行入口（兼容旧用法）。"""
    import argparse
    parser = argparse.ArgumentParser(
        description="MyClaude 目录地图可视化生成器",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="项目根目录路径（可选，默认当前执行目录）",
    )
    parser.add_argument(
        "--mode",
        choices=["init", "init_file"],
        default="init",
        help="执行模式：init=文件级树形摘要，init_file=函数级摘要",
    )
    args = parser.parse_args()

    if args.root:
        root_path = Path(args.root).resolve()
    else:
        root_path = Path.cwd()

    create_project_tree(root_path, mode=args.mode)


if __name__ == "__main__":
    main()
