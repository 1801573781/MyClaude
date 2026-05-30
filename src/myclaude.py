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


from src.cli.mycli import MyClaudeCLI  # noqa 402


def main():
    """主函数"""
    import argparse
    import sys

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

    cli = MyClaudeCLI(role=args.role)

    if args.test_mode:
        cli.run_test_mode(args.prompt)
    else:
        cli.run()


if __name__ == "__main__":
    main()
