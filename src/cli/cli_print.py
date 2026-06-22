import os
import time
from contextlib import contextmanager
from html import escape as html_escape  # 用于 HTML 转义
from pathlib import Path

from rich.markup import escape
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.prompt import Prompt
from datetime import datetime
from rich.text import Text


# MyClaude Code 风格的 CLI 界面

# ============================
# HTML 缓冲区（用于 /save 命令）
# ============================
_html_parts = []  # 累积所有屏幕输出的 HTML 片段
_interaction_starts = []  # 记录每次用户输入时 _html_parts 的索引，用于按交互保存


# 自定义样式
STYLES = {
    "user": "bold cyan",
    "assistant": "bold green",
    "system": "dim italic",
    "error": "bold red",
    "info": "bold blue",
    "timestamp": "dim",
    "header": "bold magenta",
    "border": "blue",
}

# 颜色常量
COLORS = {
    "primary": "#7C3AED",  # 紫色
    "secondary": "#10B981",  # 绿色
    "accent": "#F59E0B",  # 橙色
    "background": "#1E1E2E",  # 深色背景
    "surface": "#2D2D3F",  # 表面色
    "text": "#E2E8F0",  # 文本色
    "muted": "#94A3B8",  # 次要文本
}

# 强制 Rich 走 UTF-8 模式，避免在 Docker 沙箱/旧控制台等环境中回退到 GBK legacy 渲染器
# 引发 UnicodeEncodeError（如 emoji ❌✅ 无法被 GBK 编码）
os.environ["PYTHONIOENCODING"] = "utf-8"
console = Console(force_terminal=True, legacy_windows=False)


def _append_html(html_fragment: str):
    """将 HTML 片段追加到全局缓冲区。"""
    _html_parts.append(html_fragment)


def save_buffer_to_file(filepath: str, all: bool = True):
    """
    将累积的 HTML 缓冲区保存为完整的 HTML 文件，可双击用浏览器打开。
    Args:
        filepath: 保存路径
        all: True 保存全部对话；False 只保存最后一次人-LLM 交互（当前 /save 默认行为由调用方决定）
    文件名扩展名决定行为：
      - .html / .htm → 直接保存 HTML
      - .doc / .docx → 保存为 HTML（Word 能打开 HTML 文件）
      - 其他 → 自动添加 .html 后缀
    """
    path = Path(filepath)
    ext = path.suffix.lower()

    # 统一保存为 HTML 格式（Word 也能直接打开 HTML 文件）
    if ext == '.html' or ext == '.htm' or ext == '.doc' or ext == '.docx' or ext == '.pdf':
        # 用原路径
        html_path = path.with_suffix('.html') if ext not in ('.html', '.htm') else path
    else:
        # 无扩展名或未知扩展名，强制改为 .html
        html_path = path.with_suffix('.html')

    # 根据 all 参数选择保存范围
    if all or not _interaction_starts:
        parts_to_save = _html_parts
    else:
        # 保存最后一次交互：从最后一个 _interaction_starts 到末尾
        start_idx = _interaction_starts[-1]
        parts_to_save = _html_parts[start_idx:]

    all_html = "\n".join(parts_to_save)

    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>MyClaude Session</title>
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: #1E1E2E;
    color: #E2E8F0;
    padding: 20px;
    max-width: 900px;
    margin: 0 auto;
    line-height: 1.6;
}}
</style>
</head>
<body>
{all_html}
</body>
</html>"""

    html_path.write_text(full_html, encoding='utf-8')
    return str(html_path)


def clear_screen():
    """清屏"""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_error(content: str):
    """打印错误消息"""
    console.print(f"\n[{STYLES['error']}]❌ Error: {content}[/{STYLES['error']}]\n")
    _append_html(f'<p style="color:#ef4444;">❌ Error: {html_escape(content)}</p>')


def print_info(content: str):
    """打印通用消息"""
    console.print(f"\n[{STYLES['info']}]✅ {content}[/{STYLES['info']}]\n")
    _append_html(f'<p style="color:#3b82f6;">✅ {html_escape(content)}</p>')


def print_user_input(content: str):
    """打印消息"""
    # 记录本次交互的 HTML 起始索引
    _interaction_starts.append(len(_html_parts))

    role_emoji = "👤"
    role_color = "cyan"
    role_name = "You"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 用户消息使用简单的文本显示
    console.print(
        f"\n[{role_color}]{role_emoji} {role_name}[/{role_color}] [{STYLES['timestamp']}]{timestamp}[/{STYLES['timestamp']}]")
    console.print(Panel(
        content,
        border_style=role_color,
        padding=(1, 2),
        width=console.width - 2
    ))

    console.print()

    # 追加到 HTML 缓冲区
    _append_html(f'<div style="margin:12px 0;">'
                 f'<span style="color:#22d3ee;">👤 You</span> '
                 f'<span style="color:#94a3b8;">{html_escape(timestamp)}</span>'
                 f'<div style="border:1px solid #22d3ee; border-radius:4px; padding:8px; margin-top:4px;">'
                 f'{html_escape(content)}'
                 f'</div></div>')


def print_welcome():
    """打印欢迎信息"""

    welcome_text = """
