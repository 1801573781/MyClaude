"""
MyOrch Context Store

持久化每次进化的元数据（进化描述、修改文件列表、测试用例、测试报告），
支持按 task_id 查询历史验证记录。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from src.A2A_EX.shared.config import global_cfg
from src.A2A_EX.shared.models import (
    ValidationReport,
    ValidationRequest,
    ValidationStatus,
)
from src.A2A_EX.myorch.models import ValidationTask


class ContextStore:
    """验证任务上下文持久化存储。

    存储格式：JSON 文件
    存储路径：{data_root}/tasks/{task_id}/
    """

    def __init__(self, data_root: Optional[str] = None):
        """初始化 ContextStore。

        Args:
            data_root: 数据存储根目录，默认使用全局配置。
        """
        self._data_root = Path(data_root or global_cfg.data_root)
        self._data_root.mkdir(parents=True, exist_ok=True)

    # ============================================================
    # 任务目录管理
    # ============================================================

    def _task_dir(self, task_id: str) -> Path:
        """获取任务目录路径。"""
        task_dir = self._data_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        """原子写入 JSON 文件。"""
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_path, path)

    @staticmethod
    def _read_json(path: Path) -> Optional[dict]:
        """读取 JSON 文件，文件不存在返回 None。"""
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ============================================================
    # CRUD 操作
    # ============================================================

    def create_task(self, task_id: str, request: ValidationRequest) -> ValidationTask:
        """创建验证任务记录。

        Args:
            task_id: 任务 ID。
            request: 验证请求。

        Returns:
            ValidationTask: 创建的任务对象。
        """
        task_dir = self._task_dir(task_id)

        # 保存元数据
        meta = {
            "task_id": task_id,
            "status": ValidationStatus.PENDING.value,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "evolution_spec": request.evolution_spec.model_dump(),
            "changed_files": request.changed_files,
        }
        self._write_json(task_dir / "meta.json", meta)

        # 保存测试用例
        if request.test_cases:
            self._write_json(
                task_dir / "test_cases.json",
                {"test_cases": [tc.model_dump() for tc in request.test_cases]},
            )

        return ValidationTask(
            task_id=task_id,
            status=ValidationStatus.PENDING,
            request=request,
            created_at=meta["created_at"],
        )

    def get_task(self, task_id: str) -> Optional[ValidationTask]:
        """查询验证任务。

        Args:
            task_id: 任务 ID。

        Returns:
            ValidationTask 或 None。
        """
        task_dir = self._data_root / "tasks" / task_id
        meta = self._read_json(task_dir / "meta.json")
        if meta is None:
            return None

        return ValidationTask(
            task_id=meta["task_id"],
            status=ValidationStatus(meta.get("status", "pending")),
            created_at=meta.get("created_at"),
            updated_at=meta.get("updated_at"),
            progress=meta.get("progress", {}),
        )

    def get_request(self, task_id: str) -> Optional[ValidationRequest]:
        """获取任务的原始验证请求。

        Args:
            task_id: 任务 ID。

        Returns:
            ValidationRequest 或 None。
        """
        task_dir = self._data_root / "tasks" / task_id
        meta = self._read_json(task_dir / "meta.json")
        if meta is None:
            return None

        from src.A2A_EX.shared.models import EvolutionSpec, TestCase

        evo_spec = EvolutionSpec(**meta["evolution_spec"])
        test_cases_data = self._read_json(task_dir / "test_cases.json")
        test_cases = []
        if test_cases_data:
            test_cases = [TestCase(**tc) for tc in test_cases_data.get("test_cases", [])]

        return ValidationRequest(
            evolution_spec=evo_spec,
            changed_files=meta.get("changed_files", []),
            test_cases=test_cases,
            regression_test_ids=meta.get("regression_test_ids"),
        )

    def update_status(self, task_id: str, status: ValidationStatus) -> None:
        """更新任务状态。

        Args:
            task_id: 任务 ID。
            status: 新状态。
        """
        task_dir = self._task_dir(task_id)
        meta = self._read_json(task_dir / "meta.json")
        if meta is None:
            return
        meta["status"] = status.value
        meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_json(task_dir / "meta.json", meta)

    def update_progress(self, task_id: str, progress: dict) -> None:
        """更新任务进度。

        Args:
            task_id: 任务 ID。
            progress: 进度信息。
        """
        task_dir = self._task_dir(task_id)
        meta = self._read_json(task_dir / "meta.json")
        if meta is None:
            return
        meta["progress"] = progress
        meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_json(task_dir / "meta.json", meta)

    def save_report(self, task_id: str, report: ValidationReport) -> None:
        """保存验证报告。

        Args:
            task_id: 任务 ID。
            report: 验证报告。
        """
        task_dir = self._task_dir(task_id)
        self._write_json(task_dir / "report.json", report.model_dump())

    def get_report(self, task_id: str) -> Optional[ValidationReport]:
        """获取验证报告。

        Args:
            task_id: 任务 ID。

        Returns:
            ValidationReport 或 None。
        """
        task_dir = self._data_root / "tasks" / task_id
        report_data = self._read_json(task_dir / "report.json")
        if report_data is None:
            return None
        return ValidationReport(**report_data)

    def list_tasks(self) -> list[str]:
        """列出所有任务 ID。

        Returns:
            任务 ID 列表。
        """
        tasks_dir = self._data_root / "tasks"
        if not tasks_dir.exists():
            return []
        return [
            d.name
            for d in tasks_dir.iterdir()
            if d.is_dir() and (d / "meta.json").exists()
        ]

    # ============================================================
    # 指标统计
    # ============================================================

    def get_metrics(self) -> dict:
        """计算全局指标。

        Returns:
            指标字典。
        """
        tasks_dir = self._data_root / "tasks"
        if not tasks_dir.exists():
            return {
                "total_validations": 0,
                "pass_count": 0,
                "fail_count": 0,
                "error_count": 0,
                "pass_rate": 0.0,
                "avg_execution_time_seconds": 0.0,
            }

        total = 0
        pass_count = 0
        fail_count = 0
        error_count = 0
        total_time = 0.0
        timed_count = 0

        for d in tasks_dir.iterdir():
            if not d.is_dir():
                continue
            meta = self._read_json(d / "meta.json")
            if meta is None:
                continue
            total += 1
            status = meta.get("status", "")
            if status == "pass":
                pass_count += 1
            elif status == "fail":
                fail_count += 1
            elif status == "error":
                error_count += 1

            report_data = self._read_json(d / "report.json")
            if report_data and report_data.get("execution_time_seconds"):
                total_time += report_data["execution_time_seconds"]
                timed_count += 1

        return {
            "total_validations": total,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "error_count": error_count,
            "pass_rate": pass_count / total if total > 0 else 0.0,
            "avg_execution_time_seconds": total_time / timed_count if timed_count > 0 else 0.0,
        }
