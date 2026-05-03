import sys
from pathlib import Path

# ===== 兼容 cmd 和 PyCharm 各种运行方式 =====
# 获取 myclaude.py 所在目录（即 src/）
_src_dir = Path(__file__).resolve().parent
# 确保 src/ 在 Python 搜索路径最前面
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))
# ===========================================

# import asyncio
from cli.mycli import MyClaudeCLI


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="MyClaude Code CLI")

    parser.add_argument(
        '-m', '--model',
        type=str,
        default='MiniMax-M2.7',
        help='AI model to use'
    )

    # args = parser.parse_args()

    cli = MyClaudeCLI()
    cli.run()
    # asyncio.run(cli.run())


if __name__ == "__main__":
    main()
