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

from src.A2A.test.models import UnitTestResult, TestStatus
from src.A2A.test.judge import LLMJudge

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
            logger.info("\n\n")

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
                check_type=case.get("check_type", "general"),
            )

            status = TestStatus.PASS if verdict_result.get("pass") else TestStatus.FAIL
            elapsed = round(time.perf_counter() - t0, 2)
            reason = verdict_result.get("reason", "") or "（评判 LLM 未返回理由）"

            return UnitTestResult(
                test_id=case["id"],
                description=case["description"],
                status=status,
                actual_output=actual_output[:500],
                reason=reason,
                duration_seconds=elapsed,
            )

        except Exception as exc:
            elapsed = round(time.perf_counter() - t0, 2)
            logger.exception("Unit-test case [id=%s] crashed", case["id"])
            return UnitTestResult(
                test_id=case["id"],
                description=case["description"],
                status=TestStatus.ERROR,
                actual_output=traceback.format_exc(),
                reason=str(exc),
                duration_seconds=elapsed,
            )

    # ------------------------------------------------------------------

    @staticmethod
    def generate_excel_report(results: list[UnitTestResult],
                              myclaude_root: str | None = None,
                              output_dir: str | None = None) -> Path:
        """根据测试结果生成 Excel 报告，输出到 logs_root 目录。

        Args:
            results: UnitTestResult 列表（每个元素需携带 _case 原始用例数据）
            myclaude_root: MyClaude 源码根目录（用于定位 config，output_dir 提供时优先使用）
            output_dir: 输出目录（优先使用；未提供时从 config 读取 logs_root）

        Returns:
            生成的 .xlsx 文件路径
        """
        import sys
        from datetime import datetime
        from pathlib import Path

        if output_dir:
            logs_root = Path(output_dir)
        else:
            root = myclaude_root or str(Path(__file__).resolve().parents[3])
            if root not in sys.path:
                sys.path.insert(0, root)
            from utility.config_loader import global_cfg
            logs_root = Path(global_cfg.base_path.logs_root)

        logs_root.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"MyClaude_Unit_Test_Report_{timestamp}.xlsx"
        filepath = logs_root / filename

        try:
            import openpyxl
            from openpyxl.styles import Alignment, Border, Side, PatternFill
        except ImportError:
            logger.error("openpyxl not installed, cannot generate Excel report")
            return filepath

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Unit Test Report"

        # 设置默认行高为 15，使内容更易阅读
        ws.sheet_format.defaultRowHeight = 15

        # 表头：用例原始列 + 测试结果列
        headers = [
            "id", "description", "target_module", "target_function",
            "test_input", "expected_behavior", "reasoning_input",
            "status", "actual_output", "reason", "duration_seconds",
        ]
        ws.append(headers)

        for result in results:
            case = getattr(result, "_case", {})
            row = [
                case.get("id", result.test_id),
                case.get("description", result.description),
                case.get("target_module", ""),
                case.get("target_function", ""),
                case.get("test_input", ""),
                case.get("expected_behavior", ""),
                case.get("reasoning_input", ""),
                result.status.value if hasattr(result.status, "value") else str(result.status),
                result.actual_output,
                result.reason,
                result.duration_seconds,
            ]
            ws.append(row)

        # --- 样式定义 ---
        yahei_font = openpyxl.styles.Font(name="微软雅黑", size=11)
        header_font = openpyxl.styles.Font(name="微软雅黑", size=11, bold=True)
        header_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")  # 浅灰
        center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin"),
        )

        # --- 应用全局样式：所有单元格上下居中 + 自动换行 + 微软雅黑 + 四面框线 ---
        for row_cells in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
            for cell in row_cells:
                cell.font = yahei_font
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                cell.border = thin_border

        # --- 首行样式：居中、加粗、浅灰底色 ---
        for cell in ws[1]:
            cell.font = header_font
            cell.alignment = center_align
            cell.fill = header_fill

        # --- A列(1)、H列(8)、K列(11) 左右居中 ---
        center_columns = [1, 8, 11]
        for col_idx in center_columns:
            col_letter = openpyxl.utils.get_column_letter(col_idx)
            for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=col_idx, max_col=col_idx):
                for cell in row:
                    cell.alignment = center_align

        # --- 冻结首行 ---
        ws.freeze_panes = "A2"

        # --- 自动调整列宽 ---
        for col_cells in ws.columns:
            max_length = 0
            col_letter = col_cells[0].column_letter
            for cell in col_cells:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = min(max_length + 4, 50)

        wb.save(filepath)
        logger.info("Excel report saved to %s", filepath)
        return filepath

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

        # execute_code_tool: test_input 描述工具调用，需构造 tool dict
        if target_function == "execute_code_tool":
            import re
            # 从 test_input 解析工具名和参数
            tool_match = re.search(r'(file_view|create|str_replace|bash|done|use_skill)', test_input)
            tool_name = tool_match.group(1) if tool_match else "file_view"

            tool_params = {}

            # 提取 path 参数
            path_match = re.search(r"path=['\"]([^'\"]+)['\"]", test_input)
            if path_match:
                tool_params["path"] = path_match.group(1)
            # 提取 limit/offset 等可选参数
            limit_match = re.search(r"limit=['\"]?(\d+)", test_input)
            if limit_match:
                tool_params["limit"] = int(limit_match.group(1))
            offset_match = re.search(r"offset=['\"]?(\d+)", test_input)
            if offset_match:
                tool_params["offset"] = int(offset_match.group(1))
            # 提取 summary（create / str_replace 共用）
            summary_match = re.search(r"summary=['\"]([^'\"]*)['\"]", test_input)
            if summary_match:
                tool_params["summary"] = summary_match.group(1)

            # create 工具：提取 body/content
            if tool_name == "create":
                body_match = re.search(
                    r"(?:body|content)=['\"]([^'\"]+)['\"]", test_input
                )
                if body_match:
                    tool_params["content"] = body_match.group(1)
                else:
                    tool_params["content"] = ""

            # str_replace 工具：提取 old 和 new 参数
            if tool_name == "str_replace":
                old_match = re.search(r"old=['\"]([^'\"]*)['\"]", test_input)
                new_match = re.search(r"new=['\"]([^'\"]*)['\"]", test_input)
                if old_match:
                    tool_params["old"] = old_match.group(1)
                if new_match:
                    tool_params["new"] = new_match.group(1)

            # use_skill 工具：提取 name 参数
            if tool_name == "use_skill":
                name_match = re.search(r"name=['\"]([^'\"]+)['\"]", test_input)
                if name_match:
                    tool_params["name"] = name_match.group(1)

            tool_dict = {"llm_tool": tool_name, "params": tool_params}
            return [tool_dict], {}

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
    from src.utility.config_loader import global_cfg

    logs_root = Path(global_cfg.base_path.logs_root)
    logs_root.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(
                logs_root / "unit_test_runner.log",
                encoding="utf-8",
            ),
        ],
    )

    judege = LLMJudge()
    ut = UnitTestRunner(judge=judege)

    ut_test_cases = [
        {
            "id": "UT-FO-001",
            "description": "file_create 在文件不存在时成功创建新文件",
            "target_module": "src.utility.file_tool",
            "target_function": "file_create",
            "test_input": "绝对路径 'D:/AI/MyClaude/code_output/test_fo001.py' 和内容 'x = 1'",
            "expected_behavior": "file_create 应在 D:/AI/MyClaude/code_output/ 目录下创建 test_fo001.py 文件，文件内容为 'x = 1'。返回结果不应包含 [BLOCKED] 或 [ERROR]，应包含成功创建的信息。",
            "check_type": "file_created"
        },
    ]

    myclaude_root_path = global_cfg.base_path.project_root

    results = ut.execute(test_cases=ut_test_cases, myclaude_root=myclaude_root_path)

    # 生成 Excel 报告，输出到 logs_root
    report_path = UnitTestRunner.generate_excel_report(
        results, output_dir=str(logs_root)
    )
    print(f"Excel report saved to: {report_path}")
