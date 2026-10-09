#!/usr/bin/env python3
"""
MyClaude 代码行数统计器 —— 增强版
支持按目录详细统计 + 按扩展名汇总统计
通过 CLI 命令 /cs 调用 code_statistics()。
"""
import os
from pathlib import Path
from collections import defaultdict
from rich.table import Table

from src.utility.config_loader import global_cfg
from src.cli.cli_print import console

# 参照 /init（tree_visualizer.py）的过滤逻辑，复用嵌套 .gitignore 解析与兜底黑名单，
# 确保对 wdbuddy 等嵌套子项目的忽略规则同样生效
from src.cli.tree_visualizer import (
    collect_all_gitignore_rules,
    is_ignored_by_gitignore,
    is_always_skipped,
    SKIP_DIR_NAMES,
)


# ==================== 常量配置 ====================

SKIP_FILES = {'.gitignore'}

CODE_EXTS = {
    '.py', '.yaml', '.yml', '.json', '.md', '.txt',
    '.sh', '.bat', '.ps1', '.toml', '.ini', '.cfg',
    # 前端 / Node 生态（wdbuddy 等 TypeScript 项目）
    '.ts', '.tsx', '.js', '.jsx', '.cjs', '.mjs',
    '.sql', '.html', '.css',
}


# ==================== 注释计数 ====================

