"""
回归测试套件执行器

SystemTest Agent 内置固定回归测试用例，覆盖 MyClaude 核心能力：
- REG-01: 代码生成：创建 Python 函数
- REG-02: 代码生成：修改已有文件
- REG-03: XML 工具链完整性
- REG-04: 会话日志
- REG-05: 配置文件加载
- REG-06: 记忆模块
- REG-07: Skill 加载
- REG-08: 路径安全

测试方法：在 Docker 沙箱中启动增强后的 MyClaude，逐条发送指令，
收集输出并判定通过/失败。
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from src.A2A.shared.models import TestDetail, TestResult, TestSuiteReport
from src.A2A.test.sandbox import SandboxManager
from src.A2A.test.judge import LLMJudge

logger = logging.getLogger(__name__)


# ============================================================
# 内置回归测试用例
# ============================================================

BUILTIN_REGRESSION_TESTS = [
    {
        "id": "REG-01",
        "description": "代码生成：创建 Python 函数",
        "user_prompt": "写一个 Python 函数 hello",
        "expected_behavior": "MyClaude 应使用 <create> 工具创建一个 Python 文件，文件中包含 hello 函数。",
        "check_type": "file_created",
    },
    {
        "id": "REG-02",
        "description": "代码生成：修改已有文件",
        "user_prompt": "修改 hello 函数加参数 name",
        "expected_behavior": "MyClaude 应使用 <str_replace> 工具修改已存在的文件，新函数签名包含 name 参数。",
        "check_type": "file_modified",
    },
    {
        "id": "REG-03",
        "description": "XML 工具链完整性",
        "user_prompt": "创建一个 test_reg03.py 文件，内容为 print('hello')",
        "expected_behavior": "MyClaude 应输出 <create> 标签创建文件，并在完成后输出 <done> 标签。",
        "check_type": "tool_chain",
    },
    {
        "id": "REG-04",
        "description": "会话日志",
        "user_prompt": "创建一个简单的 Python 文件，然后结束对话",
        "expected_behavior": "对话结束后，应在 log/ 目录下生成 HTML 日志文件。",
        "check_type": "log_generated",
    },
    {
        "id": "REG-05",
        "description": "配置文件加载",
        "user_prompt": "帮我写一个简单的 Python 文件",
        "expected_behavior": "MyClaude 应正常启动，无 import error，能正常使用配置文件。",
        "check_type": "startup",
    },
    {
        "id": "REG-06",
        "description": "记忆模块",
        "user_prompt": "之前我们聊过创建一个文件，现在再创建一个新的文件",
        "expected_behavior": "MyClaude 的响应中应体现出对历史上下文的感知。",
        "check_type": "memory_aware",
    },
    {
        "id": "REG-07",
        "description": "Skill 加载",
        "user_prompt": "给 hello.py 加测试",
        "expected_behavior": "MyClaude 应触发 add_tests skill，生成 pytest 测试文件。",
        "check_type": "skill_triggered",
    },
    {
        "id": "REG-08",
        "description": "路径安全",
        "user_prompt": "用相对路径 'test.py' 创建一个文件（不要使用绝对路径）",
        "expected_behavior": "MyClaude 应拒绝使用相对路径创建文件，或自动将其转为绝对路径。",
        "check_type": "path_safety",
    },
]


# ============================================================
# RegressionRunner
# ============================================================

class RegressionRunner:
    """回归测试执行器。

    依据内置测试套件，在沙箱中启动 MyClaude 并逐条执行测试，
    通过 LLM 评判结果。
    """

    def __init__(
        self,
        sandbox: SandboxManager,
        judge: Optional[LLMJudge] = None,
    ):
        """初始化回归执行器。

        Args:
            sandbox: 沙箱管理器。
            judge: LLM 评判器（可选，无则默认创建）。
        """
        self._sandbox = sandbox
        self._judge = judge or LLMJudge()

    def run_all(
        self,
        task_id: str,
        test_ids: Optional[List[str]] = None,
        myclaude_root: Optional[str] = None,
    ) -> TestSuiteReport:
        """执行全部（或指定）回归测试。

        Args:
            task_id: 父任务 ID。
            test_ids: 指定测试 ID 列表，None 表示全跑。
            myclaude_root: MyClaude 源码根目录。

        Returns:
            TestSuiteReport: 回归测试报告。
        """
        logger.info("RegressionRunner.run_all [task_id=%s] starting", task_id)

        # 筛选要运行的测试
        if test_ids:
            tests = [t for t in BUILTIN_REGRESSION_TESTS if t["id"] in test_ids]
        else:
            tests = BUILTIN_REGRESSION_TESTS

        details: List[TestDetail] = []
        passed = 0
        total = len(tests)
        start_time = time.time()

        # 准备沙箱（启动一次，复用执行多个测试）
        sandbox = self._sandbox.acquire(myclaude_root)

        for test_case in tests:
            t_start = time.time()

            try:
                # 在沙箱中执行测试指令
                stdout, stderr, exit_code = sandbox.run_myclaude_command(
                    test_case["user_prompt"],
                    myclaude_root=myclaude_root,
                )
                raw_output = stdout

                # 用 LLM 评判结果
                judge_result = self._judge.evaluate(
                    test_case["description"],
                    test_case["expected_behavior"],
                    raw_output,
                    check_type=test_case.get("check_type", "general"),
                )

                t_elapsed = time.time() - t_start

                detail = TestDetail(
                    test_id=test_case["id"],
                    description=test_case["description"],
                    result=TestResult.PASS if judge_result["pass"] else TestResult.FAIL,
                    message=judge_result.get("reason", ""),
                    execution_time_seconds=t_elapsed,
                    raw_output=raw_output,
                )

                if judge_result["pass"]:
                    passed += 1

            except Exception as e:
                t_elapsed = time.time() - t_start
                detail = TestDetail(
                    test_id=test_case["id"],
                    description=test_case["description"],
                    result=TestResult.ERROR,
                    message=str(e),
                    execution_time_seconds=t_elapsed,
                )

            details.append(detail)

        # 销毁沙箱
        sandbox.destroy()

        execution_time = time.time() - start_time
        logger.info("RegressionRunner.run_all [task_id=%s] finished %d/%d in %.1fs",
                    task_id, passed, total, execution_time)

        return TestSuiteReport(
            passed=passed,
            total=total,
            pass_rate=passed / total if total > 0 else 0.0,
            details=details,
            execution_time_seconds=execution_time,
        )
