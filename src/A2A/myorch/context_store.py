"""
任务上下文持久化存储
记录多轮迭代状态与最优结果，支持超时任务自动清理
"""
import json
import logging
import time
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from src.A2A.shared.config import get_config
from src.A2A.shared.models import SpecDocument, RoundSummary, TaskSummary, TestReport

logger = logging.getLogger("myorch.context_store")


class TaskContext:
    """单个任务的上下文"""

    def __init__(self, task_id: str, spec: SpecDocument):
        self.task_id = task_id
        self.spec = spec
        self.status = "PENDING"
        self.current_round = 0
        self.rounds: list[dict] = []  # [{round, code, test_report, timestamp}]
        self.best_code: str = ""
        self.best_pass_rate: float = 0.0
        self.last_attempt: Optional[dict] = None
        self.created_at = time.time()
        self.updated_at = time.time()

    def save_code_snapshot(self, round_num: int, code: str) -> None:
        """保存代码快照"""
        self.rounds.append({
            "round": round_num,
            "code": code,
            "test_report": None,
            "timestamp": datetime.now().isoformat(),
        })
        self.current_round = round_num
        self.updated_at = time.time()

    def save_test_report(self, round_num: int, report: TestReport) -> None:
        """保存测试报告"""
        for r in self.rounds:
            if r["round"] == round_num:
                r["test_report"] = report.model_dump()
                break

        if report.pass_rate > self.best_pass_rate:
            self.best_pass_rate = report.pass_rate
            for r in self.rounds:
                if r["round"] == round_num and r["code"]:
                    self.best_code = r["code"]
                    break

        self.last_attempt = {
            "code": self._get_code_for_round(round_num),
            "test_report": report.model_dump(),
        }
        self.updated_at = time.time()

    def _get_code_for_round(self, round_num: int) -> str:
        for r in self.rounds:
            if r["round"] == round_num:
                return r.get("code", "")
        return ""

    def last_n_rounds(self, n: int) -> list[dict]:
        """获取最近 n 轮的摘要"""
        recent = [r for r in self.rounds if r.get("test_report")]
        recent.sort(key=lambda x: x["round"])
        return recent[-n:]

    def summary(self) -> TaskSummary:
        """生成任务摘要"""
        summaries = []
        for r in self.rounds:
            if r.get("test_report"):
                tr = r["test_report"]
                failed = [
                    d.get("test_id", "?")
                    for d in tr.get("details", [])
                    if d.get("status") in ("FAIL", "ERROR")
                ]
                summaries.append(RoundSummary(
                    round=r["round"],
                    pass_rate=tr["pass_rate"],
                    failed_tests=failed,
                ))

        total_time = time.time() - self.created_at
        verdict = "ALL_TESTS_PASSED" if self.status == "SUCCESS" else self.status

        return TaskSummary(
            rounds=summaries,
            total_time_seconds=total_time,
            final_verdict=verdict,
        )


class ContextStore:
    """任务上下文持久化存储"""

    def __init__(self):
        config = get_config()
        self.store_path = Path(config.context_store_path)
        self.store_path.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, TaskContext] = {}

    def create_task(self, task_id: str, spec: SpecDocument) -> TaskContext:
        """创建任务上下文"""
        ctx = TaskContext(task_id, spec)
        self._tasks[task_id] = ctx
        self._persist(ctx)
        return ctx

    def get_task(self, task_id: str) -> Optional[TaskContext]:
        """获取任务上下文"""
        if task_id in self._tasks:
            return self._tasks[task_id]

        # 尝试从磁盘恢复
        task_file = self.store_path / f"{task_id}.json"
        if task_file.exists():
            try:
                data = json.loads(task_file.read_text(encoding="utf-8"))
                ctx = TaskContext(
                    task_id=data["task_id"],
                    spec=SpecDocument.model_validate(data["spec"]),
                )
                ctx.status = data.get("status", "PENDING")
                ctx.current_round = data.get("current_round", 0)
                ctx.rounds = data.get("rounds", [])
                ctx.best_code = data.get("best_code", "")
                ctx.best_pass_rate = data.get("best_pass_rate", 0.0)
                ctx.created_at = data.get("created_at", time.time())
                ctx.updated_at = data.get("updated_at", time.time())
                self._tasks[task_id] = ctx
                return ctx
            except Exception as e:
                logger.warning(f"恢复任务 {task_id} 失败: {e}")

        return None

    def save(self, ctx: TaskContext) -> None:
        """持久化任务上下文"""
        self._persist(ctx)

    def _persist(self, ctx: TaskContext) -> None:
        """写入磁盘"""
        task_file = self.store_path / f"{ctx.task_id}.json"
        data = {
            "task_id": ctx.task_id,
            "spec": ctx.spec.model_dump(),
            "status": ctx.status,
            "current_round": ctx.current_round,
            "rounds": ctx.rounds,
            "best_code": ctx.best_code,
            "best_pass_rate": ctx.best_pass_rate,
            "created_at": ctx.created_at,
            "updated_at": ctx.updated_at,
        }
        task_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def cleanup_stale_tasks(self, max_age_minutes: int = 10) -> int:
        """清理超时任务"""
        cleaned = 0
        cutoff = time.time() - max_age_minutes * 60

        for task_id, ctx in list(self._tasks.items()):
            if ctx.status == "RUNNING" and ctx.updated_at < cutoff:
                ctx.status = "TIMEOUT"
                self._persist(ctx)
                del self._tasks[task_id]
                cleaned += 1

        # 扫描磁盘上的任务文件
        for task_file in self.store_path.glob("*.json"):
            try:
                data = json.loads(task_file.read_text(encoding="utf-8"))
                if data.get("status") == "RUNNING" and data.get("updated_at", 0) < cutoff:
                    data["status"] = "TIMEOUT"
                    task_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    cleaned += 1
            except (json.JSONDecodeError, OSError):
                pass

        if cleaned > 0:
            logger.info(f"清理了 {cleaned} 个超时任务")
        return cleaned
