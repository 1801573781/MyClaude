#!/usr/bin/env python3
"""
MyClaude 目录地图可视化生成器
递归扫描项目目录，调用 DeepSeek LLM 生成文件概述，输出标准树形目录。
运行方式：python tree_visualizer.py <项目根目录路径> [--no-cache]
"""

import argparse
import fnmatch
import os
import re
import sys
import time
from pathlib import Path

from openai import OpenAI
from rich.console import Console
from rich.tree import Tree


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

        # 去掉开头的 / （表示从根目录匹配）
        # 保留用于后续匹配判断
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
    parts = pattern.split("**")
    if len(parts) == 1:
        return fnmatch.fnmatch(path, pattern)
    # 将 ** 替换为 * 进行宽松匹配
    regex_pattern = re.escape(pattern).replace(r"\*\*", ".*")
    # 同时也要处理原有的 * 通配符
    regex_pattern = regex_pattern.replace(r"\*", "[^/]*")
    # 修正：** 应该匹配任意路径（包含 /）
    regex_pattern = pattern
    # 构建正则
    regex = fnmatch.translate(pattern)
    # fnmatch.translate 不原生支持 **，手动调整
    # 简化处理：将 ** 视为匹配任意字符（包括路径分隔符）
    if "**" in pattern:
        # 手动构建正则
        escaped = re.escape(pattern)
        # ** → 匹配任意字符包括 /
        escaped = escaped.replace(r"\*\*", ".*")
        # * → 匹配除 / 外的任意字符
        # 注意：re.escape 已将 * 转义，需要区分处理
        # 更简单的做法：先替换 **，再替换单独的 *
        pass

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
# 缓存机制
# ============================================================

def load_cache(cache_path: Path) -> dict[str, tuple[int, str]]:
    """
    从 .tree_cache.md 加载缓存。
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
                mtime = int(parts[2])
            except (ValueError, IndexError):
                continue
            overview = parts[3] if len(parts) > 3 else ""
            cache[file_path] = (mtime, overview)

    return cache


def save_cache(cache_path: Path, cache: dict[str, tuple[int, str]]) -> None:
    """保存缓存到 .tree_cache.md。"""
    lines = [
        "# Tree Cache",
        "",
        "| 文件路径 | 修改时间 | 概述 |",
        "|---------|---------|------|",
    ]
    for file_path, (mtime, overview) in sorted(cache.items()):
        lines.append(f"| {file_path} | {mtime} | {overview} |")

    try:
        cache_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass  # 缓存写入失败不阻塞主流程


# ============================================================
# LLM 概述生成
# ============================================================

MAX_FILE_SIZE = 50 * 1024  # 50KB

# Prompt 模板
PROMPT_TEMPLATE = (
    '请为文件 "{file_name}" 生成不超过20字的用途概述。'
    "文件内容如下：\n{content_snippet}\n"
    "只返回概述文本，不要解释。"
)


def get_file_overview(
    file_path: Path,
    cache: dict[str, tuple[int, str]],
    cache_path: Path,
    client: OpenAI | None,
    no_cache: bool,
) -> str:
    """
    获取文件概述。优先使用缓存，缓存未命中则调用 LLM。
    返回概述文本。
    """
    abs_path = str(file_path.resolve())
    mtime = int(file_path.stat().st_mtime)

    # 检查缓存
    if not no_cache and abs_path in cache:
        cached_mtime, cached_overview = cache[abs_path]
        if cached_mtime == mtime:
            return cached_overview

    # 读取文件内容
    file_size = file_path.stat().st_size
    is_truncated = file_size > MAX_FILE_SIZE
    try:
        if is_truncated:
            with open(file_path, "rb") as f:
                raw = f.read(MAX_FILE_SIZE)
            # 尝试以 UTF-8 解码截断内容
            try:
                content = raw.decode("utf-8", errors="replace")
            except UnicodeDecodeError:
                content = raw.decode("latin-1", errors="replace")
        else:
            content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError, PermissionError):
        overview = "(无法读取)"
        cache[abs_path] = (mtime, overview)
        return overview

    # 调用 LLM 生成概述
    if client is None:
        overview = "(无 API Key)"
    else:
        prompt = PROMPT_TEMPLATE.format(
            file_name=file_path.name,
            content_snippet=content[:4000],  # 限制 prompt 长度
        )
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=50,
                temperature=0.3,
            )
            overview = response.choices[0].message.content.strip()
            # 清理可能的引号和多余空白
            overview = overview.strip('"\'').strip()
            if len(overview) > 20:
                overview = overview[:20]
        except Exception as e:
            overview = f"(API错误: {str(e)[:15]})"

    if is_truncated:
        overview += " (截断)"

    # 更新缓存
    cache[abs_path] = (mtime, overview)
    # 每次更新后保存缓存
    save_cache(cache_path, cache)

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
    no_cache: bool,
    console: Console,
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

        # 跳过隐藏缓存文件（不在树中显示 .tree_cache.md）
        if entry_name == ".tree_cache.md":
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
                cache_path, client, no_cache, console,
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
            overview = get_file_overview(
                entry, cache, cache_path, client, no_cache,
            )

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
    # 目标：概述从第 45 列开始
    target_col = 45
    current_len = len(file_name)
    padding = max(2, target_col - current_len)
    return f"{file_name}{' ' * padding}# {overview}"


# ============================================================
# 主入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MyClaude 目录地图可视化生成器",
    )
    parser.add_argument(
        "root",
        help="项目根目录路径",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="忽略缓存，强制重新生成概述",
    )
    args = parser.parse_args()

    root_path = Path(args.root).resolve()
    if not root_path.is_dir():
        print(f"错误：目录不存在 - {root_path}", file=sys.stderr)
        sys.exit(1)

    # 初始化 OpenAI 客户端
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    client: OpenAI | None = None
    if api_key:
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
        )
    else:
        print("[警告] 未设置 DEEPSEEK_API_KEY 环境变量，将跳过 LLM 概述生成")
        print("        设置方法：set DEEPSEEK_API_KEY=your-key  (Windows)")
        print("                  export DEEPSEEK_API_KEY=your-key  (Linux/Mac)")
        print()

    # 解析 .gitignore
    gitignore_rules = parse_gitignore(root_path)

    # 加载缓存
    cache_path = root_path / ".tree_cache.md"
    cache = load_cache(cache_path) if not args.no_cache else {}

    # 构建树
    console = Console()
    tree = build_tree(
        root_path, root_path, gitignore_rules, cache,
        cache_path, client, args.no_cache, console,
    )

    if tree is not None:
        console.print(tree)
    else:
        console.print("[dim](空目录或所有内容已被忽略)[/dim]")

    # 最终保存缓存
    if not args.no_cache:
        save_cache(cache_path, cache)


if __name__ == "__main__":
    main()