# 🤖 MyClaude Code CLI

Welcome to MyClaude Code CLI! A beautiful terminal interface for AI Coding.

## Features
- 💬 Chat with AI in a modern interface
- 📝 Markdown rendering support
- 🎨 Syntax highlighting for code blocks
- ⌨  Command shortcuts
- 📋 Copy messages to clipboard

## Commands
- `/cls` - Clear Screen
- `/help` - Show this help message
- `/tokens` - Show the tokens statistics
- `/t number` - 展开指定 Turn 的思考过程
- `/r mem` - 清除所有记忆（短期 + 长期 + 工作记忆）
- `/init` - 创建MyClaude的项目工程树
- `/cs` - 统计项目代码行数
- `/ut-c` - 生成单元测试用例（输入 `/ut-c --help` 获取详细信息）
- `/ut-e <测试用例JSON> <日志文件> [报告目录]` - 执行单元测试用例
- `/ut-a2a <测试用例JSON> [报告目录]` - 通过 A2A 协议执行单元测试（MyOrch → SystemTest）
- `/h2m <p1> <p2> [<p3>] [<p4>]` - 将 Session Log HTML 转换为 Markdown（参数值含空格请用引号）
- `/save <filename> [all]` - Save last interaction (or all with "all" flag) to HTML file
- `/quit` or `/exit` - Exit the application

---

*Start typing to begin your conversation!*
"""

    console.print(Panel(
        Markdown(welcome_text),
        title="[bold magenta]MyClaude Code CLI[/bold magenta]",
        border_style="blue",
        padding=(1, 2)
    ))

    # HTML 缓冲区：欢迎信息文本（Markdown 格式无渲染，纯文本存储）
    _append_html('<div style="border:2px solid #3b82f6; border-radius:8px; padding:12px; margin-bottom:16px;">'
                 '<h1 style="color:#c084fc; margin:0 0 12px 0; font-size:28px; border-bottom:2px solid #7C3AED; padding-bottom:8px; text-align:center;">🤖 MyClaude Code CLI</h1>'
                 '<pre style="color:#e2e8f0; white-space:pre-wrap; font-family:inherit;">'
                 + html_escape(welcome_text) +
                 '</pre></div>')


def print_banner():
    """打印 ASCII 艺术 banner"""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║     █████╗ ██████╗  ██████╗██╗  ██╗██╗██╗   ██╗███████╗     ║
║    ██╔══██╗██╔══██╗██╔════╝██║  ██║██║██║   ██║██╔════╝     ║
║    ███████║██████╔╝██║     ███████║██║██║   ██║█████╗       ║
║    ██╔══██║██╔══██╗██║     ██╔══██║██║╚██╗ ██╔╝██╔══╝       ║
║    ██║  ██║██║  ██║╚██████╗██║  ██║██║ ╚████╔╝ ███████╗     ║
║    ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝  ╚══════╝     ║
║                                                              ║
║                    [ CLI Interface v2.0 ]                    ║
╚══════════════════════════════════════════════════════════════╝
    """
    console.print(f"[bold magenta]{banner}[/bold magenta]")
    console.print()


