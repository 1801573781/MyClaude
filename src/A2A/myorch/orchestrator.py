"""任务编排引擎核心逻辑

协调 MyCode 与 MyTest 完成代码生成→测试→修复循环。
实现循环终止判定、智能熔断、重试机制。
"""

import time
import uuid
import logging
from typing import Optional, Dict, Any, Tuple

import httpx

from .models import (
    Spec,
    TaskStatus,
    RoundSummary,
    TaskSummary,
    RunTaskResponse,
)
from .context_store import ContextStore

logger = logging.getLogger("myorch.orchestrator")

# 默认配置
DEFAULT_MYCODE_URL = "http://localhost:8000"
DEFAULT_MYTEST_URL = "http://localhost:8001"
DEFAULT_MAX_ROUNDS = 10
DEFAULT_MELT_DOWN_WINDOW = 3
DEFAULT_CODE_GEN_TIMEOUT = 10
DEFAULT_TEST_EXEC_TIMEOUT = 30
DEFAULT_RETRY_MAX = 3
DEFAULT_RETRY_BACKOFF = [2, 4, 8]  # 秒


class Orchestrator:
    """任务编排器"""

    def __init__(
        self,
        context_store: Optional[ContextStore] = None,
        mycode_url: str = DEFAULT_MYCODE_URL,
        mytest_url: str = DEFAULT_MYTEST_URL,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        melt_down_window: int = DEFAULT_MELT_DOWN_WINDOW,
        code_gen_timeout: int = DEFAULT_CODE_GEN_TIMEOUT,
        test_exec_timeout: int = DEFAULT_TEST_EXEC_TIMEOUT,
        auth_token: Optional[str] = None,
    ):
        self.context_store = context_store or ContextStore()
        self.mycode_url = mycode_url.rstrip("/")
        self.mytest_url = mytest_url.rstrip("/")
        self.max_rounds = max_rounds
        self.melt_down_window = melt_down_window
        self.code_gen_timeout = code_gen_timeout
        self.test_exec_timeout = test_exec_timeout
        self.auth_token = auth_token

    def run_task(self, spec: Spec, max_rounds: Optional[int] = None) -> RunTaskResponse:
        """执行完整的代码生成→测试→修复循环

        Args:
            spec: 需求规格文档对象
            max_rounds: 最大循环轮次（覆盖默认值）

        Returns:
            RunTaskResponse: 任务最终结果
        """
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        effective_max_rounds = max_rounds or self.max_rounds
        start_time = time.time()

        logger.info(f"任务开始 task_id={task_id} max_rounds={effective_max_rounds}")

        # 初始化任务状态
        self.context_store.save_task_state(task_id, {
            "task_id": task_id,
            "status": TaskStatus.RUNNING.value,
            "current_round": 0,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })

        last_attempt: Optional[Dict[str, Any]] = None
        best_code = ""
        best_pass_rate = 0.0
        round_summaries = []
        final_status = TaskStatus.MAX_ROUNDS
        final_code = ""

        try:
            for round_num in range(1, effective_max_rounds + 1):
                self._update_status(task_id, TaskStatus.GENERATING, round_num)
                logger.info(f"第 {round_num}/{effective_max_rounds} 轮开始 task_id={task_id}")

                # 1. 调用 MyCode 生成/修复代码
                code = self._generate_code(task_id, round_num, spec, last_attempt)
                if code is None:
                    final_status = TaskStatus.ERROR
                    final_code = best_code
                    break

                self.context_store.save_code_snapshot(task_id, round_num, code)

                # 2. 调用 MyTest 执行测试
                self._update_status(task_id, TaskStatus.TESTING, round_num)
                test_report = self._run_tests(task_id, round_num, code, spec)
                if test_report is None:
                    final_status = TaskStatus.ERROR
                    final_code = code
                    break

                self.context_store.save_test_report(task_id, round_num, test_report)
                self._update_status(task_id, TaskStatus.EVALUATING, round_num)

                pass_rate = test_report.get("pass_rate", 0.0)
                failed_tests = [
                    d.get("test_id", "?")
                    for d in test_report.get("details", [])
                    if d.get("status") != "PASS"
                ]
                round_summaries.append(RoundSummary(
                    round=round_num,
                    pass_rate=pass_rate,
                    failed_tests=failed_tests,
                ))

                logger.info(f"第 {round_num} 轮完成 pass_rate={pass_rate:.2f} failed={failed_tests}")

                # 3. 判定终止条件
                if pass_rate == 1.0:
                    final_status = TaskStatus.SUCCESS
                    final_code = code
                    logger.info(f"全部测试通过 task_id={task_id} round={round_num}")
                    break

                # 更新最佳结果
                if pass_rate > best_pass_rate:
                    best_pass_rate = pass_rate
                    best_code = code

                # 智能熔断检测（从第 melt_down_window 轮开始）
                if round_num >= self.melt_down_window:
                    if self._is_melt_down(round_summaries):
                        final_status = TaskStatus.MELT_DOWN
                        final_code = best_code
                        logger.warning(f"触发熔断 task_id={task_id} round={round_num}")
                        break

                # 4. 准备下一轮上下文
                last_attempt = {
                    "code": code,
                    "test_report": test_report,
                }

            else:
                # 达到最大轮次
                final_status = TaskStatus.MAX_ROUNDS
                final_code = best_code or code
                logger.warning(f"达到最大轮次 task_id={task_id}")

        except Exception as e:
            logger.error(f"任务执行异常 task_id={task_id}: {e}")
            final_status = TaskStatus.ERROR
            final_code = best_code

        total_time = time.time() - start_time

        # 持久化最终状态
        self._save_final_state(task_id, final_status, final_code, round_summaries, total_time)

        return RunTaskResponse(
            task_id=task_id,
            status=final_status,
            total_rounds=len(round_summaries),
            final_code=final_code,
            summary=TaskSummary(
                rounds=round_summaries,
                total_time_seconds=round(total_time, 1),
                final_verdict=final_status.value,
            ),
        )

    # ---- 内部方法 ----

    def _generate_code(
        self,
        task_id: str,
        round_num: int,
        spec: Spec,
        last_attempt: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """调用 MyCode 服务生成代码（含重试）"""
        payload = {
            "spec": spec.model_dump(),
            "task_id": task_id,
            "round": round_num,
        }
        if last_attempt:
            payload["previous_attempt"] = last_attempt

        for attempt in range(DEFAULT_RETRY_MAX + 1):
            try:
                response = httpx.post(
                    f"{self.mycode_url}/a2a/generate_code",
                    json=payload,
                    timeout=self.code_gen_timeout,
                    headers=self._auth_headers(),
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("code", "")
                elif response.status_code == 503:
                    if attempt < DEFAULT_RETRY_MAX:
                        wait = DEFAULT_RETRY_BACKOFF[attempt]
                        logger.warning(f"MyCode 503，{wait}s 后重试 task_id={task_id}")
                        time.sleep(wait)
                        continue
                logger.error(f"MyCode 调用失败 task_id={task_id} status={response.status_code}")
                return None
            except httpx.TimeoutException:
                logger.error(f"MyCode 超时 task_id={task_id}")
                return None
            except httpx.ConnectError:
                if attempt < DEFAULT_RETRY_MAX:
                    wait = DEFAULT_RETRY_BACKOFF[attempt]
                    logger.warning(f"MyCode 连接失败，{wait}s 后重试 task_id={task_id}")
                    time.sleep(wait)
                    continue
                logger.error(f"MyCode 无法连接 task_id={task_id}")
                return None
        return None

    def _run_tests(
        self,
        task_id: str,
        round_num: int,
        code: str,
        spec: Spec,
    ) -> Optional[Dict[str, Any]]:
        """调用 MyTest 服务执行测试（含重试）"""
        payload = {
            "code": code,
            "spec": spec.model_dump(),
            "task_id": task_id,
            "round": round_num,
        }

        for attempt in range(DEFAULT_RETRY_MAX + 1):
            try:
                response = httpx.post(
                    f"{self.mytest_url}/a2a/run_tests",
                    json=payload,
                    timeout=self.test_exec_timeout,
                    headers=self._auth_headers(),
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("test_report", {})
                elif response.status_code == 503:
                    if attempt < DEFAULT_RETRY_MAX:
                        wait = DEFAULT_RETRY_BACKOFF[attempt]
                        logger.warning(f"MyTest 503，{wait}s 后重试 task_id={task_id}")
                        time.sleep(wait)
                        continue
                logger.error(f"MyTest 调用失败 task_id={task_id} status={response.status_code}")
                return None
            except httpx.TimeoutException:
                logger.error(f"MyTest 超时 task_id={task_id}")
                return None
            except httpx.ConnectError:
                if attempt < DEFAULT_RETRY_MAX:
                    wait = DEFAULT_RETRY_BACKOFF[attempt]
                    logger.warning(f"MyTest 连接失败，{wait}s 后重试 task_id={task_id}")
                    time.sleep(wait)
                    continue
                logger.error(f"MyTest 无法连接 task_id={task_id}")
                return None
        return None

    def _is_melt_down(self, round_summaries: list) -> bool:
        """检测是否触发智能熔断

        熔断条件：
        1. 最近 melt_down_window 轮通过率持续不上升且最后轮 < 1.0
        2. 最近一轮通过率为 0
        """
        if not round_summaries:
            return False

        window = self.melt_down_window
        recent = round_summaries[-window:] if len(round_summaries) >= window else round_summaries
        pass_rates = [r.pass_rate for r in recent]

        # 条件 2: 最近一轮通过率为 0
        if pass_rates[-1] == 0.0:
            return True

        # 条件 1: 最近 window 轮通过率持续不上升
        if len(pass_rates) >= 2:
            non_increasing = all(
                pass_rates[i] >= pass_rates[i + 1]
                for i in range(len(pass_rates) - 1)
            )
            if non_increasing and pass_rates[-1] < 1.0:
                return True

        return False

    def _update_status(self, task_id: str, status: TaskStatus, round_num: int) -> None:
        """更新任务状态"""
        self.context_store.save_task_state(task_id, {
            "task_id": task_id,
            "status": status.value,
            "current_round": round_num,
        })

    def _save_final_state(
        self,
        task_id: str,
        status: TaskStatus,
        final_code: str,
        round_summaries: list,
        total_time: float,
    ) -> None:
        """保存任务最终状态"""
        self.context_store.save_task_state(task_id, {
            "task_id": task_id,
            "status": status.value,
            "current_round": len(round_summaries),
            "total_time_seconds": round(total_time, 1),
            "rounds_summary": [r.model_dump() for r in round_summaries],
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        # 保存最终代码
        if final_code:
            final_path = self.context_store.base_path / task_id / "final_code.py"
            with open(final_path, "w", encoding="utf-8") as f:
                f.write(final_code)

    def _auth_headers(self) -> Dict[str, str]:
        """构建认证头"""
        if self.auth_token:
            return {"Authorization": f"Bearer {self.auth_token}"}
        return {}

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """查询任务状态"""
        state = self.context_store.get_task_state(task_id)
        if state is None:
            return None
        history = self.context_store.get_history(task_id)
        return {
            "task_id": task_id,
            "status": state.get("status", "UNKNOWN"),
            "current_round": state.get("current_round", 0),
            "history": history,
        }