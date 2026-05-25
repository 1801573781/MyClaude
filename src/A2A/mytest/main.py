"""
MyTest A2A 服务 — FastAPI 应用入口
封装测试智能体 B，在 Docker 沙箱中执行代码并返回测试报告
"""
import logging
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header
from python_a2a import A2AServer

# sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.A2A.shared.config import get_config
from src.A2A.mytest.agent_card import AGENT_CARD
from src.A2A.mytest.models import RunTestsRequest, RunTestsResponse
from src.A2A.mytest.sandbox import DockerSandbox
from src.A2A.shared.models import TestReport, TestDetail

logger = logging.getLogger("mytest")
app = FastAPI(title="MyTest", description="测试执行 A2A 服务", version="1.0.0")


def verify_auth(authorization: str | None = Header(default=None)) -> None:
    """验证 Bearer Token"""
    config = get_config()
    if not config.a2a_auth_token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    if token != config.a2a_auth_token:
        raise HTTPException(status_code=401, detail="Invalid token")


def build_test_script(code: str, spec) -> str:
    """根据需求规格的验收标准构建测试脚本"""
    lines = [
        "import sys",
        "import json",
        "import traceback",
        "",
        code,
        "",
        "results = []",
        "",
    ]

    for i, criterion in enumerate(spec.acceptance_criteria, start=1):
        test_id = f"TC-{i:02d}"
        input_expr = criterion.input
        expected = criterion.expected_output

        lines.append(f"# {test_id}: {input_expr} 应返回 {expected}")
        lines.append("try:")
        lines.append(f"    actual = {input_expr}")
        lines.append(f"    expected_val = {expected}")
        lines.append("    actual_str = str(actual)")
        lines.append("    if actual_str == str(expected_val):")
        lines.append(f"        results.append({{")
        lines.append(f"            'test_id': '{test_id}',")
        lines.append(f"            'status': 'PASS',")
        lines.append(f"            'description': '{input_expr} 应返回 {expected}',")
        lines.append(f"            'expected': '{expected}',")
        lines.append(f"            'actual': actual_str,")
        lines.append(f"            'error_message': None")
        lines.append(f"        }})")
        lines.append("    else:")
        lines.append(f"        results.append({{")
        lines.append(f"            'test_id': '{test_id}',")
        lines.append(f"            'status': 'FAIL',")
        lines.append(f"            'description': '{input_expr} 应返回 {expected}',")
        lines.append(f"            'expected': '{expected}',")
        lines.append(f"            'actual': actual_str,")
        lines.append(f"            'error_message': f'值不匹配: 期望 {{expected_val}}, 实际 {{actual}}'")
        lines.append(f"        }})")
        lines.append("except Exception as e:")
        lines.append(f"    exc_info = traceback.format_exc()")
        lines.append(f"    results.append({{")
        lines.append(f"        'test_id': '{test_id}',")
        lines.append(f"        'status': 'ERROR',")
        lines.append(f"        'description': '{input_expr} 应返回 {expected}',")
        lines.append(f"        'expected': '{expected}',")
        lines.append(f"        'actual': 'N/A',")
        lines.append(f"        'error_message': f'{{type(e).__name__}}: {{str(e)}}'")
        lines.append(f"    }})")
        lines.append("")

    lines.append("print(json.dumps(results, ensure_ascii=False))")
    return "\n".join(lines)


def call_agent_b(request: RunTestsRequest) -> RunTestsResponse:
    """
    内部调用测试智能体 B（MyClaude 实例，sys_prompt=测试生成）
    当前实现：根据验收标准构建测试脚本，在沙箱中执行
    """
    start_time = time.time()
    logger.info(f"[{request.task_id}] Round {request.round}: 开始执行测试")

    # 构建测试脚本
    test_script = build_test_script(request.code, request.spec)

    # 在沙箱中执行
    sandbox = DockerSandbox()
    result = sandbox.execute(test_script)

    # 解析测试结果
    details = []
    passed = 0
    total = 0

    if result.exit_code == 0 and result.stdout.strip():
        try:
            import json
            raw_details = json.loads(result.stdout.strip())
            for d in raw_details:
                detail = TestDetail(**d)
                details.append(detail)
                if detail.status == "PASS":
                    passed += 1
                total += 1
        except Exception as e:
            logger.warning(f"解析测试结果失败: {e}, stdout={result.stdout[:200]}")
            details.append(TestDetail(
                test_id="PARSE_ERROR",
                status="ERROR",
                description="无法解析测试输出",
                error_message=str(e),
            ))
            total = 1
    else:
        details.append(TestDetail(
            test_id="EXEC_ERROR",
            status="ERROR",
            description="代码执行失败",
            error_message=result.stderr or result.stdout or "未知错误",
        ))
        total = 1

    pass_rate = passed / total if total > 0 else 0.0
    elapsed_ms = int((time.time() - start_time) * 1000)

    report = TestReport(
        passed=passed,
        total=total,
        pass_rate=pass_rate,
        details=details,
        execution_time_ms=elapsed_ms,
    )

    logger.info(f"[{request.task_id}] Round {request.round}: "
                 f"测试完成 {passed}/{total} (pass_rate={pass_rate:.0%})")

    return RunTestsResponse(test_report=report)


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "ok", "service": "mytest"}


@app.get("/.well-known/agent-card.json")
async def agent_card():
    """A2A Agent Card 发现端点"""
    return AGENT_CARD.model_dump()


@app.post("/a2a/run_tests")
async def run_tests(
    request: RunTestsRequest,
    authorization: str | None = Header(default=None),
):
    """A2A run_tests 能力端点"""
    verify_auth(authorization)

    try:
        response = call_agent_b(request)
        return response.model_dump()
    except TimeoutError:
        raise HTTPException(status_code=408, detail="Test execution timeout")
    except Exception as e:
        logger.error(f"[{request.task_id}] 测试执行失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


a2a_server = A2AServer(
    agent_card=AGENT_CARD,
    app=app,
)

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=get_config().log_level)
    uvicorn.run(app, host="0.0.0.0", port=8001)