def print_header(session_id):
    """打印头部信息"""
    header_table = Table(show_header=False, box=None, padding=0)
    header_table.add_column(style="cyan")
    header_table.add_column(style="magenta", justify="right")

    time_str = datetime.now().strftime("%H:%M:%S")
    header_table.add_row(
        f"🤖 MyClaude CLI | Session: {session_id}",
        f"🕐 {time_str}"
    )

    console.print(header_table)
    console.print("─" * console.width)


def print_timestamp(timestamp):
    console.print(
        f"\n[bold green]🤖 MyClaude[/bold green] "
        f"[{STYLES['timestamp']}]{timestamp}[/{STYLES['timestamp']}]"
    )


# 打印空行
def print_blank():
    console.print()


def typewriter_print(text, delay=0.005):
    """打字机效果逐字输出，完整支持 Rich markup（如 [bold red]...[/bold red]）

    Args:
        text: 可能包含 Rich markup 的字符串
        delay: 字符间延迟（秒）
    """

    # 1. 将 markup 解析为 Rich 内部带样式的 Text 对象
    rich_text = Text.from_markup(text)

    # 2. 逐字符提取：纯文本字符 + 该偏移位置的合成样式
    for offset, char in enumerate(rich_text.plain):
        # 获取该字符上叠加的所有 span 样式（自动处理嵌套/重叠标签）
        style = rich_text.get_style_at_offset(console, offset)
        # 用 console.print 输出单个字符，应用其样式，不换行
        console.print(char, end="", style=style)
        if delay:
            time.sleep(delay)

    # 3. 全部输出完后统一换行
    console.print()


def typewriter_then_markdown(text: str, delay: float = 0.005):
    """
    先逐字打字机显示纯文本，全部完成后原地替换为 Markdown 渲染效果。
    """
    buffer = ""

    with Live(console=console, refresh_per_second=60) as live:
        # 阶段1：逐字累积，Live 原地刷新纯文本
        for char in text:
            buffer += char
            live.update(buffer)
            if delay:
                time.sleep(delay)

            # 阶段2：判断内容类型，选择最终渲染器
            _CODE_KEYWORDS = ("def ", "import ", "class ", "include", "function ", "const ")
            stripped = text.strip()

            if stripped.startswith("```") or stripped.startswith("#") or "- " in stripped[:100]:
                # 带 Markdown 标记：用 Markdown 渲染
                live.update(Markdown(text))
            elif any(kw in text for kw in _CODE_KEYWORDS):
                # 纯代码（无 Markdown 包裹）：用 Syntax 代码高亮
                live.update(Syntax(text, "python", theme="monokai", line_numbers=False))
            else:
                # 普通文本/聊天回复：用 Markdown
                live.update(Markdown(text))

    # Live 退出后，Markdown 效果保留在终端上
    # 追加到 HTML 缓冲区（Markdown 文本 + 时间戳）
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _append_html(f'<div style="margin:12px 0;">'
                 f'<span style="color:#4ade80;">🤖 MyClaude</span> '
                 f'<span style="color:#94a3b8;">{html_escape(ts)}</span>'
                 f'<pre style="background:#2D2D3F; border-radius:4px; padding:12px; '
                 f'color:#e2e8f0; white-space:pre-wrap; font-family:inherit; margin-top:4px;">'
                 f'{html_escape(text)}'
                 f'</pre></div>')


def show_history(messages):
    """显示对话历史"""
    if not messages:
        print_info("No conversation history yet.")
        return

    table = Table(title="Conversation History", show_header=True)
    table.add_column("ID", style="cyan", width=4)
    table.add_column("Role", style="magenta")
    table.add_column("Preview", style="white")
    table.add_column("Time", style="dim")

    for i, msg in enumerate(messages, 1):
        preview = msg['content'][:50] + "..." if len(msg['content']) > 50 else msg['content']
        preview = preview.replace('\n', ' ')
        table.add_row(
            str(i),
            msg['role'].capitalize(),
            preview,
            datetime.now().strftime("%H:%M")
        )

    console.print(table)


