"""
新功能测试执行器

针对 MyClaude 新增能力，逐个执行验收用例，调用评判 LLM 判定 PASS / FAIL。
"""

from __future__ import annotations

import logging
import time

from .models import TestResult, TestStatus
from .sandbox import SandboxManager
from .judge import LLMJudge

logger = logging.getLogger(__name__)


class NewFeatureRunner:
    """新功能测试用例执行器"""

    def __init__(self, sandbox_mgr: SandboxManager, judge: LLMJudge):
        self._sandbox_mgr = sandbox_mgr
        self._judge = judge

    # ------------------------------------------------------------------

    def execute(self,
                test_cases: list,
                myclaude_root: str | None = None) -> list[TestResult]:
        """执行全部新功能测试用例，返回结果列表"""
        results: list[TestResult] = []

        for case in test_cases:
            logger.info("Running new-feature case [id=%s] %s", case.id, case.description)
            result = self._run_one(case, myclaude_root)
            results.append(result)
            logger.info("Case [id=%s] -> %s", case.id, result.status)

        return results

    # ------------------------------------------------------------------

    def _run_one(self, case, myclaude_root: str | None) -> TestResult:
        t0 = time.perf_counter()

        try:
            # 1. 在沙箱中启动 MyClaude 并发送指令
            sandbox = self._sandbox_mgr.acquire()
            std_out, std_err, exit_code = sandbox.run_myclaude_command(
                user_prompt=case.user_prompt,
                myclaude_root=myclaude_root,
            )

            # 2. 调用评判 LLM
            if exit_code == 0:
                verdict_result = self._judge.evaluate(
                    expected=case.expected_behavior,
                    actual_output=std_out,
                    context=case.description,
                )
                if verdict_result.get("pass"):
                    verdict = TestStatus.PASS
                else:
                    verdict = TestStatus.FAIL
            else:
                verdict = TestStatus.FAIL

            elapsed = round(time.perf_counter() - t0, 2)
            return TestResult(
                test_id=case.id,
                description=case.description,
                status=verdict,
                stdout_preview=std_out[:500] if std_out else "(empty)",
                stderr_preview=std_err[:500] if std_err else "",
                exit_code=exit_code,
                duration_seconds=elapsed,
            )

        except Exception as exc:
            elapsed = round(time.perf_counter() - t0, 2)
            logger.exception("New-feature case [id=%s] crashed", case.id)
            return TestResult(
                test_id=case.id,
                description=case.description,
                status=TestStatus.ERROR,
                stdout_preview=str(exc)[:500],
                stderr_preview="",
                exit_code=-1,
                duration_seconds=elapsed,
            )

        finally:
            self._sandbox_mgr.release()
