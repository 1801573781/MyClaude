"""Token 统计模块。

在后台默默记录每次 LLM 调用的 token 消耗：
- 月度明细（CSV，每月一个文件，每行对应一次 LLM 调用/turn）
- 汇总统计（3个CSV文件：token_statistics_summary_day/month/year.csv）

Excel 公式说明：
  计费列使用 =E2*F2 格式的公式，在 Excel 中打开 CSV 时自动计算。
  单价列留空，由用户手动填写后，计费列自动计算。
"""

import csv
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 写文件锁（simple_chat 可能在记忆系统的线程中调用）
_write_lock = threading.Lock()

_STATS_DIR: Optional[Path] = None

# 明细表头：14 列
_DETAIL_HEADERS = [
    "时间", "模型名称", "Query", "Turn",
    "输入(命中缓存)-数量", "输入(命中缓存)-单价", "输入(命中缓存)-计费",
    "输入(未命中缓存)-数量", "输入(未命中缓存)-单价", "输入(未命中缓存)-计费",
    "输出-数量", "输出-单价", "输出-计费",
    "总计费",
]

# 汇总表头（3个文件共用）——与明细表头一致，含单价和计费列
_SUMMARY_HEADERS = [
    "周期值", "模型名称",
    "输入(命中缓存)-数量", "输入(命中缓存)-单价", "输入(命中缓存)-计费",
    "输入(未命中缓存)-数量", "输入(未命中缓存)-单价", "输入(未命中缓存)-计费",
    "输出-数量", "输出-单价", "输出-计费",
    "总计费",
]


def _get_stats_dir() -> Path:
    """获取统计文件目录（单例缓存）。"""
    global _STATS_DIR
    if _STATS_DIR is not None:
        return _STATS_DIR
    from src.utility.config_loader import global_cfg
    _STATS_DIR = Path(global_cfg.base_path.project_root) / "token_statistics"
    _STATS_DIR.mkdir(parents=True, exist_ok=True)
    return _STATS_DIR


def get_stats_dir_path() -> str:
    """返回统计文件目录的绝对路径字符串，供 CLI 显示用。"""
    return str(_get_stats_dir())


def _get_monthly_detail_path() -> Path:
    """获取当月明细文件路径。"""
    return _get_stats_dir() / f"token_statistics_{datetime.now().strftime('%Y%m')}.csv"


def _get_summary_path(period_type: str) -> Path:
    """获取汇总统计文件路径。period_type: day/month/year"""
    return _get_stats_dir() / f"token_statistics_summary_{period_type}.csv"


def _ensure_detail_format(filepath: Path) -> int:
    """确保明细文件格式正确，返回现有数据行数。

    - 文件不存在 → 返回 0
    - 文件存在但表头不匹配（旧格式）→ 备份旧文件，返回 0
    - 文件存在且表头匹配 → 返回数据行数
    """
    if not filepath.exists():
        return 0

    with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != _DETAIL_HEADERS:
            # 旧格式，备份
            backup = filepath.with_name(filepath.stem + "_old" + filepath.suffix)
            filepath.rename(backup)
            logger.info(f"旧格式明细文件已备份: {backup}")
            return 0
        # 表头匹配，计算数据行数
        return sum(1 for _ in reader)


def _ensure_summary_format(filepath: Path) -> bool:
    """确保汇总文件格式正确。

    返回 True 表示文件可用（存在且格式正确），False 表示需要新建。
    """
    if not filepath.exists():
        return False

    with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != _SUMMARY_HEADERS:
            backup = filepath.with_name(filepath.stem + "_old" + filepath.suffix)
            filepath.rename(backup)
            logger.info(f"旧格式汇总文件已备份: {backup}")
            return False
    return True


def _update_summary_file(
    filepath: Path, period_value: str, model_name: str,
    cached: int, uncached: int, output: int,
) -> None:
    """更新一个汇总统计 CSV 文件。

    算法：读取全量 → 内存中查找并累加 → 全量写回。
    文件行数很少（每天/月/年×模型数），读写极快。
    汇总表头与明细表头一致（含单价、计费列），计费列使用 Excel 公式，
    单价列留空由用户手动填写。
    """
    existing_rows: List[List[str]] = []
    if _ensure_summary_format(filepath):
        with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            next(reader, None)  # 跳过表头
            for row in reader:
                if row and len(row) >= 12:
                    existing_rows.append(row)

    # 更新或插入
    found = False
    for row in existing_rows:
        if row[0] == period_value and row[1] == model_name:
            # 累加数量列（保留原有单价列不变）
            row[2] = str(int(row[2] or 0) + cached)
            row[5] = str(int(row[5] or 0) + uncached)
            row[8] = str(int(row[8] or 0) + output)
            found = True
            break

    if not found:
        # 新行：数量有值，单价留空，计费由公式计算
        existing_rows.append([
            period_value, model_name,
            str(cached), "", "",        # 输入(命中缓存): 数量/单价/计费
            str(uncached), "", "",      # 输入(未命中缓存): 数量/单价/计费
            str(output), "", "",        # 输出: 数量/单价/计费
            "",                          # 总计费
        ])

    # 全量写回（带 Excel 公式）
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(_SUMMARY_HEADERS)
        for i, row in enumerate(existing_rows):
            excel_row = i + 2  # +1 for header (row 1), +1 for 1-based
            # 确保行有 12 列
            while len(row) < 12:
                row.append("")
            # 重新生成计费公式（基于当前行号，确保行号正确）
            row[4] = f"=C{excel_row}*D{excel_row}"                # 命中缓存计费
            row[7] = f"=F{excel_row}*G{excel_row}"                # 未命中缓存计费
            row[10] = f"=I{excel_row}*J{excel_row}"               # 输出计费
            row[11] = f"=E{excel_row}+H{excel_row}+K{excel_row}"  # 总计费
            writer.writerow(row[:12])