def show_token_count(token_stats: dict):
    """显示详细的 Token 统计（基于 API 返回的精确 usage）"""
    console.print(f"[bold]📊 Token 统计（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）[/bold]")

    stats_table = Table(show_header=False, box=None)
    stats_table.add_column("Metric", style="cyan", min_width=20)
    stats_table.add_column("Value", style="green", min_width=12)

    stats_table.add_row("输入（命中缓存）", f"{token_stats['prompt_cache_hit']:,}")
    stats_table.add_row("输入（未命中缓存）", f"{token_stats['prompt_cache_miss']:,}")
    stats_table.add_row("输出", f"{token_stats['completion_tokens']:,}")
    stats_table.add_row("总计", f"[bold]{token_stats['total']:,}[/bold]")

    console.print(stats_table)
    # HTML 缓冲区
    _append_html(
        f'<p style="color:#f59e0b;">📊 Token（精确）: '
        f'输入(缓存命中)={token_stats["prompt_cache_hit"]:,}, '
        f'输入(未命中)={token_stats["prompt_cache_miss"]:,}, '
        f'输出={token_stats["completion_tokens"]:,}, '
        f'总计={token_stats["total"]:,}</p>'
    )


def print_unknown_cmd(command):
    print_error(f"Unknown command: {command}")
    console.print("[dim]Type /help for available commands[/dim]")


