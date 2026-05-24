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


from src.cli.mycli import MyClaudeCLI


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

    args = parser.parse_args()

    if args.role != 'mycode':
        print("暂时不支持此类角色，程序退出。")
        sys.exit(1)

    cli = MyClaudeCLI(role=args.role)
    cli.run()


if __name__ == "__main__":
    main()
