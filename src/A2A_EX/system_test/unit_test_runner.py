"""
单元测试执行器

直接 import 被测模块、调用被测函数、捕获返回值/异常，再调用 LLMJudge 评判结果。
复用现有的 LLMJudge 评判逻辑，不做端到端 CLI 调用。
"""

from __future__ import annotations

import importlib
import logging
import time
import traceback
from pathlib import Path
from typing import Any

from .models import UnitTestResult, TestStatus
from .judge import LLMJudge

logger = logging.getLogger(__name__)


class UnitTestRunner:
    """单元测试用例执行器"""

    def __init__(self, judge: LLMJudge):
        self._judge = judge

    # ------------------------------------------------------------------

    def execute(self,
                test_cases: list,
                myclaude_root: str | None = None) -> list[UnitTestResult]:
        """执行全部单元测试用例，返回结果列表"""
        results: list[UnitTestResult] = []

        for case in test_cases:
            logger.info("Running unit-test case [id=%s] %s", case.id, case.description)
            result = self._run_one(case, myclaude_root)
            results.append(result)
            logger.info("Case [id=%s] -> %s", case.id, result.status)

        return results

    # ------------------------------------------------------------------

    def _run_one(self, case, myclaude_root: str | None) -> UnitTestResult:
        t0 = time.perf_counter()

        try:
            # 1. 动态导入被测模块并调用函数
            actual_output = self._invoke_target(
                target_module=case.target_module,
                target_function=case.target_function,
                test_input=case.test_input,
                myclaude_root=myclaude_root,
            )

            # 2. 调用评判 LLM
            verdict_result = self._judge.evaluate(
                expected=case.expected_behavior,
                actual_output=actual_output,
                context=case.description,
                check_type="general",
            )

            status = TestStatus.PASS if verdict_result.get("pass") else TestStatus.FAIL
            elapsed = round(time.perf_counter() - t0, 2)

            return UnitTestResult(
                test_id=case.id,
                description=case.description,
                status=status,
                actual_output=actual_output[:500],
                duration_seconds=elapsed,
            )

        except Exception as exc:
            elapsed = round(time.perf_counter() - t0, 2)
            logger.exception("Unit-test case [id=%s] crashed", case.id)
            return UnitTestResult(
                test_id=case.id,
                description=case.description,
                status=TestStatus.ERROR,
                actual_output=traceback.format_exc()[:500],
                duration_seconds=elapsed,
            )

    # ------------------------------------------------------------------

    @staticmethod
    def _invoke_target(target_module: str,
                       target_function: str,
                       test_input: str,
                       myclaude_root: str | None) -> str:
        """动态导入被测模块并调用函数，返回 repr(result) 或异常字符串。

        Args:
            target_module: 如 'src.utility.file_tool'
            target_function: 如 'resolve_path'
            test_input: 测试说明字符串，复杂参数需 Runner 内部构造（见 _build_args）
            myclaude_root: MyClaude 源码根目录

        Returns:
            格式化的实际输出字符串（含返回值或异常信息）
        """
        # 确保项目根在 sys.path 中
        import sys
        root = myclaude_root or str(Path(__file__).resolve().parents[3])
        if root not in sys.path:
            sys.path.insert(0, root)

        # 动态导入模块
        mod = importlib.import_module(target_module)
        func = getattr(mod, target_function)

        # 构造参数（根据 test_input 解析）
        args, kwargs = UnitTestRunner._build_args(test_input, target_function)

        # 调用函数
        result = func(*args, **kwargs)

        # 格式化返回值
        return f"返回值: {repr(result)}"

    # ------------------------------------------------------------------

    @staticmethod
    def _build_args(test_input: str, target_function: str) -> tuple[list, dict]:
        """根据 test_input 描述构造函数参数。

        目前支持常见场景的简单解析，复杂用例可按需扩展。
        """
        # resolve_path: test_input 形如 "绝对路径 'D:/...' 和相对路径 'test.py'"
        if target_function == "resolve_path":
            import re
            paths = re.findall(r"['\"]([^'\"]+)['\"]", test_input)
            if len(paths) >= 2:
                return paths, {}
            return paths, {}

        # parse_tools: test_input 是完整的 LLM 输出文本
        if target_function == "parse_tools":
            return [test_input], {}

        # file_create: test_input 描述创建流程
        if target_function == "file_create":
            import re
            paths = re.findall(r"([A-Za-z]:/\S+\.py)", test_input)
            if len(paths) >= 2:
                # 第一次创建用第一个路径和 v1 内容，返回第一次结果；
                # 但单次调用只能测一个，这里先调用第二次（目标路径）看 BLOCKED
                return [paths[0], "print('v2')"], {}

        # append_tool_exec_result: test_input 描述构建场景
        if target_function == "append_tool_exec_result":
            return [], {"api_messages": [
                {"role": "system", "content": "初始系统提示词"},
                {"role": "user", "content": "用户输入"},
            ], "tool_exec_result": {"role": "user", "content": "工具执行结果"}}

        # strip_thinking: test_input 是待处理的文本
        if target_function == "strip_thinking":
            return [test_input], {}

        # 默认：test_input 作为单参数字符串传入
        return [test_input], {}