def typewriter_then_collapse(text: str, delay: float = 0.003):
    """
    打字机效果逐字显示推理过程，完成后折叠为 1-2 行预览。
    打字阶段限制 Panel 高度（最多终端可见行数），避免内容超屏时 Rich Live 无法渲染。
    不阻塞主流程，折叠后立即继续执行。
    用户可通过输入 /t 命令展开/折叠完整思考内容。
    """
    # 转义 Rich markup 特殊字符（如方括号），避免解析错误
    text = escape(text)

    set_reasoning_text(text)  # 保存文本供展开/折叠命令使用
    char_count = len(text)
    preview_lines = 2

    base_title = "💭 思考过程"
    # 预留标题、边框、折叠提示等占用的行数，取终端高度的 2/3 作为打字阶段最大行数
    max_visible_lines = max(4, (console.height * 2) // 3 - 4)

    with Live(
        Panel("", title=base_title, border_style="dim", padding=(0, 1)),
        console=console,
        refresh_per_second=30,
        transient=False,
        vertical_overflow="ellipsis"
    ) as live:
        # 阶段1：打字机 —— 只显示最后 max_visible_lines 行，确保 Panel 不超出终端高度
        accumulated = ""
        for _, char in enumerate(text):
            accumulated += char
            # 截取最后 N 行，保持 Live 渲染高度可控
            all_lines = accumulated.split('\n')
            visible_lines = all_lines[-max_visible_lines:]
            display_text = '\n'.join(visible_lines)
            live.update(
                Panel(display_text, title=base_title, border_style="dim", padding=(0, 1)),
                refresh=True
            )
            if delay:
                time.sleep(delay)

        # 阶段2：打字完成后，折叠为 1-2 行预览
        lines = text.split('\n')
        preview = '\n'.join(lines[:preview_lines])
        if len(lines) > preview_lines or len(preview) < char_count:
            preview += "\n···"

        collapsed_panel = Panel(
            preview + f"\n[dim][已折叠，共 {char_count} 字符] — 输入 /t 数字 展开[/dim]",
            title=f"💭 思考过程 ({char_count} 字符) [已折叠]",
            border_style="dim",
            padding=(0, 1)
        )
        live.update(collapsed_panel, refresh=True)

    # 不阻塞，直接继续。完整文本由 QueryLoop 存储，通过 /t 命令访问。
    # 追加完整推理过程到 HTML 缓冲区
    _append_html(f'<details style="margin:8px 0;">'
                 f'<summary style="color:#94a3b8; cursor:pointer;">💭 思考过程 ({char_count} 字符)</summary>'
                 f'<pre style="background:#2D2D3F; border-radius:4px; padding:12px; '
                 f'color:#e2e8f0; white-space:pre-wrap; font-family:inherit; margin-top:8px;">'
                 f'{html_escape(text)}'
                 f'</pre></details>')


def print_tool_call(tool_name: str, params: dict):
    """打印工具调用预告，根据工具类型显示关键参数"""
    if tool_name == "bash":
        detail = params.get("command", "")
    elif tool_name == "done":
        detail = ""
    elif tool_name == "file_view":
        # file_view 额外显示 limit 和 offset 参数
        path = params.get("path", "")
        limit = params.get("limit")
        offset = params.get("offset")
        parts = [path]
        if limit is not None:
            parts.append(f"limit={limit}")
        if offset is not None:
            parts.append(f"offset={offset}")
        detail = "，".join(parts)
    else:
        detail = params.get("path", "")

    # 箭头用亮青色（与工具名一致），工具名亮青加粗，参数亮白
    console.print(f"  [bold cyan]→[/bold cyan] [bold cyan]{tool_name}[/bold cyan] [white]{detail}[/white]")
    # HTML 缓冲区
    _append_html(f'<p style="margin:4px 0 4px 20px; color:#22d3ee;">→ {html_escape(tool_name)} {html_escape(detail)}</p>')


def print_tool_result(tool_name: str, content: str):
    if not content:
        console.print("    [yellow]⚠ 无输出[/yellow]")
        _append_html('<p style="margin:4px 0 4px 40px; color:#f59e0b;">⚠ 无输出</p>')
        return

    # 对于 file_view、use_skill、excel_view，不打印详细内容，只输出简洁提示
    if tool_name in ("file_view", "use_skill", "excel_view"):
        # 注意：不要转义颜色标记部分，只转义可能出现在固定文本中的方括号（但这里固定文本没有方括号，所以无需转义）
        console.print(f"    [green]✓[/green] [{tool_name}]工具执行结果：详细内容略", markup=True)
        _append_html(f'<p style="margin:4px 0 4px 40px; color:#4ade80;">✓ [{tool_name}] 工具执行结果</p>')
        return

    # 其他工具正常打印
    if len(content) < 300:
        safe_content = escape(content)  # 只转义用户内容
        console.print(f"    [green]✓[/green] {safe_content}", markup=True)
        _append_html(f'<p style="margin:4px 0 4px 40px; color:#4ade80;">✓</p>'
                     f'<pre style="background:#2D2D3F; margin:4px 0 4px 40px; padding:8px; '
                     f'border-radius:4px; color:#e2e8f0; white-space:pre-wrap; font-family:inherit; '
                     f'font-size:13px;">{html_escape(content)}</pre>')
    else:
        lines = content.count("\n") + 1
        console.print(f"    [green]✓[/green] [dim]({lines} 行，共 {len(content)} 字符)[/dim]", markup=True)
        safe_content = escape(content)
        console.print(safe_content, markup=True)
        _append_html(f'<p style="margin:4px 0 4px 40px; color:#4ade80;">✓'
                     f' <span style="color:#94a3b8;">({lines} 行，{len(content)} 字符)</span></p>'
                     f'<pre style="background:#2D2D3F; margin:4px 0 4px 40px; padding:8px; '
                     f'border-radius:4px; color:#e2e8f0; white-space:pre-wrap; font-family:inherit; '
                     f'font-size:13px;">{html_escape(content)}</pre>')


# 存储所有轮次的推理过程内容，供展开/折叠命令循环访问
_reasoning_history = []          # 列表元素: (text, turn_number)
_reasoning_cursor = -1           # 当前选中的索引，-1 表示最新轮次
_reasoning_expanded = False


_reasoning_turn_counter = 0


def reset_reasoning():
    """重置推理历史（新对话开始时调用）。"""
    global _reasoning_history, _reasoning_cursor, _reasoning_expanded, _reasoning_turn_counter
    _reasoning_history.clear()
    _reasoning_cursor = -1
    _reasoning_expanded = False
    _reasoning_turn_counter = 0


def set_reasoning_text(text: str):
    """保存推理过程文本（由 query_loop 调用），自动递增轮次号"""
    global _reasoning_history, _reasoning_cursor, _reasoning_turn_counter
    _reasoning_turn_counter += 1
    _reasoning_history.append((text, _reasoning_turn_counter))
    _reasoning_cursor = -1  # 新内容来了，光标回到最新


def reset_reasoning():
    """重置推理历史（新对话开始时调用）。"""
    global _reasoning_history, _reasoning_cursor, _reasoning_expanded, _reasoning_turn_counter
    _reasoning_history.clear()
    _reasoning_cursor = -1
    _reasoning_expanded = False
    _reasoning_turn_counter = 0


def expand_reasoning(turn: int = -1):
    """展开指定轮次的思考过程。

    Args:
        turn: 要展开的轮次号。-1 表示最新轮次；正数表示具体 Turn 编号。
    """
    global _reasoning_history, _reasoning_cursor, _reasoning_expanded
    if not _reasoning_history:
        console.print("[dim]无思考过程可展开[/dim]")
        return

    total = len(_reasoning_history)

    if turn == -1:
        # 展开最新轮次
        idx = total - 1
        _reasoning_cursor = -1
    else:
        # 查找指定 turn 号对应的记录
        idx = None
        for i, (_, t) in enumerate(_reasoning_history):
            if t == turn:
                idx = i
                break
        if idx is None:
            console.print(f"[dim]未找到 Turn {turn} 的思考过程（共 {total} 轮）[/dim]")
            return
        _reasoning_cursor = idx

    text, turn_num = _reasoning_history[idx]
    _reasoning_expanded = True
    console.print(Panel(
        text,
        title=f"💭 思考过程 [Turn {turn_num}] {idx + 1}/{total} ({len(text)} 字符) [已展开]",
        border_style="dim",
        padding=(0, 1)
    ))


def fold_reasoning():
    """折叠当前展开的思考过程"""
    global _reasoning_history, _reasoning_cursor, _reasoning_expanded
    if not _reasoning_history:
        console.print("[dim]无思考过程可折叠[/dim]")
        return

    if not _reasoning_expanded:
        console.print("[dim]当前已是折叠状态[/dim]")
        return

    total = len(_reasoning_history)
    if _reasoning_cursor == -1:
        idx = total - 1
    else:
        idx = _reasoning_cursor

    text, turn = _reasoning_history[idx]
    _show_reasoning_folded(text, turn, total)


def _show_reasoning_folded(text: str, turn: int, total: int):
    """折叠显示推理过程"""
    global _reasoning_expanded
    _reasoning_expanded = False
    lines = text.split('\n')
    preview = '\n'.join(lines[:2])
    char_count = len(text)
    if len(lines) > 2 or len(preview) < char_count:
        preview += "\n···"
    console.print(Panel(
        preview + f"\n[dim][已折叠，共 {char_count} 字符] — 输入 /t 数字 展开[/dim]",
        title=f"💭 思考过程 [Turn {turn}] ({char_count} 字符) [已折叠]",
        border_style="dim",
        padding=(0, 1)
    ))


@contextmanager
def show_status(text: str = "Thinking...", spinner: str = "dots"):
    """
    显示状态动画的上下文管理器。
    用法：
        with show_status("正在执行..."):
            do_something()
    """
    with console.status(f"[bold green]{text}...", spinner=spinner):
        yield  # 把控制权交还给调用方


def get_input() -> str:
    """获取用户输入"""
    try:
        user_input = Prompt.ask(
            f"\n[{STYLES['user']}]➤ You[/] "
        )
        return user_input.strip()
    except (KeyboardInterrupt, EOFError):
        return "/quit"

