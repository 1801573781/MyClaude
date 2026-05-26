"""
SystemTest A2A 服务入口

提供系统测试执行器的 REST API，包括回归测试和新功能测试两个端点。
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from python_a2a import A2AServer

from .agent_card import get_agent_card
from .models import (
    RunRegressionRequest, RunRegressionResponse,
    RunNewFeatureRequest, RunNewFeatureResponse,
    TestRunState, TestStatus,
)
from .regression_runner import RegressionRunner
from .new_feature_runner import NewFeatureRunner
from .sandbox import SandboxManager
from .judge import LLMJudge

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 应用初始化
# ---------------------------------------------------------------------------

app = FastAPI(title="SystemTest Agent", version="1.0.0")
sandbox_mgr = SandboxManager()
judge = LLMJudge()

# A2A Server 包装
a2a_server = A2AServer(app=app, agent_card=get_agent_card())


@app.get("/.well-known/agent-card.json")
async def serve_agent_card():
    """标准 A2A Agent Card 发现端点"""
    return JSONResponse(content=jsonable_encoder(get_agent_card()))


# ------------------------------ 健康检查 ---------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "system_test",
        "docker_available": sandbox_mgr.is_available(),
    }


# ------------------------------ 回归测试 ---------------------------------

@app.post("/a2a/run_regression", response_model=RunRegressionResponse)
async def run_regression(req: RunRegressionRequest):
    """执行 MyClaude 回归测试套件"""
    t0 = time.perf_counter()
    task_id = req.task_id or f"reg-{int(t0)}"

    logger.info("Starting regression run [task_id=%s] test_ids=%s",
                task_id, req.test_ids or "(all)")

    runner = RegressionRunner(sandbox=sandbox_mgr, judge=judge)
    report = runner.run_all(task_id=task_id,
                            test_ids=req.test_ids,
                            myclaude_root=req.myclaude_root)
    details = report.details

    passed = sum(1 for d in details if d.result == TestStatus.PASS)
    total = len(details)
    elapsed = round(time.perf_counter() - t0, 2)

    logger.info("Regression complete [task_id=%s] %d/%d passed (%.1f%%) in %.1fs",
                task_id, passed, total, passed / total * 100 if total else 0, elapsed)

    return RunRegressionResponse(
        task_id=task_id,
        state=TestRunState.COMPLETED,
        passed=passed,
        total=total,
        pass_rate=passed / total if total else 0.0,
        details=details,
        execution_time_seconds=elapsed,
    )


# ------------------------------ 新功能测试 --------------------------------

@app.post("/a2a/run_new_feature_tests", response_model=RunNewFeatureResponse)
async def run_new_feature_tests(req: RunNewFeatureRequest):
    """执行新功能测试用例"""
    t0 = time.perf_counter()
    task_id = req.task_id or f"nf-{int(t0)}"

    logger.info("Starting new-feature test run [task_id=%s] cases=%d",
                task_id, len(req.test_cases))

    runner = NewFeatureRunner(sandbox_mgr=sandbox_mgr, judge=judge)
    details = runner.execute(test_cases=req.test_cases,
                             myclaude_root=req.myclaude_root)

    passed = sum(1 for d in details if d.status == TestStatus.PASS)
    total = len(details)
    elapsed = round(time.perf_counter() - t0, 2)

    logger.info("New-feature tests complete [task_id=%s] %d/%d passed (%.1f%%) in %.1fs",
                task_id, passed, total, passed / total * 100 if total else 0, elapsed)

    return RunNewFeatureResponse(
        task_id=task_id,
        state=TestRunState.COMPLETED,
        passed=passed,
        total=total,
        pass_rate=passed / total if total else 0.0,
        details=details,
        execution_time_seconds=elapsed,
    )


# ------------------------------ 指标端点 ---------------------------------

@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点（简化版）"""
    return JSONResponse(content={
        "service": "system_test",
        "uptime_seconds": time.perf_counter(),
        "docker_available": sandbox_mgr.is_available(),
    })
