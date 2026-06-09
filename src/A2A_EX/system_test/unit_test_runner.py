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

from src.A2A_EX.system_test.models import UnitTestResult, TestStatus
from src.A2A_EX.system_test.judge import LLMJudge

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
            logger.info("Running unit-test case [id=%s] %s", case["id"], case["description"])
            result = self._run_one(case, myclaude_root)
            # 注入原始用例数据，供 Excel 报告使用
            result._case = case
            results.append(result)
            logger.info("Case [id=%s] -> %s", case["id"], result.status)

        return results

    # ------------------------------------------------------------------

    def _run_one(self, case, myclaude_root: str | None) -> UnitTestResult:
        t0 = time.perf_counter()

        try:
            # 1. 动态导入被测模块并调用函数
            actual_output = self._invoke_target(
                target_module=case["target_module"],
                target_function=case["target_function"],
                test_input=case.get("test_input", ""),
                reasoning_input=case.get("reasoning_input", ""),
                myclaude_root=myclaude_root,
            )

            # 2. 调用评判 LLM

            verdict_result = self._judge.evaluate(
                expected=case["expected_behavior"],
                actual_output=actual_output,
                context=case["description"],
                check_type="general",
            )

            status = TestStatus.PASS if verdict_result.get("pass") else TestStatus.FAIL
            elapsed = round(time.perf_counter() - t0, 2)

            return UnitTestResult(
                test_id=case["id"],
                description=case["description"],
                status=status,
                actual_output=actual_output[:500],
                reason=verdict_result.get("reason", ""),
                duration_seconds=elapsed,
            )

        except Exception as exc:
            elapsed = round(time.perf_counter() - t0, 2)
            logger.exception("Unit-test case [id=%s] crashed", case["id"])
            return UnitTestResult(
                test_id=case["id"],
                description=case["description"],
                status=TestStatus.ERROR,
                actual_output=traceback.format_exc()[:500],
                reason=str(exc)[:200],
                duration_seconds=elapsed,
            )

    # ------------------------------------------------------------------

    @staticmethod
    def _invoke_target(target_module: str,
                       target_function: str,
                       test_input: str,
                       reasoning_input: str = "",
                       myclaude_root: str | None = None) -> str:
        """动态导入被测模块并调用函数，返回 repr(result) 或异常字符串。

        Args:
            target_module: 如 'src.utility.file_tool'
            target_function: 如 'resolve_path'
            test_input: 测试说明字符串，复杂参数需 Runner 内部构造（见 _build_args）
            reasoning_input: reasoning_content 参数（如 parse_tools 的第二个参数）
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

        # 构造参数（根据 test_input / reasoning_input 解析）
        args, kwargs = UnitTestRunner._build_args(
            test_input, target_function, reasoning_input
        )

        # 调用函数
        result = func(*args, **kwargs)

        # 格式化返回值
        return f"返回值: {repr(result)}"

    # ------------------------------------------------------------------

    @staticmethod
    def _build_args(test_input: str,
                    target_function: str,
                    reasoning_input: str = "") -> tuple[list, dict]:
        """根据 test_input / reasoning_input 描述构造函数参数。

        目前支持常见场景的简单解析，复杂用例可按需扩展。
        """
        # resolve_path: test_input 形如 "绝对路径 'D:/...' 和相对路径 'test.py'"
        if target_function == "resolve_path":
            import re
            paths = re.findall(r"['\"]([^'\"]+)['\"]", test_input)
            if len(paths) >= 2:
                return paths, {}
            return paths, {}

        # parse_tools: test_input 是主 content，reasoning_input 是 reasoning_content
        # 当 reasoning_input 非空时传两个参数，否则传一个参数
        if target_function == "parse_tools":
            if reasoning_input:
                return [test_input, reasoning_input], {}
            return [test_input], {}

        # file_create: test_input 描述创建流程
        if target_function == "file_create":
            import re
            paths = re.findall(r"([A-Za-z]:/\S+\.py)", test_input)
            if len(paths) >= 2:
                return [paths[0], "print('v2')"], {}
            return [test_input], {}

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


if __name__ == "__main__":
    # 配置日志输出到控制台和文件
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                Path(__file__).resolve().parent / "unit_test_runner_test.log",
                encoding="utf-8",
            ),
        ],
    )

    judege = LLMJudge()
    ut = UnitTestRunner(judge=judege)

    ut_test_cases = [
          {
            "id": "UT-TP-009",
            "description": "str_replace 的 new 块以 </old> 闭合时 parse_tools 容错降级",
            "target_module": "src.llm_tool.tool_executor",
            "target_function": "parse_tools",
            "test_input": "<str_replace path='D:/test.py' summary='测试'><old>x=1</old><new>y=2</old></str_replace>",
            "expected_behavior": "parse_tools 应能容错解析此畸形的 str_replace 标签，new 块以 </old> 错误闭合时降级识别，返回 new 内容为 'y=2' 而非 'y=2</old>'。不应抛出异常或丢失 str_replace 工具调用。"
          },
          {
            "id": "UT-TP-011",
            "description": "主响应无工具时从 reasoning_content 兜底提取工具调用",
            "target_module": "src.llm_tool.tool_executor",
            "target_function": "parse_tools",
            "test_input": "根据需求，我将创建一个文件。这个文件包含一个简单的 Python 函数，用于计算斐波那契数列。",
            "reasoning_input": "用户要求写一个斐波那契函数。我需要创建一个 Python 文件来实现这个功能。让我使用 create 工具。\n<create path='D:/AI/MyClaude/code_output/fib.py' summary='斐波那契数列函数'>\ndef fib(n):\n    if n <= 1:\n        return n\n    a, b = 0, 1\n    for _ in range(2, n + 1):\n        a, b = b, a + b\n    return b\n</create>\n任务完成，输出 done。\n<done>已完成</done>",
            "expected_behavior": "parse_tools 在主 content（test_input）中未解析到工具时，应从 reasoning_content 中兜底提取工具。返回的 tools_list 应包含 create 和 done 两个工具调用。remaining_text 为 test_input 的原文（即 '根据需求，我将创建一个文件...'）。不应因主 content 无工具而返回空的 tools_list。"
          },
    ]

    myclaude_root_path = 'd:/ai/myclaude/'

    ut.execute(test_cases=ut_test_cases, myclaude_root=myclaude_root_path)
