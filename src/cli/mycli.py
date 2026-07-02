from datetime import datetime
from pathlib import Path
from src.cli import cli_print
from src.query.query_loop import QueryLoop
from src.cli.cli_print import save_buffer_to_file, reset_reasoning


class MyClaudeCLI:
    """MyClaude Code 风格的 CLI 界面"""


    def __init__(self, role: str = "mycode"):
        self.query_loop = QueryLoop(role=role)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")


    def handle_command(self, command: str) -> bool:
        """处理命令，返回是否应该继续对话"""
        cmd = command.lower().strip()

        if cmd in ['/quit', '/exit', '/q']:
            cli_print.print_info("Goodbye! Thanks for using MyClaude CLI.")
            return False

        elif cmd == '/cls':
            cli_print.clear_screen()
            cli_print.print_header(self.session_id)
            cli_print.print_info("Clear Screen")
            return True

        elif cmd == '/help':
            cli_print.print_welcome()
            return True

        elif cmd == '/tokens':
            token_stats = self.query_loop.get_tokens()
            cli_print.show_token_count(token_stats)
            return True

        elif cmd.startswith('/test'):
            # /test --ut-c | --ut-e | --ut-a2a | --st-c | --st-e | --st-a2a | --help
            # 统一入口：单元测试与系统测试命令
            import shlex

            parts = command.strip().split(maxsplit=2)
            sub_flag = parts[1].lower() if len(parts) > 1 else ""
            remaining = parts[2] if len(parts) > 2 else ""

            if sub_flag in ("--help", "-h"):
                cli_print.print_info(
                    "用法: /test <子命令> [参数]\n"
                    "\n"
                    "单元测试命令:\n"
                    "  /test --ut-c [--root <路径>] [--output <路径>]\n"
                    "      生成单元测试用例\n"
                    "      --root    Python 项目根目录（绝对路径），默认从 config.yaml 读取\n"
                    "      --output  输出测试用例 JSON 文件路径（绝对路径）\n"
                    "\n"
                    "  /test --ut-e <测试用例JSON> <日志文件> [报告目录]\n"
                    "      执行单元测试用例\n"
                    "      <测试用例JSON>  测试用例 JSON 文件全路径\n"
                    "      <日志文件>      日志文件全路径\n"
                    "      [报告目录]      报告输出目录（可选）\n"
                    "\n"
                    "  /test --ut-a2a <测试用例JSON> [报告目录]\n"
                    "      通过 A2A 协议执行单元测试（MyOrch → SystemTest）\n"
                    "      <测试用例JSON>  测试用例 JSON 文件全路径\n"
                    "      [报告目录]      报告输出目录（可选）\n"
                    "\n"
                    "系统测试命令:\n"
                    "  /test --st-c\n"
                    "      生成系统测试用例（暂时还未实现，敬请谅解）\n"
                    "\n"
                    "  /test --st-e\n"
                    "      执行系统测试用例（暂时还未实现，敬请谅解）\n"
                    "\n"
                    "  /test --st-a2a\n"
                    "      通过 A2A 协议执行系统测试（暂时还未实现，敬请谅解）\n"
                    "\n"
                    "其他:\n"
                    "  /test --help    显示此帮助信息"
                )
                return True

            elif sub_flag == "--ut-c":
                # /test --ut-c [--root <path>] [--output <path>]
                import sys
                import subprocess
                from pathlib import Path
                from src.utility.config_loader import global_cfg

                script_path = Path(global_cfg.base_path.project_root) / "src" / "tools" / "unit_test_generator_ex.py"

                cmd_list = [sys.executable, str(script_path)]
                if remaining:
                    try:
                        parsed = shlex.split(remaining, posix=False)
                        cmd_list.extend(parsed)
                    except ValueError as e:
                        cli_print.print_error(f"参数解析错误: {e}")
                        return True

                cli_print.print_info(f"执行: {' '.join(cmd_list)}")
                cli_print.print_info("=" * 60)

                try:
                    process = subprocess.Popen(
                        cmd_list,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        cwd=str(Path(global_cfg.base_path.project_root)),
                        bufsize=1
                    )
                    for line in process.stdout:
                        print(line, end='')
                    process.wait()
                    if process.returncode == 0:
                        cli_print.print_info("\n单元测试用例生成完成。")
                    else:
                        cli_print.print_error(f"\n脚本执行失败，退出码: {process.returncode}")
                except Exception as e:
                    cli_print.print_error(f"执行失败: {e}")

                return True

            elif sub_flag == "--ut-e":
                # /test --ut-e <测试用例JSON路径> <日志文件路径> [<报告输出目录>]
                try:
                    ut_args = shlex.split(remaining, posix=False)
                except ValueError as e:
                    cli_print.print_error(f"参数解析错误: {e}")
                    return True

                if len(ut_args) < 2:
                    cli_print.print_error("缺少必选参数：测试用例JSON路径 和 日志文件路径")
                    cli_print.print_info("用法: /test --ut-e <测试用例JSON全路径> <日志文件全路径> [<报告输出目录>]")
                    cli_print.print_info("示例: /test --ut-e D:/AI/MyClaude/tests/cases.json D:/AI/MyClaude/logs/output.txt D:/AI/MyClaude/logs")
                    return True

                p1 = ut_args[0]
                p2 = ut_args[1]
                p3 = ut_args[2] if len(ut_args) > 2 else None
                self._run_unit_test_exec(p1, p2, p3)
                return True

            elif sub_flag == "--ut-a2a":
                # /test --ut-a2a <测试用例JSON路径> [<报告输出目录>]
                try:
                    ut_args = shlex.split(remaining, posix=False)
                except ValueError as e:
                    cli_print.print_error(f"参数解析错误: {e}")
                    return True

                if len(ut_args) < 1:
                    cli_print.print_error("缺少必选参数：测试用例 JSON 路径")
                    cli_print.print_info("用法: /test --ut-a2a <测试用例JSON全路径> [<报告输出目录>]")
                    cli_print.print_info("示例: /test --ut-a2a D:/AI/MyClaude/tests/unit_test_cases.json D:/AI/MyClaude/logs")
                    return True

                p1 = ut_args[0]
                p2 = ut_args[1] if len(ut_args) > 1 else None
                self._run_unit_test_a2a(p1, p2)
                return True

            elif sub_flag in ("--st-c", "--st-e", "--st-a2a"):
                cli_print.print_info("暂时还未实现，敬请谅解")
                return True

            else:
                cli_print.print_error("未知的子命令。使用 /test --help 查看帮助。")
                return True

        elif cmd.startswith('/t'):
            # /t [number] — 展开指定轮次的思考过程
            parts = command.strip().split()
            if len(parts) > 1:
                turn = int(parts[1])
                cli_print.expand_reasoning(turn)
            else:
                cli_print.print_error("Usage: /t number — 展开指定 Turn 的思考过程")
            return True

        elif cmd == '/r mem':
            # /r mem — 清除所有记忆（短期 + 长期 + 工作记忆）
            total = self.query_loop.clear_memory()
            if total == 0:
                cli_print.print_info("当前没有记忆。")
            else:
                cli_print.print_info(f"已清除所有记忆（共 {total} 条）。")
            return True

        elif cmd.startswith('/init'):
            # /init — 创建 MyClaude 项目工程树
            from src.cli.tree_visualizer import create_project_tree
            success = create_project_tree()
            if success:
                cli_print.print_info("项目工程树创建完成。")
            else:
                cli_print.print_error("项目工程树创建失败，请检查目录是否存在。")
            return True

        elif cmd.startswith('/h2m'):
            # /h2m <p1> <p2> [<p3>] [<p4>] — HTML 转 Markdown
            # 参数值如果包含空格，用双引号或单引号包裹
            import shlex
            try:
                args = shlex.split(command[4:].strip(), posix=False)
            except ValueError as e:
                cli_print.print_error(f"参数解析错误: {e}")
                return True
            if len(args) < 2:
                cli_print.print_error("缺少必选参数 p1 和 p2（源文件和目标文件）")
                cli_print.print_info("用法: /h2m <源HTML文件> <目标MD文件> [<轮次>] [<小节>]")
                cli_print.print_info("示例: /h2m \"MyClaude session.html\" output.md t1 \"用户输入,LLM 应答\"")
                return True
            p1 = args[0]
            p2 = args[1]
            p3 = args[2] if len(args) > 2 else None
            p4 = args[3] if len(args) > 3 else None
            from src.cli.h2m import convert_html_to_markdown
            result = convert_html_to_markdown(p1, p2, p3, p4)
            if result.startswith("[ERROR]"):
                cli_print.print_error(result[7:].strip())  # 去掉 "[ERROR] " 前缀
            else:
                cli_print.print_info(result[1:].strip())  # 去掉 "✅ " 前缀
            return True

        elif cmd == '/cs':
            # /cs — 统计项目代码行数
            from src.cli.code_statistics import code_statistics
            code_statistics()
            return True

        elif cmd.startswith('/save'):
            # /save <filename> [all] — 保存屏幕输出到文件（HTML/Word）
            parts = command.strip().split(maxsplit=2)
            if len(parts) > 1:
                from pathlib import Path
                from src.utility.config_loader import global_cfg
                filename = parts[1].strip()
                save_all = len(parts) > 2 and parts[2].strip().lower() == "all"
                filepath = Path(filename)
                if not filepath.is_absolute():
                    logs_root = global_cfg.base_path.logs_root
                    filepath = Path(logs_root) / filepath.name
                    filepath.parent.mkdir(parents=True, exist_ok=True)
                saved_path = save_buffer_to_file(str(filepath), all=save_all)
                if save_all:
                    cli_print.print_info(f"已保存全部对话到: {saved_path}")
                else:
                    cli_print.print_info(f"已保存最后一次交互到: {saved_path}")
            else:
                cli_print.print_error("Usage: /save <filename> [all]")
            return True

        elif cmd.startswith('/'):
            cli_print.print_unknown_cmd(command)
            return True

        return True


    def run(self):
        """运行 CLI 主循环：聊天流式 + 编码工具双模式（全同步）"""
        # cli_print.clear_screen()
        cli_print.print_welcome()

        while True:
            user_input = cli_print.get_input()
            if not user_input:
                continue

            if user_input.startswith('/'):
                if not self.handle_command(user_input):
                    break
                continue

            # 记录用户消息
            cli_print.print_user_input(user_input)

            # 每次对话前重置推理历史，避免 /t 命令跨会话显示旧的思考内容  # noqa
            cli_print.reset_reasoning()

            self.query_loop.run(user_input,
                                cli_print.show_status,
                                cli_print.print_info,
                                cli_print.typewriter_then_markdown,
                                cli_print.print_tool_call,
                                cli_print.print_tool_result,
                                cli_print.typewriter_then_collapse)

            cli_print.print_blank()


    def run_test_mode(self, prompt: str, test_output_path: str = None):
        """测试模式：直接执行一次 QueryLoop，不进入交互循环。
        
        Args:
            prompt: 用户输入指令
            test_output_path: 可选，结构化 JSON 结果输出文件路径。
                输出 JSON 结构为：
                {
                    "exit_code": int,         # 0=成功，1=异常
                    "tool_calls": [           # 工具调用列表
                        {
                            "tool": str,      # 工具名（create/str_replace/bash/file_view/use_skill/done）
                            "params": dict,   # 工具参数（已脱敏，路径为绝对路径）
                            "result": str     # 工具执行结果（截断 500 字符）
                        }
                    ],
                    "key_outputs": [str, ...],  # LLM 在各轮对话中输出的纯文本片段（非工具调用部分）
                    "is_truncated": bool,       # LLM 输出是否因 max_tokens 被截断
                    "error": str|null           # 异常信息（正常为 null）
                }
        """
        import json
        from pathlib import Path
        from src.utility.config_loader import global_cfg
        from src.cli import cli_print as cp

        cp.print_info(f"[测试模式] 输入: {prompt}")
        cp.reset_reasoning()

        user_original_input = prompt

        # 收集结构化测试结果数据
        test_data = {
            "user_original_input": user_original_input,
            "exit_code": 0,
            "tool_calls": [],
            "key_outputs": [],
            "is_truncated": False,
            "error": None,
        }

        # 包装回调：捕获 LLM 输出文本（typewriter_then_markdown 的参数）到 key_outputs
        original_print_llm_rsp = cp.typewriter_then_markdown

        def capturing_print_llm_rsp(text: str):
            if text and text.strip():
                test_data["key_outputs"].append(text)
            original_print_llm_rsp(text)

        # 包装回调：捕获工具调用信息
        original_print_tool_call = cp.print_tool_call

        def capturing_print_tool_call(tool_name: str, params: dict):
            test_data["tool_calls"].append({
                "tool": tool_name,
                "params": params,
                "result": "",  # 先占位，等 print_tool_result 填充
            })
            original_print_tool_call(tool_name, params)

        # 包装回调：捕获工具执行结果并回填到最近一次工具调用记录中
        original_print_tool_result = cp.print_tool_result

        def capturing_print_tool_result(tool_name: str, result: str):
            # 回填结果到最近的同名工具调用
            for tc in reversed(test_data["tool_calls"]):
                if tc["tool"] == tool_name and tc["result"] == "":
                    tc["result"] = result[:500]  # 截断防止过大
                    break
            original_print_tool_result(tool_name, result)

        try:
            self.query_loop.run(
                prompt,
                cp.show_status,
                cp.print_info,
                capturing_print_llm_rsp,
                capturing_print_tool_call,
                capturing_print_tool_result,
                cp.typewriter_then_collapse
            )
        except Exception as e:
            test_data["exit_code"] = 1
            test_data["error"] = str(e)

        # 写入 JSON 结果文件
        if test_output_path:
            output_path = Path(test_output_path)
            if not output_path.is_absolute():
                logs_root = global_cfg.base_path.logs_root
                output_path = Path(logs_root) / output_path.name
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(test_data, f, ensure_ascii=False, indent=2)
            cp.print_info(f"[测试模式] JSON 结果已输出到: {output_path}")

        cp.print_info("[测试模式] 执行完毕，退出。")


    def _run_unit_test_exec(self,
                            json_path: str,
                            log_path: str,
                            report_output_dir: str | None = None):
        """执行 /ut-e 命令：加载 JSON 测试用例并执行单元测试，打印进度与总结"""
        import json
        import sys
        import time
        from pathlib import Path
        from datetime import datetime

        from src.utility.config_loader import global_cfg
        from src.A2A.test.ut.unit_test_runner import UnitTestRunner
        from src.A2A.test.judge import LLMJudge
        from src.A2A.test.models import TestStatus

        # ── 1. 路径解析 ──
        json_file = Path(json_path)
        if not json_file.is_absolute():
            cli_print.print_error(f"测试用例路径必须是绝对路径: {json_path}")
            return

        log_file = Path(log_path)
        if not log_file.is_absolute():
            # 只输入文件名时使用当前工作目录
            log_file = Path.cwd() / log_file.name

        # 确保日志目录存在
        log_file.parent.mkdir(parents=True, exist_ok=True)

        # 报告输出目录：优先 p3，其次 config logs_root
        if report_output_dir:
            report_dir = Path(report_output_dir)
        else:
            report_dir = Path(global_cfg.base_path.logs_root)
        report_dir.mkdir(parents=True, exist_ok=True)

        cli_print.print_info(
            f"测试用例文件: {json_file}\n"
            f"日志文件: {log_file}\n"
            f"报告目录: {report_dir}"
        )

        # ── 2. 检查 JSON 文件 ──
        if not json_file.exists():
            cli_print.print_error(f"测试用例 JSON 文件不存在: {json_file}")
            return

        # ── 3. 加载测试用例 ──
        try:
            with open(json_file, encoding="utf-8") as f:
                test_cases = json.load(f)
        except Exception as e:
            cli_print.print_error(f"加载测试用例 JSON 失败: {e}")
            return

        total_cases = len(test_cases)
        if total_cases == 0:
            cli_print.print_info("测试用例数为 0，无需执行。")
            return

        # ── 4. 重定向标准输出到日志文件（同时保留控制台打印） ──
        log_fh = open(log_file, "w", encoding="utf-8")

        class TeeWriter:
            """同时写入控制台和日志文件"""
            def __init__(self, console, file):
                self.console = console
                self.file = file

            def write(self, data):
                self.console.write(data)
                self.file.write(data)
                self.file.flush()

            def flush(self):
                self.console.flush()
                self.file.flush()

        original_stdout = sys.stdout
        original_stderr = sys.stderr
        sys.stdout = TeeWriter(original_stdout, log_fh)
        sys.stderr = TeeWriter(original_stderr, log_fh)

        try:
            # ── 5. 打印开始时间 ──
            start_time = datetime.now()
            start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
            cli_print.print_info(
                f"单元测试开始时间: {start_time_str}\n"
                f"共 {total_cases} 个测试用例"
            )

            # ── 6. 执行测试（带进度回调） ──
            judge = LLMJudge()
            runner = UnitTestRunner(judge=judge)

            # 进度行控制变量
            progress_last_line = [""]

            def _on_progress(completed: int, total: int, results: list):
                passed = sum(1 for r in results if r.status == TestStatus.PASS)
                pass_rate = (passed / completed * 100) if completed > 0 else 0.0
                line = (
                    f"  进度: {completed}/{total} 已执行 | "
                    f"通过 {passed}/{completed} ({pass_rate:.1f}%)"
                )
                # 覆盖上一行进度信息（用 \r 回到行首）
                if progress_last_line[0]:
                    sys.stdout.write("\r" + " " * len(progress_last_line[0]) + "\r")
                sys.stdout.write(line)
                sys.stdout.flush()
                progress_last_line[0] = line

            results = runner.execute(
                test_cases=test_cases,
                myclaude_root=global_cfg.base_path.project_root,
                progress_callback=_on_progress,
            )

            # 进度行结束，换行
            sys.stdout.write("\n")
            sys.stdout.flush()

            # ── 7. 打印结束时间 ──
            end_time = datetime.now()
            end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
            elapsed = end_time - start_time
            elapsed_str = f"{elapsed.total_seconds():.1f} 秒"

            cli_print.print_info(f"单元测试结束时间: {end_time_str}")

            # ── 8. 生成 Excel 报告 ──
            report_path = UnitTestRunner.generate_excel_report(
                results, output_dir=str(report_dir)
            )

            # ── 9. 打印测试报告总结 ──
            passed = sum(1 for r in results if r.status == TestStatus.PASS)
            failed = sum(1 for r in results if r.status == TestStatus.FAIL)
            error_count = sum(1 for r in results if r.status == TestStatus.ERROR)
            inconclusive = sum(1 for r in results if r.status == TestStatus.INCONCLUSIVE)
            total = len(results)
            pass_rate = passed / total * 100 if total > 0 else 0.0

            cli_print.print_info(
                "\n" + "=" * 60 + "\n"
                "  单元测试总结\n"
                f"  共执行 {total} 个用例\n"
                f"  开始时间: {start_time_str}\n"
                f"  结束时间: {end_time_str}\n"
                f"  执行耗时: {elapsed_str}\n"
                f"  成功: {passed}  失败: {failed}  错误: {error_count}  不确定: {inconclusive}\n"
                f"  通过率: {pass_rate:.1f}%\n"
                f"  测试用例文件: {json_file}\n"
                f"  测试日志文件: {log_file}\n"
                f"  测试报告文件: {report_path}\n"
                f"  如需获取详细信息，请直接查阅上述文件。\n"
                + "=" * 60
            )

        except Exception as e:
            cli_print.print_error(f"单元测试执行异常: {e}")
        finally:
            # 恢复标准输出
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            log_fh.close()


    def _check_port_open(self, host: str, port: int, timeout: float = 1.0) -> bool:
        """检查指定端口是否在监听"""
        import socket
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except (OSError, ConnectionRefusedError):
            return False


    def _ensure_a2a_services(self) -> bool:
        """检查并启动 A2A 服务（MyOrch + SystemTest）

        在 base_path.project_root 目录下启动服务：
        - MyOrch:      python -m src.A2A.myorch.main  (端口 8200)
        - SystemTest:  python -m uvicorn src.A2A.test.main:app --host 127.0.0.1 --port 8201

        Returns:
            True 如果两个服务都已就绪，False 如果有服务启动失败
        """
        import sys
        import time
        import subprocess
        from pathlib import Path
        from src.utility.config_loader import global_cfg
        from src.A2A.shared.config import a2a_global_cfg

        cfg = a2a_global_cfg
        project_root = str(Path(global_cfg.base_path.project_root))

        services = [
            {
                "name": "MyOrch",
                "host": cfg.myorch.host,
                "port": cfg.myorch.port,
                "cmd": [sys.executable, "-m", "src.A2A.myorch.main"],
            },
            {
                "name": "SystemTest",
                "host": cfg.system_test.host,
                "port": cfg.system_test.port,
                "cmd": [
                    sys.executable, "-m", "uvicorn",
                    "src.A2A.test.st.main:app",
                    "--host", cfg.system_test.host,
                    "--port", str(cfg.system_test.port),
                ],
            },
        ]

        all_ready = True

        for svc in services:
            if self._check_port_open(svc["host"], svc["port"]):
                cli_print.print_info(f"[A2A] {svc['name']} 服务已在运行 (端口 {svc['port']})")
                continue

            cli_print.print_info(f"[A2A] {svc['name']} 服务未启动，正在启动...")
            cli_print.print_detail(f"[A2A] 启动命令: {' '.join(svc['cmd'])}")
            cli_print.print_detail(f"[A2A] 工作目录: {project_root}")

            try:
                # Windows 下在新控制台窗口启动，便于查看服务日志
                creation_flags = 0
                if sys.platform == "win32":
                    creation_flags = subprocess.CREATE_NEW_CONSOLE

                subprocess.Popen(
                    svc["cmd"],
                    cwd=project_root,
                    creationflags=creation_flags,
                )

                # 等待服务就绪（最多 30 秒）
                max_wait = 30
                waited = 0
                ready = False
                while waited < max_wait:
                    time.sleep(1)
                    waited += 1
                    if self._check_port_open(svc["host"], svc["port"]):
                        cli_print.print_detail(f"[A2A] {svc['name']} 服务已就绪 (等待 {waited} 秒)")
                        ready = True
                        break

                if not ready:
                    cli_print.print_error(f"[A2A] {svc['name']} 服务启动超时（{max_wait}秒）")
                    all_ready = False

            except Exception as e:
                cli_print.print_error(f"[A2A] 启动 {svc['name']} 服务失败: {e}")
                all_ready = False

        return all_ready


    def _run_unit_test_a2a(self,
                           json_path: str,
                           report_output_dir: str | None = None):
        """执行 /ut-a2a 命令：通过 A2A 协议（MyOrch → SystemTest）执行单元测试"""
        import json
        from pathlib import Path
        from datetime import datetime

        import httpx
        from src.utility.config_loader import global_cfg
        from src.A2A.shared.config import a2a_global_cfg

        # ── 0. 检查并启动 A2A 服务 ──
        if not self._ensure_a2a_services():
            cli_print.print_error("A2A 服务未就绪，无法执行测试。请手动启动服务后重试。")
            return

        # ── 1. 路径解析 ──
        json_file = Path(json_path)
        if not json_file.is_absolute():
            cli_print.print_error(f"测试用例路径必须是绝对路径: {json_path}")
            return

        # 报告输出目录
        if report_output_dir:
            report_dir = Path(report_output_dir)
        else:
            report_dir = Path(global_cfg.base_path.logs_root)
        report_dir.mkdir(parents=True, exist_ok=True)

        cli_print.print_info(
            f"测试用例文件: {json_file}\n"
            f"报告目录: {report_dir}"
        )

        # ── 2. 检查 JSON 文件 ──
        if not json_file.exists():
            cli_print.print_error(f"测试用例 JSON 文件不存在: {json_file}")
            return

        # ── 3. 加载测试用例 ──
        try:
            with open(json_file, encoding="utf-8") as f:
                test_cases = json.load(f)
        except Exception as e:
            cli_print.print_error(f"加载测试用例 JSON 失败: {e}")
            return

        total_cases = len(test_cases)
        if total_cases == 0:
            cli_print.print_info("测试用例数为 0，无需执行。")
            return

        # ── 4. 构造 MyOrch URL ──
        cfg = a2a_global_cfg
        myorch_url = f"http://{cfg.myorch.host}:{cfg.myorch.port}/a2a/run_unit_tests"

        cli_print.print_info(
            f"通过 A2A 协议提交单元测试任务...\n"
            f"MyOrch Agent: {myorch_url}\n"
            f"共 {total_cases} 个测试用例"
        )

        # ── 5. 发送请求 ──
        start_time = datetime.now()
        start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        cli_print.print_info(f"任务开始时间: {start_time_str}")

        try:
            with httpx.Client(timeout=600) as client:
                resp = client.post(
                    myorch_url,
                    json={
                        "test_cases": test_cases,
                        "myclaude_root": str(global_cfg.base_path.project_root),
                        "report_output_dir": str(report_dir),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            cli_print.print_error(f"A2A 协议调用失败: {e}")
            return

        end_time = datetime.now()
        end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
        elapsed = (end_time - start_time).total_seconds()

        # ── 6. 打印结果 ──
        status = data.get("status", "UNKNOWN")
        passed = data.get("passed", 0)
        total = data.get("total", 0)
        pass_rate = data.get("pass_rate", 0.0)
        task_id = data.get("task_id", "")
        report_path = data.get("report_path", "")

        # 始终显示"测试报告文件"行
        # 注意：不在此处回退查找本地旧报告文件，避免显示历史残留文件
        report_display = report_path if report_path else "（未生成，请检查 SystemTest 服务日志）"

        cli_print.print_info(
            "A2A 单元测试报告\n"
            + "=" * 60 + "\n"
            f"  任务 ID: {task_id}\n"
            f"  共执行 {total} 个用例\n"
            f"  开始时间: {start_time_str}\n"
            f"  结束时间: {end_time_str}\n"
            f"  执行耗时: {elapsed:.1f} 秒\n"
            f"  状态: {status}\n"
            f"  成功: {passed}  失败: {total - passed}\n"
            f"  通过率: {pass_rate * 100:.1f}%\n"
            f"  测试用例文件: {json_file}\n"
            f"  测试报告文件: {report_display}\n"
            + "=" * 60
        )
        cli_print.print_info(f"任务结束时间: {end_time_str}，执行耗时：{elapsed:.1f} 秒")
