"""
代码生成、测试与修复的循环编排引擎
协调 MyCode 与 MyTest 服务，实现"生成→测试→反馈→修复"闭环
"""
import logging
import time
import uuid
from typing import Optional

import httpx

from src.A2A.shared.config import get_config
from src.A2A.shared.models import (
    SpecDocument, TestReport, PreviousAttempt,
    CodeGenerationRequest, CodeGenerationResponse,
    TestRunRequest, TestRunResponse,
    RoundSummary
)
from src.A2A.myorch.context_store import TaskContext, ContextStore

logger = logging.getLogger("myorch.orchestrator")


class Orchestrator:
    """任务编排引擎"""

    def __init__(self):
        self.config = get_config()
        self.context_store = ContextStore()
        self._http_client: Optional[httpx.Client] = None

    @property
    def http_client(self) -> httpx.Client:
        """获取 HTTP 客户端（延迟初始化）"""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=30.0)
        return self._http_client

    def run_task(self, spec: SpecDocument, max_rounds: Optional[int] = None) -> dict:
        """
        执行完整的代码生成→测试→修复循环

        Args:
            spec: 需求规格文档
            max_rounds: 最大循环轮次（覆盖全局默认值）

        Returns:
            包含 task_id, status, final_code, summary 的字典
        """
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        max_rounds = max_rounds or self.config.max_rounds
        melt_down_window = self.config.melt_down_window

        ctx = self.context_store.create_task(task_id, spec)
        ctx.status = "RUNNING"
        self.context_store.save(ctx)

        best_code = ""
        best_pass_rate = 0.0

        logger.info(f"[{task_id}] 开始执行, max_rounds={max_rounds}")

        try:
            for round_num in range(1, max_rounds + 1):
                logger.info(f"[{task_id}] === Round {round_num}/{max_rounds} ===")

                # 1. 调用 MyCode 生成/修复代码
                code_result = self._call_mycode(task_id, round_num, spec, ctx.last_attempt)
                if code_result is None:
                    ctx.status = "ERROR"
                    self.context_store.save(ctx)
                    return self._build_error_result(task_id, ctx, "MyCode 调用失败")

                code = code_result.code
                ctx.save_code_snapshot(round_num, code)

                # 2. 调用 MyTest 执行测试
                test_result = self._call_mytest(task_id, round_num, spec, code)
                if test_result is None:
                    ctx.status = "ERROR"
                    self.context_store.save(ctx)
                    return self._build_error_result(task_id, ctx, "MyTest 调用失败")

                report = test_result.test_report
                ctx.save_test_report(round_num, report)

                # 更新最佳结果
                if report.pass_rate > best_pass_rate:
                    best_pass_rate = report.pass_rate
                    best_code = code

                logger.info(
                    f"[{task_id}] Round {round_num}: "
                    f"通过 {report.passed}/{report.total} ({report.pass_rate: .0%})"
                )

                # 3. 判定终止条件
                # 3a. 全部通过 → 成功
                if report.pass_rate >= 1.0:
                    ctx.status = "SUCCESS"
                    self.context_store.save(ctx)
                    logger.info(f"[{task_id}] 全部测试通过！总轮次: {round_num}")
                    return self._build_success_result(task_id, ctx)

                # 3b. 熔断检测（需要至少 melt_down_window 轮数据）
                if round_num >= melt_down_window:
                    if self._is_melt_down(ctx, window=melt_down_window):
                        ctx.status = "MELT_DOWN"
                        self.context_store.save(ctx)
                        logger.warning(f"[{task_id}] 触发熔断！近 {melt_down_window} 轮无改善")
                        return self._build_meltdown_result(task_id, ctx)

            # 达到最大轮次
            ctx.status = "MAX_ROUNDS_REACHED"
            self.context_store.save(ctx)
            logger.info(f"[{task_id}] 达到最大轮次 {max_rounds}")
            return self._build_max_rounds_result(task_id, ctx)

        except Exception as e:
            logger.error(f"[{task_id}] 执行异常: {e}", exc_info=True)
            ctx.status = "ERROR"
            self.context_store.save(ctx)
            return self._build_error_result(task_id, ctx, str(e))

    def get_task_status(self, task_id: str) -> Optional[dict]:
        """查询任务状态"""
        ctx = self.context_store.get_task(task_id)
        if ctx is None:
            return None

        history = []
        for r in ctx.rounds:
            if r.get("test_report"):
                tr = r["test_report"]
                failed = [
                    d.get("test_id", "?")
                    for d in tr.get("details", [])
                    if d.get("status") in ("FAIL", "ERROR")
                ]
                history.append(RoundSummary(
                    round=r["round"],
                    pass_rate=tr["pass_rate"],
                    failed_tests=failed,
                ).model_dump())

        return {
            "task_id": task_id,
            "status": ctx.status,
            "current_round": ctx.current_round,
            "history": history,
        }

    def _call_mycode(
        self,
        task_id: str,
        round_num: int,
        spec: SpecDocument,
        last_attempt: Optional[dict],
    ) -> Optional[CodeGenerationResponse]:
        """调用 MyCode 服务（带重试）"""
        url = f"{self.config.mycode_url}/a2a/generate_code"
        headers = {}
        if self.config.a2a_auth_token:
            headers["Authorization"] = f"Bearer {self.config.a2a_auth_token}"

        prev = None
        if last_attempt:
            prev = PreviousAttempt(
                code=last_attempt["code"],
                test_report=TestReport.model_validate(last_attempt["test_report"])
                if last_attempt.get("test_report") else None,
            )

        req = CodeGenerationRequest(
            spec=spec,
            task_id=task_id,
            round=round_num,
            previous_attempt=prev,
        )

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.http_client.post(
                    url,
                    json=req.model_dump(),
                    headers=headers,
                    timeout=self.config.code_gen_timeout_sec,
                )
                if resp.status_code == 200:
                    return CodeGenerationResponse.model_validate(resp.json())
                elif resp.status_code == 503:
                    # 服务暂不可用，重试
                    wait = 2 ** attempt
                    logger.warning(f"[{task_id}] MyCode 返回 503, {wait}s 后重试 ({attempt}/{max_retries})")
                    time.sleep(wait)
                elif resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning(f"[{task_id}] MyCode 限流 429, {wait}s 后重试 ({attempt}/{max_retries})")
                    time.sleep(wait)
                else:
                    logger.error(f"[{task_id}] MyCode 返回错误: {resp.status_code} {resp.text[:200]}")
                    return None
            except httpx.TimeoutException:
                logger.warning(f"[{task_id}] MyCode 超时 ({attempt}/{max_retries})")
                time.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"[{task_id}] MyCode 调用异常: {e}")
                time.sleep(2 ** attempt)

        logger.error(f"[{task_id}] MyCode 重试耗尽")
        return None

    def _call_mytest(
        self,
        task_id: str,
        round_num: int,
        spec: SpecDocument,
        code: str,
    ) -> Optional[TestRunResponse]:
        """调用 MyTest 服务（带重试）"""
        url = f"{self.config.mytest_url}/a2a/run_tests"
        headers = {}
        if self.config.a2a_auth_token:
            headers["Authorization"] = f"Bearer {self.config.a2a_auth_token}"

        req = TestRunRequest(
            code=code,
            spec=spec,
            task_id=task_id,
            round=round_num,
        )

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.http_client.post(
                    url,
                    json=req.model_dump(),
                    headers=headers,
                    timeout=self.config.test_exec_timeout_sec,
                )
                if resp.status_code == 200:
                    return TestRunResponse.model_validate(resp.json())
                elif resp.status_code == 503:
                    wait = 2 ** attempt
                    logger.warning(f"[{task_id}] MyTest 返回 503, {wait}s 后重试 ({attempt}/{max_retries})")
                    time.sleep(wait)
                elif resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning(f"[{task_id}] MyTest 限流 429, {wait}s 后重试 ({attempt}/{max_retries})")
                    time.sleep(wait)
                else:
                    logger.error(f"[{task_id}] MyTest 返回错误: {resp.status_code} {resp.text[:200]}")
                    return None
            except httpx.TimeoutException:
                logger.warning(f"[{task_id}] MyTest 超时 ({attempt}/{max_retries})")
                time.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"[{task_id}] MyTest 调用异常: {e}")
                time.sleep(2 ** attempt)

        logger.error(f"[{task_id}] MyTest 重试耗尽")
        return None

    def _is_melt_down(self, ctx: TaskContext, window: int = 3) -> bool:
        """检测是否触发熔断"""
        recent = ctx.last_n_rounds(window)
        if len(recent) < window:
            return False

        pass_rates = [r["test_report"]["pass_rate"] for r in recent]

        # 条件 1: 连续 window 轮通过率不上升（持平或下降）
        non_increasing = all(
            pass_rates[i] >= pass_rates[i + 1]
            for i in range(len(pass_rates) - 1)
        )
        if non_increasing and pass_rates[-1] < 1.0:
            logger.warning(f"[{ctx.task_id}] 熔断条件1触发: pass_rates={pass_rates}")
            return True

        # 条件 2: 最近一轮通过率为 0
        if pass_rates[-1] == 0:
            logger.warning(f"[{ctx.task_id}] 熔断条件2触发: 最近一轮全部失败")
            return True

        return False

    def _build_success_result(self, task_id: str, ctx: TaskContext) -> dict:
        """构建成功结果"""
        return {
            "task_id": task_id,
            "status": "SUCCESS",
            "total_rounds": ctx.current_round,
            "final_code": ctx.best_code,
            "summary": ctx.summary().model_dump(),
        }

    def _build_max_rounds_result(self, task_id: str, ctx: TaskContext) -> dict:
        """构建达到最大轮次结果"""
        return {
            "task_id": task_id,
            "status": "MAX_ROUNDS_REACHED",
            "total_rounds": ctx.current_round,
            "final_code": ctx.best_code,
            "summary": ctx.summary().model_dump(),
        }

    def _build_meltdown_result(self, task_id: str, ctx: TaskContext) -> dict:
        """构建熔断结果"""
        return {
            "task_id": task_id,
            "status": "MELT_DOWN",
            "total_rounds": ctx.current_round,
            "final_code": ctx.best_code,
            "summary": ctx.summary().model_dump(),
        }

    def _build_error_result(self, task_id: str, ctx: TaskContext, error_msg: str) -> dict:
        """构建错误结果"""
        return {
            "task_id": task_id,
            "status": "ERROR",
            "total_rounds": ctx.current_round,
            "final_code": ctx.best_code,
            "error": error_msg,
            "summary": ctx.summary().model_dump(),
        }
