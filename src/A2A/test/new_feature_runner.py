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
            # 1. 在沙箱中启动 MyClaude 并发送指令，获取结构化测试结果
            sandbox = self._sandbox_mgr.acquire()
            std_out, std_err, exit_code, test_data = sandbox.run_myclaude_command_with_test_output(
                user_prompt=case.user_prompt,
                myclaude_root=myclaude_root,
            )

            # 2. 构建评判 actual_output（优先使用结构化 JSON 的关键输出片段）
            actual_output = self._build_actual_output(std_out, test_data)

            # 3. 确定 check_type
            check_type = getattr(case, 'check_type', None) or "general"

            # 4. 调用评判 LLM
            if exit_code == 0:
                verdict_result = self._judge.evaluate(
                    expected=case.expected_behavior,
                    actual_output=actual_output,
                    context=case.description,
                    check_type=check_type,
                )
                judge_verdict = verdict_result.get("verdict")
                if judge_verdict and judge_verdict in (
                    TestStatus.PASS, TestStatus.FAIL,
                    TestStatus.INCONCLUSIVE, TestStatus.ERROR,
                ):
                    verdict = judge_verdict
                elif verdict_result.get("pass"):
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
                stdout_preview=actual_output[:500] if actual_output else "(empty)",
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

    # ------------------------------------------------------------------

    @staticmethod
    def _build_actual_output(std_out: str, test_data: dict | None) -> str:
        """将结构化测试数据与原始 stdout 合并为评判 LLM 的输入。

        优先使用结构化 JSON 中的 key_outputs（LLM 纯文本片段）和 tool_calls
        （工具调用记录），因为 stdout 可能包含 Rich ANSI 转义码干扰评判。
        如果 JSON 不可用，回退到原始 stdout。
        """
        if not test_data:
            return std_out[:2000]

        parts = []

        # 1. 工具调用摘要（结构化，无 Rich 转义码）
        tool_calls = test_data.get("tool_calls", [])
        if tool_calls:
            parts.append("=== 工具调用序列 ===")
            for i, tc in enumerate(tool_calls):
                parts.append(
                    f"[{i+1}] {tc.get('tool', '?')}: "
                    f"params={tc.get('params', {})}, "
                    f"result={tc.get('result', '')[:300]}"
                )

        # 2. LLM 输出的纯文本片段
        key_outputs = test_data.get("key_outputs", [])
        if key_outputs:
            parts.append("=== LLM 关键输出 ===")
            for ko in key_outputs:
                parts.append(ko[:500])

        # 3. 错误信息
        error = test_data.get("error")
        if error:
            parts.append(f"=== 异常信息 ===\n{error}")

        # 4. 退出码
        parts.append(f"=== 退出码 ===\n{test_data.get('exit_code', -1)}")

        # 5. 截断标记
        if test_data.get("is_truncated"):
            parts.append("=== 警告 ===\nLLM 输出被截断（max_tokens 不足）")

        return "\n\n".join(parts)[:2000]
