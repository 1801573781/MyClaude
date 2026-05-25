"""
MyOrchestrator A2A 服务 — FastAPI 应用入口
A2A 任务编排服务，协调 MyCode 与 MyTest 完成代码生成→测试→修复循环
"""
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from python_a2a import A2AServer

# sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.A2A.shared.config import get_config
from src.A2A.myorch.agent_card import AGENT_CARD
from src.A2A.myorch.models import RunTaskRequest
from src.A2A.myorch.orchestrator import Orchestrator
from src.A2A.myorch.context_store import ContextStore

logger = logging.getLogger("myorch")
app = FastAPI(title="MyOrchestrator", description="任务编排 A2A 服务", version="1.0.0")

# 单例
_orchestrator: Orchestrator | None = None
_context_store: ContextStore | None = None


def get_orchestrator() -> Orchestrator:
    """获取 Orchestrator 单例"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


def get_context_store() -> ContextStore:
    """获取 ContextStore 单例"""
    global _context_store
    if _context_store is None:
        _context_store = ContextStore()
    return _context_store


@app.on_event("startup")
async def startup():
    """启动时初始化并启动僵尸任务清理定时器"""
    store = get_context_store()

    def cleanup_loop():
        while True:
            time.sleep(60)  # 每 60 秒扫描一次
            try:
                store.cleanup_stale_tasks()
            except Exception as e:
                logger.error(f"清理超时任务失败: {e}")

    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()
    logger.info("僵尸任务清理定时器已启动")


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "ok", "service": "myorch"}


@app.get("/.well-known/agent-card.json")
async def agent_card():
    """A2A Agent Card 发现端点"""
    return AGENT_CARD.model_dump()


@app.post("/a2a/run_task")
async def run_task(request: RunTaskRequest):
    """A2A run_task 能力端点 — 提交代码生成任务"""
    orch = get_orchestrator()

    # 校验需求规格
    spec = request.spec
    if spec.language != "python":
        raise HTTPException(status_code=400, detail="目前仅支持 Python 语言")

    if spec.constraints.max_execution_time_ms <= 0:
        raise HTTPException(status_code=400, detail="max_execution_time_ms 必须大于 0")

    # 同步执行编排循环（当前为单任务同步模式）
    # 如需并发，可改为 BackgroundTasks + asyncio.to_thread
    result = orch.run_task(spec, max_rounds=request.max_rounds)

    return result


@app.get("/a2a/tasks/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    orch = get_orchestrator()
    status = orch.get_task_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return status


@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点"""
    store = get_context_store()
    tasks = []
    for task_file in Path(store.store_path).glob("*.json"):
        try:
            import json
            data = json.loads(task_file.read_text(encoding="utf-8"))
            tasks.append(data)
        except Exception:
            pass

    total = len(tasks)
    success = sum(1 for t in tasks if t.get("status") == "SUCCESS")
    failed = sum(1 for t in tasks if t.get("status") in ("MAX_ROUNDS_REACHED", "MELT_DOWN", "ERROR"))
    running = sum(1 for t in tasks if t.get("status") == "RUNNING")

    # 计算平均轮次
    rounds = [t.get("current_round", 0) for t in tasks]
    avg_rounds = sum(rounds) / len(rounds) if rounds else 0

    lines = [
        "# HELP myorch_tasks_total 任务总数",
        "# TYPE myorch_tasks_total gauge",
        f"myorch_tasks_total {total}",
        "# HELP myorch_tasks_success 成功任务数",
        "# TYPE myorch_tasks_success gauge",
        f"myorch_tasks_success {success}",
        "# HELP myorch_tasks_failed 失败任务数",
        "# TYPE myorch_tasks_failed gauge",
        f"myorch_tasks_failed {failed}",
        "# HELP myorch_tasks_running 运行中任务数",
        "# TYPE myorch_tasks_running gauge",
        f"myorch_tasks_running {running}",
        "# HELP myorch_avg_rounds 平均轮次",
        "# TYPE myorch_avg_rounds gauge",
        f"myorch_avg_rounds {avg_rounds:.1f}",
    ]
    return "\n".join(lines)


a2a_server = A2AServer(
    agent_card=AGENT_CARD,
    app=app,
)

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(
        level=get_config().log_level,
        format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "service": "myorch", "task_id": "", "message": "%(message)s"}'
    )
    uvicorn.run(app, host="0.0.0.0", port=8002)
