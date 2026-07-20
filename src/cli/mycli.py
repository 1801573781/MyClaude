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

        # 初始化斜杠命令系统：扫描 .myclaude/ 目录注册命令
        from src.command.scanner import CommandScanner
        from src.command.dispatcher import CommandDispatcher
        from src.utility.config_loader import global_cfg

        project_root = global_cfg.base_path.project_root
        scanner = CommandScanner(project_root)
        self.registry = scanner.scan()
        self.dispatcher = CommandDispatcher(self.registry)

        # 静默加载斜杠命令，不打印提示信息


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
                    "  /test --ut-e <测试用例JSON> [日志目录] [报告目录]\n"
                    "      执行单元测试用例\n"
                    "      <测试用例JSON>  测试用例 JSON 文件全路径\n"
                    "      [日志目录]      日志文件所在的目录（可选，默认与报告目录相同）\n"
                    "      [报告目录]      报告输出目录（可选）\n"
                    "\n"
                    "  /test --ut-a2a <测试用例JSON> [报告目录]\n"
                    "      通过 A2A 协议执行单元测试（MyOrch → UnitTest）\n"
                    "      <测试用例JSON>  测试用例 JSON 文件全路径\n"
                    "      [报告目录]      报告输出目录（可选）\n"
                    "\n"
                    "系统测试命令:\n"
                    "  /test --st-c [--spec <路径>] [--output <路径>]\n"
                    "      生成系统测试用例\n"
                    "      --spec    系统规格文档路径（绝对路径），默认读取 spec/myclaude_spec.md\n"
                    "      --output  输出测试用例 JSON 文件路径（绝对路径）\n"
                    "\n"
                    "  /test --st-e <测试用例JSON> [日志目录] [报告目录]\n"
                    "      执行系统测试用例\n"
                    "      <测试用例JSON>  测试用例 JSON 文件全路径\n"
                    "      [日志目录]      日志文件所在的目录（可选，默认与报告目录相同）\n"
                    "      [报告目录]      报告输出目录（可选）\n"
                    "\n"
                    "  /test --st-a2a <测试用例JSON> [报告目录]\n"
                    "      通过 A2A 协议执行系统测试（MyOrch → SystemTest）\n"
                    "      <测试用例JSON>  测试用例 JSON 文件全路径\n"
                    "      [报告目录]      报告输出目录（可选）\n"
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

                cli_print.print_info(f"执行: {' '.join(cmd_list)}\n\n" + "=" * 60)

                try:
                    process = subprocess.Popen(
                        cmd_list,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        cwd=str(Path(global_cfg.base_path.project_root)),
                        bufsize=1
                    )
                    for line in process.stdout:
                        print(line, end='', flush=True)
                    process.wait()
                    if process.returncode == 0:
                        cli_print.print_info("单元测试用例生成完成。")
                    else:
                        cli_print.print_error(f"脚本执行失败，退出码: {process.returncode}")
                except Exception as e:
                    cli_print.print_error(f"执行失败: {e}")

                return True

            elif sub_flag == "--ut-e":
                # /test --ut-e <测试用例JSON路径> [<日志目录路径>] [<报告输出目录>]
                try:
                    ut_args = shlex.split(remaining, posix=False)
                except ValueError as e:
                    cli_print.print_error(f"参数解析错误: {e}")
                    return True

                if len(ut_args) < 1:
                    cli_print.print_error("缺少必选参数：测试用例JSON路径")
                    cli_print.print_info("用法: /test --ut-e <测试用例JSON全路径> [<日志目录路径>] [<报告输出目录>]")
                    cli_print.print_info("示例: /test --ut-e D:/AI/MyClaude/tests/cases.json D:/AI/MyClaude/logs D:/AI/MyClaude/logs")
                    return True

                p1 = ut_args[0]
                p2 = ut_args[1] if len(ut_args) > 1 else None
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

            elif sub_flag == "--st-c":
                # /test --st-c [--spec <路径>] [--output <路径>]
                import sys
                import subprocess
                from pathlib import Path
                from src.utility.config_loader import global_cfg

                script_path = Path(global_cfg.base_path.project_root) / "src" / "tools" / "system_test_generator_ex.py"

                cmd_list = [sys.executable, str(script_path)]
                if remaining:
                    try:
                        parsed = shlex.split(remaining, posix=False)
                        cmd_list.extend(parsed)
                    except ValueError as e:
                        cli_print.print_error(f"参数解析错误: {e}")
                        return True

                cli_print.print_info(f"执行: {' '.join(cmd_list)}\n\n" + "=" * 60)

                try:
                    process = subprocess.Popen(
                        cmd_list,
                        cwd=str(Path(global_cfg.base_path.project_root)),
                    )
                    process.wait()
                    if process.returncode == 0:
                        cli_print.print_info("系统测试用例生成完成。")
                    else:
                        cli_print.print_error(f"脚本执行失败，退出码: {process.returncode}")
                except Exception as e:
                    cli_print.print_error(f"执行失败: {e}")

                return True

            elif sub_flag == "--st-e":
                # /test --st-e <测试用例JSON路径> [<日志目录路径>] [<报告输出目录>]
                try:
                    st_args = shlex.split(remaining, posix=False)
                except ValueError as e:
                    cli_print.print_error(f"参数解析错误: {e}")
                    return True

                if len(st_args) < 1:
                    cli_print.print_error("缺少必选参数：测试用例JSON路径")
                    cli_print.print_info("用法: /test --st-e <测试用例JSON全路径> [<日志目录路径>] [<报告输出目录>]")
                    cli_print.print_info("示例: /test --st-e D:/AI/MyClaude/tests/s20.json D:/AI/MyClaude/logs D:/AI/MyClaude/logs")
                    return True

                p1 = st_args[0]
                p2 = st_args[1] if len(st_args) > 1 else None
                p3 = st_args[2] if len(st_args) > 2 else None
                self._run_system_test_exec(p1, p2, p3)
                return True

            elif sub_flag == "--st-a2a":
                # /test --st-a2a <测试用例JSON路径> [<报告输出目录>]
                try:
                    st_args = shlex.split(remaining, posix=False)
                except ValueError as e:
                    cli_print.print_error(f"参数解析错误: {e}")
                    return True

                if len(st_args) < 1:
                    cli_print.print_error("缺少必选参数：测试用例 JSON 路径")
                    cli_print.print_info("用法: /test --st-a2a <测试用例JSON全路径> [<报告输出目录>]")
                    cli_print.print_info("示例: /test --st-a2a D:/AI/MyClaude/tests/s20.json D:/AI/MyClaude/logs")
                    return True

                p1 = st_args[0]
                p2 = st_args[1] if len(st_args) > 1 else None
                self._run_system_test_a2a(p1, p2)
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

        elif cmd == '/new session':
            # /new session — 开启新 Session：重置上下文 + 新 SessionLog
            self.query_loop.new_session()
            cli_print.print_info(
                "已开启新Session（上下文已重置）。"
            )
            return True

        elif cmd.startswith('/mem'):
            # /mem compaction | /mem cpct | /mem evolution | /mem evol
            parts = command.strip().split(maxsplit=1)
            sub_cmd = parts[1].lower().strip() if len(parts) > 1 else ""

            if sub_cmd in ("compaction", "cpct"):
                # 手动触发记忆整理
                memory = self.query_loop._memory
                if not hasattr(memory, "compact_detailed"):
                    cli_print.print_error("当前记忆后端不支持手动整理。")
                    return True

                cli_print.print_info("开始执行记忆整理...")
                try:
                    result = memory.compact_detailed()
                    if result.get("skipped"):
                        cli_print.print_info(f"记忆整理已跳过: {result.get('reason', '未知原因')}")
                    else:
                        cli_print.print_info(
                            f"记忆整理完成:\n"
                            f"  合并: {result.get('merged', 0)} 条\n"
                            f"  降级: {result.get('demoted', 0)} 条\n"
                            f"  淘汰: {result.get('evicted', 0)} 条\n"
                            f"  Layer 1 行数: {result.get('layer1_before', 0)} → {result.get('layer1_after', 0)}"
                        )
                except Exception as e:
                    cli_print.print_error(f"记忆整理执行失败: {e}")
                return True

            elif sub_cmd in ("evolution", "evol"):
                # 手动触发记忆进化
                memory = self.query_loop._memory
                if not hasattr(memory, "evolve"):
                    cli_print.print_error("当前记忆后端不支持手动进化。")
                    return True

                cli_print.print_info("开始执行记忆进化...")
                try:
                    result = memory.evolve()
                    if result.get("skipped"):
                        cli_print.print_info(f"记忆进化已跳过: {result.get('reason', '未知原因')}")
                    else:
                        cli_print.print_info(
                            f"记忆进化完成:\n"
                            f"  消费记录: {result.get('layer0_consumed', 0)} 条\n"
                            f"  生成认知: {result.get('evolutions_generated', 0)} 条\n"
                            f"  模式识别: {result.get('patterns_found', 0)} 个\n"
                            f"  归纳规则: {result.get('generalizations_found', 0)} 条"
                        )
                except Exception as e:
                    cli_print.print_error(f"记忆进化执行失败: {e}")
                return True

            elif sub_cmd == "extract":
                # 手动触发记忆提取（从 Layer 0 raw 条目中用 LLM 提取结构化记忆）
                memory = self.query_loop._memory
                if not hasattr(memory, "extract"):
                    cli_print.print_error("当前记忆后端不支持手动提取。")
                    return True

                cli_print.print_info("开始执行记忆提取（调用 LLM，可能需要数秒）...")
                try:
                    result = memory.extract()
                    if result.get("skipped"):
                        cli_print.print_info(f"记忆提取已跳过: {result.get('reason', '未知原因')}")
                    else:
                        cli_print.print_info(
                            f"记忆提取完成:\n"
                            f"  处理条目: {result.get('processed', 0)} 条\n"
                            f"  提取记忆: {result.get('extracted', 0)} 条\n"
                            f"  归档条目: {result.get('archived', 0)} 条\n"
                            f"  前置过滤: {result.get('filtered', 0)} 条"
                        )
                except Exception as e:
                    cli_print.print_error(f"记忆提取执行失败: {e}")
                return True

            else:
                cli_print.print_error("未知的 /mem 子命令。可用: /mem extract, /mem compaction (或 /mem cpct), /mem evolution (或 /mem evol)")
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

        elif cmd == '/opsx':
            # /opsx — 列出所有已注册的 OpenSpec 斜杠命令
            cli_print.print_command_list(self.registry)
            return True

        elif cmd.startswith('/'):
            # 尝试匹配已注册的斜杠命令（如 /opsx:propose）
            command_info = self.dispatcher.parse_and_lookup(command)
            if command_info:
                # 提取用户参数
                user_arg = self.dispatcher.extract_argument(command, command_info)
                # 打印命令调用提示
                cli_print.print_command_invoked(
                    command_info.command_name, user_arg, command_info.file_path
                )
                # 组装命令上下文
                ctx = self.dispatcher.build_context(command_info, user_arg)
                # 记录用户消息
                cli_print.print_user_input(command)
                # 每次对话前重置推理历史
                cli_print.reset_reasoning()
                # 通过命令上下文启动 QueryLoop
                self.query_loop.run(
                    command,
                    cli_print.show_status,
                    cli_print.print_info,
                    cli_print.typewriter_then_markdown,
                    cli_print.print_tool_call,
                    cli_print.print_tool_result,
                    cli_print.typewriter_then_collapse,
                    on_todo_update=cli_print.print_todo_list,
                    command_context=ctx,
                )
                cli_print.print_blank()
            else:
                cli_print.print_command_unknown(
                    command, self.registry.list_command_names()
                )
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
                                cli_print.typewriter_then_collapse,
                                on_todo_update=cli_print.print_todo_list)

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
        import io
        import json
        from pathlib import Path
        from src.utility.config_loader import global_cfg
        from src.cli import cli_print as cp

        # --- 捕获 ALL Rich Console 输出（TeeFile: 同时写 stdout 和内存缓冲） ---
        output_buffer = io.StringIO()
        original_console_file = cp.console.file

        class _TeeFile:
            """同时写入原始 stdout 和内存缓冲区，用于捕获 Rich Console 全部输出。"""
            def __init__(self, original, buffer):
                self._original = original
                self._buffer = buffer
            def write(self, data):
                self._original.write(data)
                self._buffer.write(data)
            def flush(self):
                self._original.flush()
                self._buffer.flush()
            def isatty(self):
                # 必须返回 True，否则 Rich Live 组件认为不是终端，
                # 会跳过刷新输出，导致 full_output 为空
                return True
            def fileno(self):
                return self._original.fileno()
            @property
            def encoding(self):
                return getattr(self._original, 'encoding', 'utf-8')

        cp.console.file = _TeeFile(original_console_file, output_buffer)

        cp.print_info(f"[测试模式] 输入: {prompt}")
        cp.reset_reasoning()

        user_original_input = prompt

        # 收集结构化测试结果数据
        test_data = {
            "user_original_input": user_original_input,
            "exit_code": 0,
            "tool_calls": [],
            "key_outputs": [],
            "info_messages": [],
            "conversation_history": [],
            "full_output": "",
            "is_truncated": False,
            "error": None,
        }

        # 包装回调：捕获 print_info 消息（含 done 消息、执行进度等关键信息）
        original_print_info = cp.print_info

        def capturing_print_info(msg: str):
            if msg and msg.strip():
                test_data["info_messages"].append(msg)
            original_print_info(msg)

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

        def capturing_print_tool_result(tool_name: str, result: str, params: dict | None = None):
            # 回填结果到最近的同名工具调用
            for tc in reversed(test_data["tool_calls"]):
                if tc["tool"] == tool_name and tc["result"] == "":
                    tc["result"] = result[:500]  # 截断防止过大
                    break
            original_print_tool_result(tool_name, result, params)

        try:
            self.query_loop.run(
                prompt,
                cp.show_status,
                capturing_print_info,
                capturing_print_llm_rsp,
                capturing_print_tool_call,
                capturing_print_tool_result,
                cp.typewriter_then_collapse
            )
        except Exception as e:
            test_data["exit_code"] = 1
            test_data["error"] = str(e)

        # 从 query_loop.api_messages 提取完整对话历史（最可靠的数据源）
        # 不依赖回调捕获机制，直接从引擎内部状态提取
        try:
            if self.query_loop.api_messages:
                for msg in self.query_loop.api_messages.get_msg():
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if content and content.strip():
                        test_data["conversation_history"].append({
                            "role": role,
                            "content": content[:2000],
                        })
        except Exception:
            pass

        # 恢复 console.file，捕获完整输出
        cp.console.file = original_console_file
        test_data["full_output"] = output_buffer.getvalue()

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
                            log_dir_path: str | None = None,
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

        # 报告输出目录：优先 report_output_dir，其次 config logs_root
        if report_output_dir:
            report_dir = Path(report_output_dir)
        else:
            report_dir = Path(global_cfg.base_path.logs_root)
        report_dir.mkdir(parents=True, exist_ok=True)

        # 日志目录：优先 log_dir_path，否则与报告目录相同
        if log_dir_path:
            log_dir = Path(log_dir_path)
            if not log_dir.is_absolute():
                log_dir = Path.cwd() / log_dir.name
        else:
            log_dir = report_dir

        # 确保日志目录存在
        log_dir.mkdir(parents=True, exist_ok=True)

        # 在日志目录下生成带时间戳的日志文件
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = log_dir / f"unit_test_{timestamp}.log"

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
                f"\n共 {total_cases} 个测试用例"
            )

            # ── 6. 执行测试（使用统一进度显示器） ──
            from src.cli.test_progress import TestProgressDisplay

            judge = LLMJudge()
            runner = UnitTestRunner(judge=judge)

            progress = TestProgressDisplay(total=total_cases, test_type="单元测试")

            def _on_progress(completed: int, total: int, results: list):
                passed = sum(1 for r in results if r.status == TestStatus.PASS)
                progress.update(completed=completed, passed=passed)

            progress.start()
            try:
                results = runner.execute(
                    test_cases=test_cases,
                    myclaude_root=global_cfg.base_path.project_root,
                    progress_callback=_on_progress,
                )
            finally:
                progress.stop()
            progress.print_final_progress()

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
                f"  成功: {passed} | 失败: {failed + error_count} | 不确定: {inconclusive}\n"
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


    def _ensure_a2a_services(self, test_type: str = "all") -> bool:
        """检查并启动 A2A 服务

        在 base_path.project_root 目录下启动服务：
        - MyOrch:      python -m src.A2A.myorch.main  (端口 8200)
        - SystemTest:  python -m uvicorn src.A2A.test.st.main:app --host 127.0.0.1 --port 8201
        - UnitTest:    python -m uvicorn src.A2A.test.ut.main:app --host 127.0.0.1 --port 8202

        Args:
            test_type: 测试类型，决定启动哪些服务
                - "ut": 仅启动 MyOrch + UnitTest
                - "st": 仅启动 MyOrch + SystemTest
                - "all": 启动全部三个服务（默认）

        Returns:
            True 如果所需服务都已就绪，False 如果有服务启动失败
        """
        import sys
        import time
        import subprocess
        from pathlib import Path
        from src.utility.config_loader import global_cfg
        from src.A2A.shared.config import a2a_global_cfg

        cfg = a2a_global_cfg
        project_root = str(Path(global_cfg.base_path.project_root))

        all_services = [
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
            {
                "name": "UnitTest",
                "host": cfg.unit_test.host,
                "port": cfg.unit_test.port,
                "cmd": [
                    sys.executable, "-m", "uvicorn",
                    "src.A2A.test.ut.main:app",
                    "--host", cfg.unit_test.host,
                    "--port", str(cfg.unit_test.port),
                ],
            },
        ]

        # 根据 test_type 过滤需要启动的服务
        # MyOrch 是编排器，始终需要；ut 只需 UnitTest，st 只需 SystemTest，all 全部
        if test_type == "ut":
            services = [s for s in all_services if s["name"] in ("MyOrch", "UnitTest")]
        elif test_type == "st":
            services = [s for s in all_services if s["name"] in ("MyOrch", "SystemTest")]
        else:
            services = all_services

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

        # ── 0. 检查并启动 A2A 服务（单元测试只需 MyOrch + UnitTest） ──
        if not self._ensure_a2a_services(test_type="ut"):
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
            f"\n共 {total_cases} 个测试用例"
        )

        # ── 5. 逐条发送请求（使用统一进度显示器） ──
        start_time = datetime.now()
        start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        cli_print.print_info(f"任务开始时间: {start_time_str}")

        from src.cli.test_progress import TestProgressDisplay

        progress = TestProgressDisplay(total=total_cases, test_type="单元测试")
        progress.start()

        all_results = []
        all_errors = []

        try:
            for idx, tc in enumerate(test_cases, 1):
                try:
                    with httpx.Client(timeout=600) as client:
                        resp = client.post(
                            myorch_url,
                            json={
                                "test_cases": [tc],
                                "myclaude_root": str(global_cfg.base_path.project_root),
                                "report_output_dir": str(report_dir),
                            },
                        )
                        resp.raise_for_status()
                        all_results.append(resp.json())
                except Exception as e:
                    all_errors.append(f"用例 {idx}/{total_cases}: {e}")

                # 更新进度
                completed = idx
                passed = sum(r.get("passed", 0) for r in all_results)
                progress.update(completed=completed, passed=passed)
        finally:
            progress.stop()

        progress.print_final_progress()

        if not all_results and all_errors:
            cli_print.print_error(
                f"A2A 协议调用失败，所有 {total_cases} 个用例均执行异常:\n"
                + "\n".join(all_errors)
            )
            return

        end_time = datetime.now()
        end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
        elapsed = (end_time - start_time).total_seconds()

        # ── 6. 汇总结果 ──
        passed = sum(r.get("passed", 0) for r in all_results)
        total = total_cases
        pass_rate = (passed / total) if total > 0 else 0.0
        task_ids = [r.get("task_id", "") for r in all_results if r.get("task_id")]
        task_id = task_ids[0] if task_ids else ""
        report_paths = [r.get("report_path", "") for r in all_results if r.get("report_path")]
        report_path = report_paths[-1] if report_paths else ""

        if all_results:
            status = "PASS" if all(r.get("status") == "PASS" for r in all_results) else "FAIL"
        else:
            status = "ERROR"

        if all_errors:
            status = status + " (部分异常)" if all_results else "ERROR"

        report_display = report_path if report_path else "（未生成，请检查 SystemTest 服务日志）"

        error_detail = ""
        if all_errors:
            error_detail = f"  异常用例: {len(all_errors)} 个\n"

        cli_print.print_info(
            "\n" + "=" * 60 + "\n"
            "  单元测试总结\n"
            f"  任务 ID: {task_id}\n"
            f"  共执行 {total} 个用例\n"
            f"  开始时间: {start_time_str}\n"
            f"  结束时间: {end_time_str}\n"
            f"  执行耗时: {elapsed:.1f} 秒\n"
            f"  状态: {status}\n"
            f"  成功: {passed} | 失败: {total - passed} | 不确定: 0\n"
            f"  通过率: {pass_rate * 100:.1f}%\n"
            + error_detail
            + f"  测试用例文件: {json_file}\n"
            f"  测试报告文件: {report_display}\n"
            f"  如需获取详细信息，请直接查阅上述文件。\n"
            + "=" * 60
        )


    def _run_system_test_exec(self,
                              json_path: str,
                              log_dir_path: str | None = None,
                              report_output_dir: str | None = None):
        """执行 /st-e 命令：加载 JSON 测试用例并执行系统测试，打印进度与总结"""
        import json
        import sys
        import time
        from pathlib import Path
        from datetime import datetime

        from src.utility.config_loader import global_cfg
        from src.A2A.test.st.system_test_runner import SystemTestRunner
        from src.A2A.test.judge import LLMJudge
        from src.A2A.test.sandbox import SandboxManager
        from src.A2A.test.models import TestStatus

        # ── 1. 路径解析 ──
        json_file = Path(json_path)
        if not json_file.is_absolute():
            cli_print.print_error(f"测试用例路径必须是绝对路径: {json_path}")
            return

        # 报告输出目录：优先 report_output_dir，其次 config logs_root
        if report_output_dir:
            report_dir = Path(report_output_dir)
        else:
            report_dir = Path(global_cfg.base_path.logs_root)
        report_dir.mkdir(parents=True, exist_ok=True)

        # 日志目录：优先 log_dir_path，否则与报告目录相同
        if log_dir_path:
            log_dir = Path(log_dir_path)
            if not log_dir.is_absolute():
                log_dir = Path.cwd() / log_dir.name
        else:
            log_dir = report_dir

        # 确保日志目录存在
        log_dir.mkdir(parents=True, exist_ok=True)

        # 在日志目录下生成带时间戳的日志文件
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = log_dir / f"system_test_{timestamp}.log"

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

        # ── 4. 重定向标准输出到日志文件 ──
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
                f"系统测试开始时间: {start_time_str}\n"
                f"\n共 {total_cases} 个测试用例"
            )

            # ── 6. 执行测试（使用统一进度显示器） ──
            from src.cli.test_progress import TestProgressDisplay

            judge = LLMJudge()
            sandbox_mgr = SandboxManager()
            runner = SystemTestRunner(sandbox_mgr=sandbox_mgr, judge=judge)

            progress = TestProgressDisplay(total=total_cases, test_type="系统测试")

            def _on_progress(completed: int, total: int, results: list):
                passed = sum(1 for r in results if r.status == TestStatus.PASS)
                progress.update(completed=completed, passed=passed)

            progress.start()
            try:
                results = runner.execute(
                    test_cases=test_cases,
                    myclaude_root=global_cfg.base_path.project_root,
                    progress_callback=_on_progress,
                )
            finally:
                progress.stop()
            progress.print_final_progress()

            # ── 7. 打印结束时间 ──
            end_time = datetime.now()
            end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
            elapsed = end_time - start_time
            elapsed_str = f"{elapsed.total_seconds():.1f} 秒"

            cli_print.print_info(f"系统测试结束时间: {end_time_str}")

            # ── 8. 生成 Excel 报告 ──
            report_path = SystemTestRunner.generate_excel_report(
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
                "  系统测试总结\n"
                f"  共执行 {total} 个用例\n"
                f"  开始时间: {start_time_str}\n"
                f"  结束时间: {end_time_str}\n"
                f"  执行耗时: {elapsed_str}\n"
                f"  成功: {passed} | 失败: {failed + error_count} | 不确定: {inconclusive}\n"
                f"  通过率: {pass_rate:.1f}%\n"
                f"  测试用例文件: {json_file}\n"
                f"  测试日志文件: {log_file}\n"
                f"  测试报告文件: {report_path}\n"
                f"  如需获取详细信息，请直接查阅上述文件。\n"
                + "=" * 60
            )

        except Exception as e:
            cli_print.print_error(f"系统测试执行异常: {e}")
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            log_fh.close()


    def _run_system_test_a2a(self,
                             json_path: str,
                             report_output_dir: str | None = None):
        """执行 /st-a2a 命令：通过 A2A 协议（MyOrch → SystemTest）执行系统测试"""
        import json
        from pathlib import Path
        from datetime import datetime

        import httpx
        from src.utility.config_loader import global_cfg
        from src.A2A.shared.config import a2a_global_cfg

        # ── 0. 检查并启动 A2A 服务（系统测试只需 MyOrch + SystemTest） ──
        if not self._ensure_a2a_services(test_type="st"):
            cli_print.print_error("A2A 服务未就绪，无法执行测试。请手动启动服务后重试。")
            return

        # ── 1. 路径解析 ──
        json_file = Path(json_path)
        if not json_file.is_absolute():
            cli_print.print_error(f"测试用例路径必须是绝对路径: {json_path}")
            return

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
        myorch_url = f"http://{cfg.myorch.host}:{cfg.myorch.port}/a2a/run_system_tests"

        cli_print.print_info(
            f"通过 A2A 协议提交系统测试任务...\n"
            f"MyOrch Agent: {myorch_url}\n"
            f"\n共 {total_cases} 个测试用例"
        )

        # ── 5. 逐条提交用例（使用统一进度显示器），最后统一生成单份报告 ──
        start_time = datetime.now()
        start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        cli_print.print_info(f"任务开始时间: {start_time_str}")

        from src.cli.test_progress import TestProgressDisplay

        progress = TestProgressDisplay(total=total_cases, test_type="系统测试")
        progress.start()

        case_results = {}  # 用例序号(1-based) -> MyOrch 响应 dict
        case_errors = {}   # 用例序号(1-based) -> 异常信息

        try:
            for idx, tc in enumerate(test_cases, 1):
                try:
                    with httpx.Client(timeout=600) as client:
                        resp = client.post(
                            myorch_url,
                            json={
                                "test_cases": [tc],
                                "myclaude_root": str(global_cfg.base_path.project_root),
                                "report_output_dir": None,
                            },
                        )
                        resp.raise_for_status()
                        case_results[idx] = resp.json()
                except Exception as e:
                    case_errors[idx] = str(e)

                # 更新进度
                completed = idx
                passed = sum(
                    1 for r in case_results.values()
                    if r.get("status") == "PASS"
                )
                progress.update(completed=completed, passed=passed)
        finally:
            progress.stop()

        progress.print_final_progress()

        if not case_results and case_errors:
            cli_print.print_error(
                f"A2A 协议调用失败，所有 {total_cases} 个用例均执行异常:\n"
                + "\n".join(case_errors.values())
            )
            return

        end_time = datetime.now()
        end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
        elapsed = (end_time - start_time).total_seconds()

        # ── 6. 汇总结果并生成单份 Excel 报告 ──
        from src.A2A.test.st.system_test_runner import SystemTestRunner as _STR
        from src.A2A.test.models import TestResult as _TR, TestStatus as _TS

        # 重建 TestResult 对象列表，注入原始用例数据（_case）
        combined_results = []
        for idx, tc in enumerate(test_cases, 1):
            if idx in case_results:
                resp = case_results[idx]
                for detail in resp.get("details", []):
                    try:
                        result_obj = _TR(**detail)
                    except Exception:
                        result_obj = _TR(
                            test_id=tc.get("id", ""),
                            description=tc.get("description", ""),
                            status=_TS.ERROR,
                            actual_output=str(detail)[:3000],
                            judge_reason="结果解析失败",
                        )
                    object.__setattr__(result_obj, "_case", tc)
                    combined_results.append(result_obj)
            elif idx in case_errors:
                result_obj = _TR(
                    test_id=tc.get("id", ""),
                    description=tc.get("description", ""),
                    status=_TS.ERROR,
                    actual_output=case_errors[idx],
                    judge_reason="执行异常",
                )
                object.__setattr__(result_obj, "_case", tc)
                combined_results.append(result_obj)

        # 生成本地合并 Excel 报告
        report_path = None
        try:
            report_path = _STR.generate_excel_report(
                combined_results, output_dir=str(report_dir)
            )
        except Exception as report_err:
            cli_print.print_error(f"生成 Excel 报告失败: {report_err}")

        # 统计
        passed = sum(1 for r in combined_results if r.status == _TS.PASS)
        total = len(combined_results)
        pass_rate = passed / total if total > 0 else 0.0
        task_ids = [r.get("task_id", "") for r in case_results.values() if r.get("task_id")]
        task_id = task_ids[0] if task_ids else ""

        if case_errors and case_results:
            status = "FAIL (部分异常)"
        elif case_errors:
            status = "ERROR"
        else:
            status = "PASS" if all(r.get("status") == "PASS" for r in case_results.values()) else "FAIL"

        report_display = str(report_path) if report_path else "（未生成，请检查日志）"

        error_detail = ""
        if case_errors:
            error_detail = f"  异常用例: {len(case_errors)} 个\n"

        cli_print.print_info(
            "\n" + "=" * 60 + "\n"
            "  系统测试总结\n"
            f"  任务 ID: {task_id}\n"
            f"  共执行 {total} 个用例\n"
            f"  开始时间: {start_time_str}\n"
            f"  结束时间: {end_time_str}\n"
            f"  执行耗时: {elapsed:.1f} 秒\n"
            f"  状态: {status}\n"
            f"  成功: {passed} | 失败: {total - passed} | 不确定: 0\n"
            f"  通过率: {pass_rate * 100:.1f}%\n"
            + error_detail
            + f"  测试用例文件: {json_file}\n"
            f"  测试报告文件: {report_display}\n"
            f"  如需获取详细信息，请直接查阅上述文件。\n"
            + "=" * 60
        )
