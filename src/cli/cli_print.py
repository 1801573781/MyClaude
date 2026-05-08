import os
import time
from contextlib import contextmanager

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

console = Console()


def clear_screen():
    """清屏"""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_error(content: str):
    """打印错误消息"""
    console.print(f"\n[{STYLES['error']}]❌ Error: {content}[/{STYLES['error']}]\n")


def print_info(content: str):
    """打印通用消息"""
    console.print(f"\n[{STYLES['info']}]✅ {content}[/{STYLES['info']}]\n")


def print_user_input(content: str):
    """打印消息"""
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
- `/clear` - Clear conversation history
- `/help` - Show this help message
- `/tokens` - Show the tokens statistics
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


def show_token_count(req_tokens, rsp_tokens):
    # 标题单独打印，表格只负责数据
    console.print("[bold]Token Statistics（粗略统计）[/bold]")

    stats_table = Table(show_header=False, box=None)
    stats_table.add_column("Metric", style="cyan", min_width=12)
    stats_table.add_column("Value", style="green", min_width=10)

    stats_table.add_row("req tokens", f"{req_tokens}")
    stats_table.add_row("rsp tokens", f"{rsp_tokens}")

    console.print(stats_table)


def print_unknown_cmd(command):
    print_error(f"Unknown command: {command}")
    console.print("[dim]Type /help for available commands[/dim]")


def print_tool_call(tool_name: str, params: dict):
    """打印工具调用预告，根据工具类型显示关键参数"""
    if tool_name == "bash":
        detail = params.get("command", "")
    elif tool_name == "done":
        detail = ""
    else:
        detail = params.get("path", "")

    # 箭头用亮青色（与工具名一致），工具名亮青加粗，参数亮白
    console.print(f"  [bold cyan]→[/bold cyan] [bold cyan]{tool_name}[/bold cyan] [white]{detail}[/white]")


def print_tool_result(tool_name: str, content: str):
    if not content:
        console.print("    [yellow]⚠ 无输出[/yellow]")
        return

    # 对于 file_view 和 use_skill，不打印详细内容，只输出简洁提示
    if tool_name in ("file_view", "use_skill"):
        # 注意：不要转义颜色标记部分，只转义可能出现在固定文本中的方括号（但这里固定文本没有方括号，所以无需转义）
        console.print(f"    [green]✓[/green] [{tool_name}]工具执行结果：详细内容略", markup=True)
        return

    # 其他工具正常打印
    if len(content) < 300:
        safe_content = escape(content)  # 只转义用户内容
        console.print(f"    [green]✓[/green] {safe_content}", markup=True)
    else:
        lines = content.count("\n") + 1
        console.print(f"    [green]✓[/green] [dim]({lines} 行，共 {len(content)} 字符)[/dim]", markup=True)
        safe_content = escape(content)
        console.print(safe_content, markup=True)


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
            f"\n[{STYLES['user']}]➤ You[/] ",
            default=""
        )
        return user_input.strip()
    except (KeyboardInterrupt, EOFError):
        return "/quit"

