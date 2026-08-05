import sys
import logging
from pathlib import Path

# import asyncio

# ===== 日志文件配置（必须在其他 import 之前，避免日志落到 stderr）=====
_log_dir = Path(__file__).resolve().parent.parent / "log"
_log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(
            _log_dir / "myclaude.log",
            encoding="utf-8"
        )
    ],
)


import time
import threading

# MyClaudeCLI 的导入延迟到 main() 内部，以便在导入前显示欢迎页面和启动动画


def _print_welcome():
    """打印欢迎页面（纯 print，不依赖任何业务模块）。"""
    title = "MyClaude Code - AI 编程助手"
    # 计算标题的终端显示宽度：ASCII=1列，中文字符=2列
    title_width = sum(2 if ord(c) > 127 else 1 for c in title)
    inner_width = max(46, title_width + 20)  # 两侧各10空格
    print()
    print(f"  ╔{'═' * inner_width}╗")
    print(f"  ║{' ' * inner_width}║")
    print(f"  ║{' ' * 10}{title}{' ' * (inner_width - 10 - title_width)}║")
    print(f"  ║{' ' * inner_width}║")
    print(f"  ╚{'═' * inner_width}╝")
    print()


def _start_spinner(message="正在启动中，请等待几秒..."):
    """启动一个 spinner 线程，返回 (停止事件, 线程对象, 输出锁)。"""
    is_tty = sys.stdout.isatty()
    spinner_chars = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')
    all_done = threading.Event()
    output_lock = threading.Lock()
    start_time = time.time()

    def _spin():
        i = 0
        while not all_done.is_set():
            char = spinner_chars[i % len(spinner_chars)]
            with output_lock:
                if all_done.is_set():
                    break
                if is_tty:
                    elapsed = int(time.time() - start_time)
                    msg = f"  {char} {message} ({elapsed}s)"
                    sys.stdout.write(f"\r{msg.ljust(70)}")
                    sys.stdout.flush()
            time.sleep(0.15)
            i += 1

    thread = threading.Thread(target=_spin, daemon=True)
    thread.start()
    return all_done, thread, output_lock


def _stop_spinner(all_done, thread, output_lock):
    """停止 spinner 线程并清除行。"""
    all_done.set()
    thread.join(timeout=1.0)
    if sys.stdout.isatty():
        with output_lock:
            sys.stdout.write(f"\r{' ' * 70}\r")
            sys.stdout.flush()


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="MyClaude Code CLI")

    parser.add_argument(
        '-r', '--role',
        type=str,
        default='mycode',
        help='角色名称，决定加载哪套提示词（默认：mycode）'
    )
    parser.add_argument(
        '--test-mode',
        action='store_true',
        default=False,
        help='进入测试模式（必须与 --prompt 成对使用）'
    )
    parser.add_argument(
        '--prompt',
        type=str,
        default=None,
        help='测试模式下的用户输入（必须与 --test-mode 成对使用）'
    )
    parser.add_argument(
        '--test-output',
        type=str,
        default=None,
        help='测试模式下输出结构化JSON结果的文件路径（仅与 --test-mode 一起使用）'
    )

    args = parser.parse_args()

    # 验证 --test-mode 与 --prompt 必须成对出现
    if args.test_mode and args.prompt is None:
        print("错误：--test-mode 必须与 --prompt 成对使用。")
        print("用法：MyClaude --test-mode --prompt \"your prompt here\"")
        sys.exit(1)
    if args.prompt is not None and not args.test_mode:
        print("错误：--prompt 必须与 --test-mode 成对使用。")
        print("用法：MyClaude --test-mode --prompt \"your prompt here\"")
        sys.exit(1)

    if args.role != 'mycode':
        print("暂时不支持此类角色，程序退出。")
        sys.exit(1)

    # 测试模式：跳过欢迎页面和 spinner，直接初始化
    if args.test_mode:
        from src.cli.mycli import MyClaudeCLI
        cli = MyClaudeCLI(role=args.role)
        cli.run_test_mode(args.prompt, test_output_path=args.test_output)
        return

    # 交互模式：先显示欢迎页面，再在 spinner 动画下加载模块
    _print_welcome()

    spinner_done, spinner_thread, spinner_lock = _start_spinner()

    try:
        from src.cli.mycli import MyClaudeCLI
        cli = MyClaudeCLI(role=args.role)
    finally:
        _stop_spinner(spinner_done, spinner_thread, spinner_lock)

    print("  ✅ 启动完毕，欢迎使用 MyClaude")
    print()

    cli.run()


if __name__ == "__main__":
    main()
