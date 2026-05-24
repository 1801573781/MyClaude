#!/usr/bin/env python3
"""
MyClaude 代码行数统计器 —— 增强版
支持按目录详细统计 + 按扩展名汇总统计
通过 CLI 命令 /cs 调用 code_statistics()。
"""
from pathlib import Path
from collections import defaultdict
from rich.table import Table

from src.utility.config_loader import global_cfg
from src.cli.cli_print import console


# ==================== 常量配置 ====================

SKIP_FILES = {'.gitignore'}

CODE_EXTS = {
    '.py', '.yaml', '.yml', '.json', '.md', '.txt',
    '.sh', '.bat', '.ps1', '.toml', '.ini', '.cfg'
}


# ==================== .gitignore 动态解析 ====================

def parse_gitignore_skip_dirs(gitignore_path):
    """
    动态解析 .gitignore，提取需要跳过的目录名集合。
    仅处理目录模式（以 '/' 或 '/*' 结尾的行），忽略以 '!' 开头的例外。
    """
    skip_dirs = set()
    gi = Path(gitignore_path)
    if not gi.is_file():
        return skip_dirs

    for line in gi.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        # 跳过空行和注释
        if not line or line.startswith('#'):
            continue
        # 跳过例外规则（! 开头）
        if line.startswith('!'):
            continue
        # 跳过文件模式（*.ext）
        if line.startswith('*.'):
            continue

        # 提取目录名：去除尾部的 '/' 和 '/*'
        if line.endswith('/*'):
            dir_name = line[:-2]
        elif line.endswith('/'):
            dir_name = line[:-1]
        else:
            # 纯文件名或 glob 模式，不处理
            continue

        skip_dirs.add(dir_name)

    return skip_dirs


# ==================== 注释计数 ====================

def count_comments(lines, ext):
    """根据扩展名统计注释行数"""
    comment = 0

    if ext == '.py':
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
                comment += 1
    elif ext in ('.yaml', '.yml', '.json', '.ini', '.cfg', '.toml'):
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('//'):
                comment += 1

    return comment


# ==================== 文件统计 ====================

def file_stats(path):
    """统计单个文件的 总行/代码/空行/注释"""
    content = path.read_text(encoding='utf-8', errors='ignore')
    lines = content.splitlines()

    total = len(lines)
    blank = sum(1 for l in lines if l.strip() == "")
    comment = count_comments(lines, path.suffix.lower())
    code = total - blank - comment

    return {
        "total": total,
        "code": code,
        "blank": blank,
        "comment": comment,
    }


# ==================== 核心遍历 ====================

def collect_stats(root):
    """
    遍历 root，返回两个数据结构：
      dir_stats:  { rel_dir: [ {name, total, code, blank, comment}, ... ] }
      ext_stats:  { ext: {"files": n, "total": n, "code": n, "blank": n, "comment": n} }
    """
    root = Path(root).resolve()
    skip_dirs = parse_gitignore_skip_dirs(root / '.gitignore')

    dir_stats = defaultdict(list)
    ext_stats = defaultdict(lambda: {"files": 0, "total": 0, "code": 0,
                                     "blank": 0, "comment": 0})

    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if path.name.startswith('.') or path.name in SKIP_FILES:
            continue

        rel = path.relative_to(root)
        parts = rel.parts
        if any(p in skip_dirs for p in parts[:-1]):
            continue

        ext = path.suffix.lower()
        if ext not in CODE_EXTS:
            continue

        try:
            s = file_stats(path)
        except Exception:
            continue

        # 按目录存储
        rel_dir = str(rel.parent) if str(rel.parent) != '.' else '.'
        dir_stats[rel_dir].append({
            "name": path.name,
            **s,
        })

        # 按扩展名汇总
        e = ext_stats[ext]
        e["files"] += 1
        e["total"] += s["total"]
        e["code"] += s["code"]
        e["blank"] += s["blank"]
        e["comment"] += s["comment"]

    return dir_stats, ext_stats


# ==================== 表格渲染 ====================

