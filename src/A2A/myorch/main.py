"""MyOrchestrator A2A 服务 — FastAPI 应用入口

任务编排引擎，协调 MyCode 与 MyTest 完成代码生成→测试→修复循环。
"""

import time
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse

from .models import (
    Spec,
    TaskStatus,
    RunTaskRequest,
    RunTaskResponse,
    GetTaskStatusResponse,
    GlobalMetrics,
    ErrorResponse,
)
from .agent_card import MYORCH_AGENT_CARD
from .orchestrator import Orchestrator
from .context_store import ContextStore

logger = logging.getLogger("myorch")
logging.basicConfig(level=logging.INFO, format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "service": "myorch", "message": "%(message)s"}')


# ---- 初始化 ----
context_store = ContextStore(base_path=Path("./data/tasks"))
orchestrator = Orchestrator(
    context_store=context_store,
    mycode_url="http://localhost:8000",
    mytest_url="http://localhost:8001",
    max_rounds=10,
    melt_down_window=3,
    code_gen_timeout=10,
    test_exec_timeout=30,
    auth_token=None,  # 从环境变量或配置文件注入
)

app = FastAPI(title="MyOrchestrator - 任务编排服务", version="1.0.0")


@app.get("/health")
async def health_check():
    """健康检查端点"""
    # 同步检查下游服务健康状态
    downstream = {"mycode": "unknown", "mytest": "unknown"}
    try:
        import httpx
        mc_resp = httpx.get("http://localhost:8000/health", timeout=2)
        downstream["mycode"] = "ok" if mc_resp.status_code == 200 else "unhealthy"
    except Exception:
        downstream["mycode"] = "unreachable"
    try:
        mt_resp = httpx.get("http://localhost:8001/health", timeout=2)
        downstream["mytest"] = "ok" if mt_resp.status_code == 200 else "unhealthy"
    except Exception:
        downstream["mytest"] = "unreachable"

    return {"status": "ok", "service": "myorch", "downstream": downstream}


@app.get("/.well-known/agent-card.json")
async def agent_card():
    """返回 A2A Agent Card"""
    return MYORCH_AGENT_CARD


@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点"""
    return context_store.get_global_metrics()


@app.post("/a2a/run_task", response_model=RunTaskResponse)
async def run_task(request: RunTaskRequest):
    """提交代码生成任务，同步返回最终结果"""
    logger.info(f"收到任务请求 title={request.spec.title} max_rounds={request.max_rounds}")

    try:
        result = orchestrator.run_task(
            spec=request.spec,
            max_rounds=request.max_rounds,
        )
        logger.info(f"任务完成 task_id={result.task_id} status={result.status.value}")
        return result
    except Exception as e:
        logger.error(f"任务执行异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/a2a/tasks/{task_id}", response_model=GetTaskStatusResponse)
async def get_task_status(task_id: str):
    """查询任务当前状态与进度"""
    state = orchestrator.get_task_status(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    return GetTaskStatusResponse(
        task_id=task_id,
        status=TaskStatus(state.get("status", "UNKNOWN")),
        current_round=state.get("current_round", 0),
        history=state.get("history", []),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理"""
    logger.error(f"未处理的异常: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "InternalError", "message": str(exc)},
    )


# ---- 僵尸任务清理后台线程 ----

import threading

def cleanup_zombie_tasks():
    """后台清理超过 10 分钟的 RUNNING 状态任务"""
    import time as _time
    while True:
        _time.sleep(60)  # 每分钟扫描一次
        try:
            for task_id in context_store.list_running_tasks():
                state = context_store.get_task_state_safe(task_id)
                if state is None:
                    continue
                status = state.get("status", "")
                if status in ("RUNNING", "GENERATING", "TESTING", "EVALUATING"):
                    updated = state.get("updated_at", "")
                    if updated:
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(updated)
                            delta = (datetime.now() - dt).total_seconds()
                            if delta > 600:  # 10 分钟
                                context_store.save_task_state(task_id, {
                                    "task_id": task_id,
                                    "status": "TIMEOUT",
                                    "current_round": state.get("current_round", 0),
                                })
                                logger.warning(f"僵尸任务清理 task_id={task_id}")
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"僵尸任务扫描异常: {e}")


cleanup_thread = threading.Thread(target=cleanup_zombie_tasks, daemon=True)
cleanup_thread.start()