def _count_block_comments(lines, start, end, line_prefix=None):
    """统计块注释行数（跨行状态机）；line_prefix 存在时同时统计行注释"""
    comment = 0
    in_block = False
    for line in lines:
        stripped = line.strip()
        if in_block:
            comment += 1
            if end in stripped:
                in_block = False
        elif line_prefix and stripped.startswith(line_prefix):
            comment += 1
        elif stripped.startswith(start):
            comment += 1
            if end not in stripped:
                in_block = True
    return comment


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
    elif ext in ('.ts', '.tsx', '.js', '.jsx', '.cjs', '.mjs'):
        # C 风格注释：// 行注释 + /* */ 跨行块注释
        comment = _count_block_comments(lines, '/*', '*/', line_prefix='//')
    elif ext == '.css':
        comment = _count_block_comments(lines, '/*', '*/')
    elif ext == '.html':
        comment = _count_block_comments(lines, '<!--', '-->')
    elif ext == '.sql':
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('--'):
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
    剪枝式遍历 root，返回两个数据结构：
      dir_stats:  { rel_dir: [ {name, total, code, blank, comment}, ... ] }
      ext_stats:  { ext: {"files": n, "total": n, "code": n, "blank": n, "comment": n} }
    过滤逻辑参照 /init（tree_visualizer.py）：
      - 嵌套 .gitignore 规则（含 wdbuddy 等子项目自身的忽略规则，按 scope 生效）
      - 兜底黑名单（依赖/缓存/虚拟环境目录、包管理器锁文件、*.log）
    遍历时对被忽略目录剪枝（不深入 node_modules 等巨型目录），避免性能劣化。
    """
    root = Path(root).resolve()
    # 收集所有嵌套 .gitignore 规则（含子项目如 wdbuddy 自身的规则）
    gitignore_rules = collect_all_gitignore_rules(root)

    dir_stats = defaultdict(list)
    ext_stats = defaultdict(lambda: {"files": 0, "total": 0, "code": 0,
                                     "blank": 0, "comment": 0})

    for dirpath, dirnames, filenames in os.walk(root):
        dir_path = Path(dirpath)
        try:
            rel_dir = str(dir_path.relative_to(root)).replace('\\', '/')
        except ValueError:
            continue

        # 原地剪枝：被忽略的子目录不深入
        pruned = []
        for d in dirnames:
            # 隐藏目录（.git、.turbo、.idea 等）
            if d.startswith('.'):
                continue
            # 依赖/缓存/虚拟环境目录（node_modules、venv、__pycache__ 等）
            if d == "__pycache__" or d in SKIP_DIR_NAMES:
                continue
            # gitignore 忽略的目录（含嵌套子项目规则，如 wdbuddy）
            sub_rel = f"{rel_dir}/{d}" if rel_dir != '.' else d
            if is_ignored_by_gitignore(sub_rel + '/', True, gitignore_rules):
                continue
            pruned.append(d)
        dirnames[:] = pruned

        # 统计当前目录下的文件
        for fname in filenames:
            # 隐藏文件与固定跳过名单
            if fname.startswith('.') or fname in SKIP_FILES:
                continue
            entry = dir_path / fname
            # 兜底黑名单：包管理器锁文件、*.log 日志文件
            if is_always_skipped(entry, False):
                continue
            # gitignore 忽略的文件（含嵌套子项目规则）
            sub_rel = f"{rel_dir}/{fname}" if rel_dir != '.' else fname
            if is_ignored_by_gitignore(sub_rel, False, gitignore_rules):
                continue

            ext = entry.suffix.lower()
            if ext not in CODE_EXTS:
                continue

            try:
                s = file_stats(entry)
            except Exception:
                continue

            # 按目录存储（key 统一使用正斜杠，与 gitignore 口径一致）
            dir_stats[rel_dir].append({
                "name": fname,
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

    return grand


def build_subdir_summary(dir_stats):
    """将逐目录统计汇总到第一层子目录（更深层级全部归并到一级子目录）"""
    summary = {}
    for rel_dir, files in dir_stats.items():
        top = rel_dir.split('/')[0] if rel_dir != '.' else '.'
        s = summary.setdefault(top, {"files": 0, "total": 0, "code": 0,
                                     "blank": 0, "comment": 0})
        for f in files:
            s["files"] += 1
            s["total"] += f["total"]
            s["code"] += f["code"]
            s["blank"] += f["blank"]
            s["comment"] += f["comment"]
    return summary


def print_subdir_summary_table(root, dir_stats):
    """打印按一级子目录汇总表（二级及以下目录全部归并到一级子目录）"""
    summary = build_subdir_summary(dir_stats)

    table = Table(
        title=f"\n📁 按一级子目录汇总: {root}\n",
        show_header=True,
        header_style="bold cyan",
        box=None,
        padding=(0, 1),
    )

    table.add_column("子目录", style="bold", min_width=12, justify="left")
    table.add_column("文件数", justify="right", min_width=8)
    table.add_column("总行数", justify="right", min_width=8)
    table.add_column("代码行", justify="right", min_width=8)
    table.add_column("空行", justify="right", min_width=6)
    table.add_column("注释", justify="right", min_width=6)

    # 按总行数降序排列，规模最大的子目录一目了然
    ordered = sorted(summary.items(), key=lambda kv: kv[1]["total"], reverse=True)
    for top, s in ordered:
        label = "（根目录）" if top == '.' else f"{top}/"
        table.add_row(label, str(s["files"]), str(s["total"]),
                      str(s["code"]), str(s["blank"]), str(s["comment"]))

    grand = {"files": 0, "total": 0, "code": 0, "blank": 0, "comment": 0}
    for s in summary.values():
        for k in grand:
            grand[k] += s[k]

    table.add_row("─" * 12, "─" * 8, "─" * 8, "─" * 8, "─" * 6, "─" * 6,
                  end_section=True)
    table.add_row("[bold]总计[/bold]", str(grand["files"]), str(grand["total"]),
                  str(grand["code"]), str(grand["blank"]), str(grand["comment"]))

    console.print(table)
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

    # 2. 输出按扩展名的项目总计表
    grand = print_summary_table(root, ext_stats)

    # 3. 输出按一级子目录的汇总表（二级及以下目录归并到一级）
    print_subdir_summary_table(root, dir_stats)

    if grand["code"] > 0:
        console.print(f"\n[dim]💡 按人均 300~500 行有效代码/天估算: "
                      f"约 {grand['code'] // 500 + 1} ~ {grand['code'] // 300 + 1} 人天[/dim]")
        console.print(f"\n[dim]💡 不过现在都是AI Coding了，人均每天多少行代码，也说不清了[/dim]")

    return grand


# 保留 main 作为独立运行的兼容入口
def main():
    """独立命令行运行入口（兼容旧用法）。"""
    code_statistics()


if __name__ == "__main__":
    main()
