"""
MyOrch 验证编排器

接收验证请求，协调 SystemTest Agent 执行回归测试和新功能测试，
根据判定阈值返回 PASS/FAIL/ERROR 结果。
"""

from __future__ import annotations

import time
from typing import Optional

import httpx

from src.A2A.shared.config import a2a_global_cfg
from src.A2A.shared.models import (
    NewFeatureTestRequest,
    RegressionRequest,
    TestSuiteReport,
    ValidationReport,
    ValidationRequest,
    ValidationStatus,
    generate_task_id,
)
from src.A2A.myorch.context_store import ContextStore


class MyOrchestrator:
    """验证编排器。

    负责：
    1. 接收验证请求，创建任务
    2. 调用 SystemTest Agent 执行回归测试
    3. 调用 SystemTest Agent 执行新功能测试
    4. 根据阈值判定 PASS / FAIL / ERROR
    5. 持久化结果
    """

    def __init__(
        self,
        context_store: Optional[ContextStore] = None,
        system_test_url: Optional[str] = None,
    ):
        """初始化编排器。

        Args:
            context_store: ContextStore 实例。
            system_test_url: SystemTest Agent 的基地址。
        """
        self._store = context_store or ContextStore()
        cfg = a2a_global_cfg.system_test
        self._sys_test_url = system_test_url or f"http://{cfg.host}:{cfg.port}"  # noqa: E231

        # 判定阈值
        self._regression_threshold = a2a_global_cfg.threshold.regression_pass_rate
        self._new_feature_threshold = a2a_global_cfg.threshold.new_feature_pass_rate

        # HTTP 客户端
        self._http = httpx.Client(timeout=float(a2a_global_cfg.docker.test_timeout) + 60)

    # ============================================================
    # 公共接口
    # ============================================================

    def run_validation(self, request: ValidationRequest) -> dict:
        """执行完整验证流程。

        Args:
            request: 验证请求。

        Returns:
            包含 task_id, status, report 的字典。
        """
        task_id = generate_task_id()
        self._store.create_task(task_id, request)
        self._store.update_status(task_id, ValidationStatus.RUNNING)
        self._store.update_progress(task_id, {"phase": "regression", "progress": 0})

        start_time = time.time()

        try:
            # Step 1: 回归测试
            regression_result = self._run_regression(task_id, request)

            self._store.update_progress(
                task_id,
                {
                    "phase": "new_feature",
                    "progress": 0,
                    "regression_done": True,
                },
            )

            # Step 2: 新功能测试
            new_feature_result = self._run_new_feature_tests(task_id, request)

            # Step 3: 判定
            overall_pass = (
                regression_result.pass_rate >= self._regression_threshold
                and new_feature_result.pass_rate >= self._new_feature_threshold
            )

            execution_time = time.time() - start_time

            summary = self._generate_summary(
                regression_result,
                new_feature_result,
                overall_pass,
            )

            report = ValidationReport(
                regression=regression_result,
                new_feature=new_feature_result,
                overall_pass=overall_pass,
                summary=summary,
                execution_time_seconds=execution_time,
            )

            status = ValidationStatus.PASS if overall_pass else ValidationStatus.FAIL
            self._store.update_status(task_id, status)
            self._store.save_report(task_id, report)

            return {
                "task_id": task_id,
                "status": status.value.upper() if status == ValidationStatus.PASS else status.value.upper(),
                "report": report.model_dump(),
            }

        except Exception as e:
            execution_time = time.time() - start_time
            self._store.update_status(task_id, ValidationStatus.ERROR)
            return {
                "task_id": task_id,
                "status": "ERROR",
                "report": {
                    "regression": None,
                    "new_feature": None,
                    "overall_pass": False,
                    "summary": f"验证过程发生错误：{str(e)}",
                    "execution_time_seconds": execution_time,
                },
                "error_message": str(e),
            }

    def get_status(self, task_id: str) -> dict:
        """查询验证任务状态。

        Args:
            task_id: 任务 ID。

        Returns:
            包含 task_id, status, progress 的字典。
        """
        task = self._store.get_task(task_id)
        if task is None:
            return {
                "task_id": task_id,
                "status": "not_found",
                "progress": {},
            }
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "progress": task.progress,
        }

    def get_metrics(self) -> dict:
        """获取全局指标。"""
        return self._store.get_metrics()

    # ============================================================
    # 内部方法
    # ============================================================

    def _run_regression(
        self, task_id: str, request: ValidationRequest
    ) -> TestSuiteReport:
        """调用 SystemTest 执行回归测试。"""
        regression_req = RegressionRequest(
            task_id=task_id,
            test_ids=request.regression_test_ids,
            myclaude_root=a2a_global_cfg.myclaude_root,
        )

        try:
            resp = self._http.post(
                f"{self._sys_test_url}/a2a/run_regression",
                json=regression_req.model_dump(),
            )
            resp.raise_for_status()
            data = resp.json()
            return TestSuiteReport(**data)
        except Exception as e:
            # SystemTest 不可用时返回空结果
            from src.A2A.shared.models import TestDetail, TestResult

            return TestSuiteReport(
                passed=0,
                total=1,
                pass_rate=0.0,
                details=[
                    TestDetail(
                        test_id="SYSTEM_ERROR",
                        description="回归测试调用失败",
                        result=TestResult.ERROR,
                        message=str(e),
                    )
                ],
            )

    def _run_new_feature_tests(
        self, task_id: str, request: ValidationRequest
    ) -> TestSuiteReport:
        """调用 SystemTest 执行新功能测试。"""
        if not request.test_cases:
            from src.A2A.shared.models import TestDetail, TestResult

            return TestSuiteReport(
                passed=0,
                total=0,
                pass_rate=1.0,
                details=[],
            )

        nf_req = NewFeatureTestRequest(
            task_id=task_id,
            test_cases=request.test_cases,
            changed_files=request.changed_files,
            myclaude_root=a2a_global_cfg.myclaude_root,
        )

        try:
            resp = self._http.post(
                f"{self._sys_test_url}/a2a/run_new_feature_tests",
                json=nf_req.model_dump(),
            )
            resp.raise_for_status()
            data = resp.json()
            return TestSuiteReport(**data)
        except Exception as e:
            from src.A2A.shared.models import TestDetail, TestResult

            return TestSuiteReport(
                passed=0,
                total=len(request.test_cases),
                pass_rate=0.0,
                details=[
                    TestDetail(
                        test_id="SYSTEM_ERROR",
                        description="新功能测试调用失败",
                        result=TestResult.ERROR,
                        message=str(e),
                    )
                ],
            )

    # ============================================================
    # 辅助方法
    # ============================================================

    def run_unit_test_orchestration(self, test_cases: list, myclaude_root: str,
                                     report_output_dir: str | None = None) -> dict:
        """执行单元测试编排流程。

        Args:
            test_cases: 单元测试用例列表（字典格式）。
            myclaude_root: MyClaude 项目根目录。
            report_output_dir: 测试报告输出目录（绝对路径），传递给 SystemTest。

        Returns:
            包含 task_id, status, passed, total, pass_rate, details 的字典。
        """
        task_id = generate_task_id()
        start_time = time.time()

        try:
            # 构造 SystemTest 请求
            from src.A2A.test.models import (
                RunUnitTestRequest,
                UnitTestCase,
            )

            unit_test_cases = []
            for tc in test_cases:
                unit_test_cases.append(
                    UnitTestCase(
                        id=tc.get("id", ""),
                        description=tc.get("description", ""),
                        target_module=tc.get("target_module", ""),
                        target_function=tc.get("target_function", ""),
                        test_input=tc.get("test_input", ""),
                        expected_behavior=tc.get("expected_behavior", ""),
                        check_type=tc.get("check_type", "general"),
                    )
                )

            ut_req = RunUnitTestRequest(
                task_id=task_id,
                test_cases=unit_test_cases,
                myclaude_root=myclaude_root or a2a_global_cfg.myclaude_root,
                report_output_dir=report_output_dir,
            )

            # 通过 A2A 协议委派任务给 SystemTest Agent
            resp = self._http.post(
                f"{self._sys_test_url}/a2a/run_unit_tests",
                json=ut_req.model_dump(),
            )
            resp.raise_for_status()
            data = resp.json()

            passed = data.get("passed", 0)
            total = data.get("total", 0)
            pass_rate = data.get("pass_rate", 0.0)
            report_path = data.get("report_path")

            status = "PASS" if pass_rate >= 1.0 else "FAIL"
            execution_time = time.time() - start_time

            return {
                "task_id": task_id,
                "status": status,
                "passed": passed,
                "total": total,
                "pass_rate": pass_rate,
                "details": data.get("details", []),
                "execution_time_seconds": execution_time,
                "report_path": report_path,
            }

        except Exception as e:
            execution_time = time.time() - start_time
            return {
                "task_id": task_id,
                "status": "ERROR",
                "passed": 0,
                "total": len(test_cases),
                "pass_rate": 0.0,
                "details": [],
                "execution_time_seconds": execution_time,
                "error_message": str(e),
            }

    def run_system_test_orchestration(self, test_cases: list, myclaude_root: str,
                                       report_output_dir: str | None = None) -> dict:
        """执行系统测试编排流程。

        Args:
            test_cases: 系统测试用例列表（字典格式）。
            myclaude_root: MyClaude 项目根目录。
            report_output_dir: 测试报告输出目录（绝对路径），传递给 SystemTest。

        Returns:
            包含 task_id, status, passed, total, pass_rate, details 的字典。
        """
        task_id = generate_task_id()
        start_time = time.time()

        try:
            # 构造 SystemTest 请求
            from src.A2A.test.models import RunSystemTestRequest

            st_req = RunSystemTestRequest(
                task_id=task_id,
                test_cases=test_cases,
                myclaude_root=myclaude_root or a2a_global_cfg.myclaude_root,
                report_output_dir=report_output_dir,
            )

            # 通过 A2A 协议委派任务给 SystemTest Agent
            resp = self._http.post(
                f"{self._sys_test_url}/a2a/run_system_tests",
                json=st_req.model_dump(),
            )
            resp.raise_for_status()
            data = resp.json()

            passed = data.get("passed", 0)
            total = data.get("total", 0)
            pass_rate = data.get("pass_rate", 0.0)
            report_path = data.get("report_path")

            status = "PASS" if pass_rate >= 1.0 else "FAIL"
            execution_time = time.time() - start_time

            return {
                "task_id": task_id,
                "status": status,
                "passed": passed,
                "total": total,
                "pass_rate": pass_rate,
                "details": data.get("details", []),
                "execution_time_seconds": execution_time,
                "report_path": report_path,
            }

        except Exception as e:
            execution_time = time.time() - start_time
            return {
                "task_id": task_id,
                "status": "ERROR",
                "passed": 0,
                "total": len(test_cases),
                "pass_rate": 0.0,
                "details": [],
                "execution_time_seconds": execution_time,
                "error_message": str(e),
            }

    @staticmethod
    def _generate_summary(
        regression: TestSuiteReport,
        new_feature: TestSuiteReport,
        overall_pass: bool,
    ) -> str:
        """生成人类可读的验证摘要。"""
        lines = [
            f"验证结果：{'通过' if overall_pass else '未通过'}",
            f"回归测试：{regression.passed}/{regression.total} 通过 "
            f"（{regression.pass_rate: .1%}）",
            f"新功能测试：{new_feature.passed}/{new_feature.total} 通过 "
            f"（{new_feature.pass_rate: .1%}）",
        ]

        # 附加失败详情
        for detail in regression.details:
            if detail.result.value != "pass":
                lines.append(f"  [回归失败] {detail.test_id}: {detail.message}")

        for detail in new_feature.details:
            if detail.result.value != "pass":
                lines.append(f"  [新功能失败] {detail.test_id}: {detail.message}")

        return "\n".join(lines)

    def close(self) -> None:
        """关闭 HTTP 客户端。"""
        self._http.close()
