#!/usr/bin/env python3
"""
MyClaude 代码行数统计器
"""
from pathlib import Path
from collections import defaultdict
from rich.console import Console
from rich.table import Table


console = Console()


def count_project_lines(root_dir="."):
    root = Path(root_dir).resolve()

    skip_dirs = {'.git', '.idea', '__pycache__', 'venv', '.venv',
                 'node_modules', 'code_output', 'log', 'context', '.memdir'}
    skip_files = {'.gitignore', 'count_lines.py'}

    code_exts = {'.py', '.yaml', '.yml', '.json', '.md', '.txt',
                 '.sh', '.bat', '.ps1', '.toml', '.ini', '.cfg'}

    stats = defaultdict(lambda: {"files": 0, "total": 0, "code": 0,
                                 "blank": 0, "comment": 0})

    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if path.name.startswith('.') or path.name in skip_files:
            continue
        if any(part in skip_dirs for part in path.relative_to(root).parts[:-1]):
            continue

        ext = path.suffix.lower()
        if ext not in code_exts:
            continue

        try:
            content = path.read_text(encoding='utf-8', errors='ignore')
            lines = content.splitlines()
            total = len(lines)
            blank = sum(1 for l in lines if l.strip() == "")

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

            code = total - blank - comment

            stats[ext]["files"] += 1
            stats[ext]["total"] += total
            stats[ext]["code"] += code
            stats[ext]["blank"] += blank
            stats[ext]["comment"] += comment

        except Exception:
            continue

    grand = {"files": 0, "total": 0, "code": 0, "blank": 0, "comment": 0}
    for s in stats.values():
        for k in grand:
            grand[k] += s[k]

    # Rich Table 渲染，彻底解决中英文对齐
    table = Table(
        title=f"\n📁 项目路径: {root}\n",
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

    for ext in sorted(stats.keys()):
        s = stats[ext]
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


if __name__ == "__main__":
    count_project_lines(".")
