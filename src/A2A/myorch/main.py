"""
MyOrch FastAPI 应用入口

A2A 验证编排服务，提供：
- POST /a2a/validate：提交验证任务
- GET /a2a/validations/{task_id}：查询任务状态
- GET /.well-known/agent-card.json：A2A Agent Card 自动发现
- GET /health：健康检查
- GET /metrics：Prometheus 指标
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from python_a2a import A2AServer

from src.A2A.myorch.agent_card import get_agent_card
from src.A2A.myorch.context_store import ContextStore
from src.A2A.myorch.models import HealthResponse
from src.A2A.myorch.orchestrator import MyOrchestrator

"""
# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
"""

# ============================================================
# 应用初始化
# ============================================================

app = FastAPI(
    title="MyOrch - A2A 验证编排服务",
    version="1.0.0",
    docs_url="/docs",
)

context_store = ContextStore()
orchestrator = MyOrchestrator(context_store=context_store)

# A2A Server 包装
a2a_server = A2AServer(app=app, agent_card=get_agent_card())


# ============================================================
# A2A 协议端点
# ============================================================

@app.get("/.well-known/agent-card.json")
async def serve_agent_card():
    """标准 A2A Agent Card 发现端点"""
    return JSONResponse(content=jsonable_encoder(get_agent_card()))


# ============================================================
# 验证 API
# ============================================================

@app.post("/a2a/validate")
async def validate(request: Request):
    """提交验证任务。

    接收 MyClaude 的进化验证请求，执行回归测试和新功能测试，
    返回 PASS / FAIL / ERROR 判定。
    """
    from src.A2A.shared.models import (
        EvolutionSpec,
        TestCase,
        ValidationRequest,
    )

    body = await request.json()

    # 解析请求体
    try:
        evo_spec = EvolutionSpec(**body["evolution_spec"])
        test_cases = [TestCase(**tc) for tc in body.get("test_cases", [])]
        validation_req = ValidationRequest(
            evolution_spec=evo_spec,
            changed_files=body.get("changed_files", []),
            test_cases=test_cases,
            regression_test_ids=body.get("regression_test_ids"),
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"请求参数校验失败：{str(e)}",
        )

    # 执行验证
    result = orchestrator.run_validation(validation_req)
    return JSONResponse(content=result)


@app.post("/a2a/run_unit_tests")
async def run_unit_tests(request: Request):
    """提交单元测试编排任务。

    接收 CLI 发送的单元测试用例 JSON，委派给 SystemTest Agent 执行，
    返回 PASS / FAIL / ERROR 判定。
    """
    from src.A2A.myorch.models import UnitTestOrchestrationRequest

    body = await request.json()

    try:
        orch_req = UnitTestOrchestrationRequest(**body)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"请求参数校验失败：{str(e)}",
        )

    result = orchestrator.run_unit_test_orchestration(
        test_cases=orch_req.test_cases,
        myclaude_root=orch_req.myclaude_root or "",
        report_output_dir=orch_req.report_output_dir,
    )
    return JSONResponse(content=result)


@app.post("/a2a/run_system_tests")
async def run_system_tests(request: Request):
    """提交系统测试编排任务。

    接收 CLI 发送的系统测试用例 JSON，委派给 SystemTest Agent 执行，
    返回 PASS / FAIL / ERROR 判定。
    """
    from src.A2A.myorch.models import SystemTestOrchestrationRequest

    body = await request.json()

    try:
        orch_req = SystemTestOrchestrationRequest(**body)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"请求参数校验失败：{str(e)}",
        )

    result = orchestrator.run_system_test_orchestration(
        test_cases=orch_req.test_cases,
        myclaude_root=orch_req.myclaude_root or "",
        report_output_dir=orch_req.report_output_dir,
    )
    return JSONResponse(content=result)


@app.get("/a2a/validations/{task_id}")
async def get_validation_status(task_id: str):
    """查询验证任务状态。"""
    result = orchestrator.get_status(task_id)
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 未找到")
    return JSONResponse(content=result)


# ============================================================
# 运维端点
# ============================================================

@app.get("/health")
async def health():
    """健康检查。"""
    return JSONResponse(content=HealthResponse().model_dump())


@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点。"""
    m = orchestrator.get_metrics()
    # Prometheus 文本格式
    lines = [
        "# HELP a2a_ex_validations_total Total number of validations",
        "# TYPE a2a_ex_validations_total gauge",
        f"a2a_ex_validations_total {m['total_validations']}",
        "# HELP a2a_ex_validations_pass_rate Pass rate",
        "# TYPE a2a_ex_validations_pass_rate gauge",
        f"a2a_ex_validations_pass_rate {m['pass_rate']}",
        "# HELP a2a_ex_validation_duration_seconds Average validation duration",
        "# TYPE a2a_ex_validation_duration_seconds gauge",
        f"a2a_ex_validation_duration_seconds {m['avg_execution_time_seconds']}",
    ]
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(content="\n".join(lines) + "\n")


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    import uvicorn
    from src.A2A.shared.config import a2a_global_cfg

    uvicorn.run(
        "src.A2A.myorch.main:app",
        host=a2a_global_cfg.myorch.host,
        port=a2a_global_cfg.myorch.port,
        reload=True,
    )
