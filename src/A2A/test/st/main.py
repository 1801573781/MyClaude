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
from ..models import (
    RunRegressionRequest, RunRegressionResponse,
    RunNewFeatureRequest, RunNewFeatureResponse,
    RunUnitTestRequest, RunUnitTestResponse,
    TestRunState, TestStatus,
)
from .regression_runner import RegressionRunner
from .new_feature_runner import NewFeatureRunner
from ..ut.unit_test_runner import UnitTestRunner
from ..sandbox import SandboxManager
from ..judge import LLMJudge

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
        "service": "test",
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


# ------------------------------ 单元测试 --------------------------------

@app.post("/a2a/run_unit_tests", response_model=RunUnitTestResponse)
async def run_unit_tests(req: RunUnitTestRequest):
    """执行单元测试用例（直接调用被测函数，LLM 评判返回值）"""
    t0 = time.perf_counter()
    task_id = req.task_id or f"ut-{int(t0)}"

    logger.info("Starting unit-test run [task_id=%s] cases=%d",
                task_id, len(req.test_cases))

    runner = UnitTestRunner(judge=judge)
    details = runner.execute(test_cases=req.test_cases,
                             myclaude_root=req.myclaude_root)

    passed = sum(1 for d in details if d.status == TestStatus.PASS)
    total = len(details)
    elapsed = round(time.perf_counter() - t0, 2)

    logger.info("Unit tests complete [task_id=%s] %d/%d passed (%.1f%%) in %.1fs",
                task_id, passed, total, passed / total * 100 if total else 0, elapsed)

    # 生成 Excel 报告
    report_path = None
    try:
        report_output_dir = getattr(req, "report_output_dir", None)
        report_path = UnitTestRunner.generate_excel_report(
            details,
            myclaude_root=req.myclaude_root,
            output_dir=report_output_dir,
        )
    except Exception as report_err:
        logger.error("Failed to generate Excel report: %s", report_err)
        report_path = None

    return RunUnitTestResponse(
        task_id=task_id,
        state=TestRunState.COMPLETED,
        passed=passed,
        total=total,
        pass_rate=passed / total if total else 0.0,
        details=details,
        execution_time_seconds=elapsed,
        report_path=str(report_path) if report_path else None,
    )


# ------------------------------ 指标端点 ---------------------------------

@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点（简化版）"""
    return JSONResponse(content={
        "service": "test",
        "uptime_seconds": time.perf_counter(),
        "docker_available": sandbox_mgr.is_available(),
    })


# ========================================================================
# CLI 模式：python -m src.A2A.test.st.main --json D:/.../u9.json
# ========================================================================

if __name__ == "__main__":
    import argparse
    import json
    import sys
    from pathlib import Path

    from src.utility.config_loader import global_cfg

    parser = argparse.ArgumentParser(
        description="SystemTest CLI — 直接加载 JSON 执行单元测试"
    )
    parser.add_argument(
        "--json",
        required=True,
        help="单元测试用例 JSON 文件路径（如 D:/AI/MyClaude/tests/u9.json）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Excel 报告输出目录（默认使用 config 中 logs_root）",
    )
    args = parser.parse_args()

    # ── 配置日志 ──────────────────────────────────────────────────
    logs_root = Path(args.output) if args.output else Path(global_cfg.base_path.logs_root)
    logs_root.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                logs_root / "unit_test_cli.log",
                encoding="utf-8",
            ),
        ],
    )

    # ── 加载测试用例 ──────────────────────────────────────────────
    json_path = Path(args.json)
    if not json_path.exists():
        logger.error("JSON 文件不存在: %s", json_path)
        sys.exit(1)

    with open(json_path, encoding="utf-8") as f:
        test_cases = json.load(f)

    logger.info("从 %s 加载了 %d 条测试用例", json_path, len(test_cases))

    # ── 执行测试 ──────────────────────────────────────────────────
    judge = LLMJudge()
    runner = UnitTestRunner(judge=judge)
    results = runner.execute(
        test_cases=test_cases,
        myclaude_root=global_cfg.base_path.project_root,
    )

    # ── 生成报告 ──────────────────────────────────────────────────
    report_path = UnitTestRunner.generate_excel_report(
        results,
        output_dir=str(logs_root),
    )

    # ── 打印摘要 ──────────────────────────────────────────────────
    passed = sum(1 for r in results if r.status == TestStatus.PASS)
    total = len(results)
    failed = total - passed
    print("\n" + "=" * 60)
    print("  单元测试完成")
    print(f"  通过: {passed}  失败: {failed}  合计: {total}")
    print(f"  通过率: {passed / total * 100:.1f}%" if total else "  无测试用例")
    print(f"  Excel 报告: {report_path}")
    print("=" * 60)
