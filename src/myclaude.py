import sys
import logging
from pathlib import Path

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

'''
# ===========================================

# ===== 兼容 cmd 和 PyCharm 各种运行方式 =====
# 获取 myclaude.py 所在目录（即 src/）
_src_dir = Path(__file__).resolve().parent
# 确保 src/ 在 Python 搜索路径最前面
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))
# ===========================================
'''

# import asyncio
from src.cli.mycli import MyClaudeCLI


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


if __name__ == "__main__":
    main()