def print_dir_table(rel_dir, files):
    """打印单个目录的详细统计表"""
    table = Table(
        title=f"\n📂 {rel_dir}",
        show_header=True,
        header_style="bold cyan",
        box=None,
        padding=(0, 1),
    )

    table.add_column("文件名", style="bold", min_width=24, justify="left")
    table.add_column("总行数", justify="right", min_width=8)
    table.add_column("代码行", justify="right", min_width=8)
    table.add_column("空行", justify="right", min_width=6)
    table.add_column("注释", justify="right", min_width=6)

    dir_files = 0
    dir_total = 0
    dir_code = 0
    dir_blank = 0
    dir_comment = 0

    for f in sorted(files, key=lambda x: x["name"]):
        table.add_row(
            f["name"],
            str(f["total"]),
            str(f["code"]),
            str(f["blank"]),
            str(f["comment"]),
        )
        dir_files += 1
        dir_total += f["total"]
        dir_code += f["code"]
        dir_blank += f["blank"]
        dir_comment += f["comment"]

    # 分隔线 + 目录汇总
    table.add_row("─" * 24, "─" * 8, "─" * 8, "─" * 6, "─" * 6,
                  end_section=True)
    table.add_row(
        f"[bold]目录汇总 ({dir_files} 个文件)[/bold]",
        str(dir_total), str(dir_code), str(dir_blank), str(dir_comment),
    )

    console.print(table)


def print_summary_table(root, ext_stats):
    """打印按扩展名汇总的项目总计表"""
    grand = {"files": 0, "total": 0, "code": 0, "blank": 0, "comment": 0}
    for s in ext_stats.values():
        for k in grand:
            grand[k] += s[k]

    table = Table(
        title=f"\n📁 项目总计: {root}\n",
        show_header=True,
        header_style="bold cyan",
        box=None,
        padding=(0, 1),
    )

    table.add_column("类型", style="bold", min_width=8, justify="left")
    table.add_column("文件数", justify="right", min_width=8)
    table.add_column("总行数", justify="right", min_width=8)
    table.add_column("代码行", justify="right", min_width=8)
    table.add_column("空行", justify="right", min_width=6)
    table.add_column("注释", justify="right", min_width=6)

    for ext in sorted(ext_stats.keys()):
        s = ext_stats[ext]
        table.add_row(ext, str(s["files"]), str(s["total"]),
                      str(s["code"]), str(s["blank"]), str(s["comment"]))

    table.add_row("─" * 8, "─" * 8, "─" * 8, "─" * 8, "─" * 6, "─" * 6,
                  end_section=True)
    table.add_row("[bold]总计[/bold]", str(grand["files"]), str(grand["total"]),
                  str(grand["code"]), str(grand["blank"]), str(grand["comment"]))

    console.print(table)

    if grand["code"] > 0:
        console.print(f"\n[dim]💡 按人均 300~500 行有效代码/天估算: "
                      f"约 {grand['code'] // 500 + 1} ~ {grand['code'] // 300 + 1} 人天[/dim]")
        console.print(f"\n[dim]💡 不过现在都是AI Coding了，人均每天多少行代码，也说不清了[/dim]")

    return grand


# ==================== 主函数 ====================

def code_statistics(root_dir: str | None = None):
    """
    统计项目代码行数，支持按目录和扩展名汇总。

    Args:
        root_dir: 项目根目录路径，None 则使用全局配置 base_path.project_root。
    """
    if root_dir is None:
        root_dir = global_cfg.base_path.project_root

    root = Path(root_dir).resolve()

    console.print(f"\n[bold]🔍 正在扫描项目: {root}[/bold]\n")

    dir_stats, ext_stats = collect_stats(root)

    # 1. 按目录输出详细统计
    for rel_dir in sorted(dir_stats.keys()):
        print_dir_table(rel_dir, dir_stats[rel_dir])

    # 2. 输出项目总计汇总表
    grand = print_summary_table(root, ext_stats)

    return grand


# 保留 main 作为独立运行的兼容入口
def main():
    """独立命令行运行入口（兼容旧用法）。"""
    code_statistics()


if __name__ == "__main__":
    main()