def record_token_usage(
    model_name: str,
    prompt_tokens: int,
    cached_tokens: int,
    completion_tokens: int,
    query: str = "",
    turn: str = "",
) -> None:
    """记录一次 LLM 调用的 token 消耗。

    同时写入月度明细 CSV（1行，含 Excel 公式）和 3 个汇总统计 CSV。
    线程安全，异常不抛出。

    Args:
        model_name: 模型名称
        prompt_tokens: 总输入 token 数
        cached_tokens: 缓存命中的 token 数
        completion_tokens: 输出 token 数
        query: 用户 Query 信息或 CLI 命令（如用户输入文本或 /mem extract）
        turn: 轮次标识，如 "turn1"、"turn2_followup1"、"CLI_COMMAND"
    """
    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uncached = max(0, prompt_tokens - cached_tokens)

        with _write_lock:
            # 1. 写入月度明细 CSV（1行，含 Excel 公式）
            detail_path = _get_monthly_detail_path()
            data_rows = _ensure_detail_format(detail_path)
            excel_row = data_rows + 2  # +1 for header (row 1), +1 for 1-based

            file_exists = detail_path.exists()
            with open(detail_path, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(_DETAIL_HEADERS)
                writer.writerow([
                    now_str,           # A: 时间
                    model_name,        # B: 模型名称
                    query,             # C: Query
                    turn,              # D: Turn
                    cached_tokens,     # E: 输入(命中缓存)-数量
                    "",                # F: 输入(命中缓存)-单价
                    f"=E{excel_row}*F{excel_row}",  # G: 输入(命中缓存)-计费
                    uncached,          # H: 输入(未命中缓存)-数量
                    "",                # I: 输入(未命中缓存)-单价
                    f"=H{excel_row}*I{excel_row}",  # J: 输入(未命中缓存)-计费
                    completion_tokens, # K: 输出-数量
                    "",                # L: 输出-单价
                    f"=K{excel_row}*L{excel_row}",  # M: 输出-计费
                    f"=G{excel_row}+J{excel_row}+M{excel_row}",  # N: 总计费
                ])

            # 2. 更新 3 个汇总统计 CSV
            now = datetime.now()
            _update_summary_file(
                _get_summary_path("day"),
                now.strftime("%Y-%m-%d"),
                model_name, cached_tokens, uncached, completion_tokens,
            )
            _update_summary_file(
                _get_summary_path("month"),
                now.strftime("%Y-%m"),
                model_name, cached_tokens, uncached, completion_tokens,
            )
            _update_summary_file(
                _get_summary_path("year"),
                now.strftime("%Y"),
                model_name, cached_tokens, uncached, completion_tokens,
            )

    except Exception as e:
        logger.warning(f"记录 token 统计失败: {e}")


def get_token_summary() -> Dict:
    """读取汇总统计文件，返回当前累计的 token 统计。

    汇总 day 文件中所有行的数据，得到全量累计值。

    Returns:
        dict: prompt_cache_hit, prompt_cache_miss, completion_tokens, total, stats_dir
    """
    summary_path = _get_summary_path("day")
    result = {
        "prompt_cache_hit": 0,
        "prompt_cache_miss": 0,
        "completion_tokens": 0,
        "total": 0,
        "stats_dir": str(_get_stats_dir()),
    }

    if not summary_path.exists():
        return result

    try:
        total_hit = 0
        total_miss = 0
        total_output = 0

        with open(summary_path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            next(reader, None)  # 跳过表头
            for row in reader:
                if row and len(row) >= 12:
                    total_hit += int(row[2] or 0)
                    total_miss += int(row[5] or 0)
                    total_output += int(row[8] or 0)

        result["prompt_cache_hit"] = total_hit
        result["prompt_cache_miss"] = total_miss
        result["completion_tokens"] = total_output
        result["total"] = total_hit + total_miss + total_output
    except Exception as e:
        logger.warning(f"读取 token 统计失败: {e}")

    return result
