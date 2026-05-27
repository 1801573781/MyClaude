"""任务上下文持久化存储

按 task_id 管理每轮的代码快照、测试报告和状态，支持回溯与审计。
"""

import json
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from .models import TaskStatus, TaskSummary, RoundSummary

logger = logging.getLogger("myorch.context_store")

DEFAULT_STORE_PATH = Path("./data/tasks")


class ContextStore:
    """按 task_id 组织的文件系统上下文存储"""

    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or DEFAULT_STORE_PATH
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _task_dir(self, task_id: str) -> Path:
        """获取任务目录"""
        task_dir = self.base_path / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    # ---- 代码快照 ----

    def save_code_snapshot(self, task_id: str, round_num: int, code: str) -> Path:
        """保存某轮生成的代码快照"""
        task_dir = self._task_dir(task_id)
        file_path = task_dir / f"round_{round_num:03d}_code.py"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)
        logger.info(f"代码快照已保存 task_id={task_id} round={round_num}")
        return file_path

    def get_code_snapshot(self, task_id: str, round_num: int) -> Optional[str]:
        """读取某轮的代码快照"""
        task_dir = self._task_dir(task_id)
        file_path = task_dir / f"round_{round_num:03d}_code.py"
        if not file_path.exists():
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    # ---- 测试报告 ----

    def save_test_report(self, task_id: str, round_num: int, report: Dict[str, Any]) -> Path:
        """保存某轮的测试报告"""
        task_dir = self._task_dir(task_id)
        file_path = task_dir / f"round_{round_num:03d}_report.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"测试报告已保存 task_id={task_id} round={round_num}")
        return file_path

    def get_test_report(self, task_id: str, round_num: int) -> Optional[Dict[str, Any]]:
        """读取某轮的测试报告"""
        task_dir = self._task_dir(task_id)
        file_path = task_dir / f"round_{round_num:03d}_report.json"
        if not file_path.exists():
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ---- 任务状态 ----

    def save_task_state(self, task_id: str, state: Dict[str, Any]) -> None:
        """保存任务整体状态"""
        task_dir = self._task_dir(task_id)
        file_path = task_dir / "task_state.json"
        state["updated_at"] = datetime.now().isoformat()
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def get_task_state(self, task_id: str) -> Optional[Dict[str, Any]]:
        """读取任务整体状态"""
        task_dir = self._task_dir(task_id)
        file_path = task_dir / "task_state.json"
        if not file_path.exists():
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ---- 历史记录 ----

    def get_history(self, task_id: str) -> List[Dict[str, Any]]:
        """获取任务的所有历史轮次"""
        task_dir = self._task_dir(task_id)
        history = []
        round_num = 1
        while True:
            code = self.get_code_snapshot(task_id, round_num)
            report = self.get_test_report(task_id, round_num)
            if code is None and report is None:
                break
            history.append({
                "round": round_num,
                "code_snapshot": code,
                "test_report": report,
            })
            round_num += 1
        return history

    # ---- 僵尸任务扫描 ----

    def list_running_tasks(self) -> List[str]:
        """列出所有目录中的任务 ID"""
        if not self.base_path.exists():
            return []
        return [
            d.name for d in self.base_path.iterdir()
            if d.is_dir()
        ]

    def get_task_state_safe(self, task_id: str) -> Optional[Dict[str, Any]]:
        """安全读取任务状态（不存在返回 None）"""
        try:
            return self.get_task_state(task_id)
        except Exception:
            return None

    # ---- 全局指标 ----

    def get_global_metrics(self) -> Dict[str, Any]:
        """统计全局指标"""
        total = 0
        success = 0
        fail = 0
        meltdown = 0
        total_rounds = 0
        latencies = []

        for task_id in self.list_running_tasks():
            state = self.get_task_state_safe(task_id)
            if state is None:
                continue
            total += 1
            status = state.get("status", "")
            if status == "SUCCESS":
                success += 1
            elif status in ("MAX_ROUNDS_REACHED", "ERROR", "TIMEOUT"):
                fail += 1
            elif status == "MELT_DOWN":
                meltdown += 1

            total_rounds += state.get("current_round", 0)
            if state.get("total_time_seconds"):
                latencies.append(state["total_time_seconds"])

        latencies.sort()
        p95_idx = int(len(latencies) * 0.95) if latencies else 0
        p95_latency = latencies[p95_idx] if latencies else 0.0

        return {
            "total_tasks": total,
            "success_count": success,
            "fail_count": fail,
            "meltdown_count": meltdown,
            "avg_rounds": total_rounds / total if total > 0 else 0.0,
            "p95_latency_seconds": p95_latency,
        }