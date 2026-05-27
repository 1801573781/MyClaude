"""MyTest A2A 服务 — FastAPI 应用入口

封装测试智能体 B，在 Docker 沙箱中执行代码并返回测试报告。
"""

import time
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from .models import RunTestsRequest, RunTestsResponse, TestReport, TestDetail
from .agent_card import MYTEST_AGENT_CARD
from .sandbox import execute_in_sandbox

logger = logging.getLogger("mytest")
logging.basicConfig(level=logging.INFO, format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "service": "mytest", "message": "%(message)s"}')


app = FastAPI(title="MyTest - 测试执行服务", version="1.0.0")


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "ok", "service": "mytest"}


@app.get("/.well-known/agent-card.json")
async def agent_card():
    """返回 A2A Agent Card"""
    return MYTEST_AGENT_CARD


@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点（简化版）"""
    return {"service": "mytest", "status": "running"}


@app.post("/a2a/run_tests", response_model=RunTestsResponse)
async def run_tests(request: RunTestsRequest):
    """接收代码与需求规格，在沙箱中执行并返回测试报告"""
    start_time = time.time()
    logger.info(f"收到测试请求 task_id={request.task_id} round={request.round}")

    try:
        # 构建测试用例
        test_details = _build_test_details(request)

        # 在沙箱中执行
        exec_result = execute_in_sandbox(
            code=request.code,
            test_inputs=[
                {"test_id": f"TC-{i+1:02d}", "input": ac.input, "expected": ac.expected_output}
                for i, ac in enumerate(request.spec.acceptance_criteria)
            ],
            timeout_sec=min(request.spec.constraints.max_execution_time_ms // 1000, 30),
        )

        # 合并沙箱执行结果与期望对比
        merged_details = _merge_results(test_details, exec_result)

        passed = sum(1 for d in merged_details if d.status == "PASS")
        total = len(merged_details)
        pass_rate = passed / total if total > 0 else 0.0
        elapsed_ms = int((time.time() - start_time) * 1000)

        report = TestReport(
            passed=passed,
            total=total,
            pass_rate=pass_rate,
            details=merged_details,
            execution_time_ms=elapsed_ms,
            coverage_percent=0.0,  # 后续可接入 coverage.py
        )

        logger.info(f"测试完成 task_id={request.task_id} round={request.round} "
                     f"passed={passed}/{total} pass_rate={pass_rate:.2f} elapsed_ms={elapsed_ms}")

        return RunTestsResponse(test_report=report)

    except TimeoutError:
        logger.error(f"测试执行超时 task_id={request.task_id}")
        raise HTTPException(status_code=408, detail="测试执行超时")
    except Exception as e:
        logger.error(f"测试执行失败 task_id={request.task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _build_test_details(request: RunTestsRequest) -> list:
    """根据验收标准构建初始测试结果列表"""
    details = []
    for i, ac in enumerate(request.spec.acceptance_criteria):
        details.append(TestDetail(
            test_id=f"TC-{i+1:02d}",
            status="PENDING",
            description=f"{ac.input} 应返回 {ac.expected_output}",
            expected=ac.expected_output,
            actual="N/A",
        ))
    return details


def _merge_results(details: list, exec_result: dict) -> list:
    """将沙箱执行结果与期望值合并，生成最终测试结果"""
    results = exec_result.get("results", [])
    result_map = {r.get("test_id", ""): r for r in results}

    merged = []
    for d in details:
        tid = d.test_id
        actual_result = result_map.get(tid)

        if actual_result is None:
            d.status = "ERROR"
            d.error_message = f"测试用例 {tid} 未执行"
        elif actual_result.get("error"):
            d.status = "ERROR"
            d.error_message = actual_result["error"]
        else:
            actual_val = str(actual_result.get("output", "")).strip()
            expected_val = d.expected.strip()
            d.actual = actual_val
            if actual_val == expected_val:
                d.status = "PASS"
            else:
                d.status = "FAIL"
                d.error_message = f"期望={expected_val}，实际={actual_val}"
        merged.append(d)

    return merged


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理"""
    logger.error(f"未处理的异常: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "InternalError", "message": str(exc)},
    )