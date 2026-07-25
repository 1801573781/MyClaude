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


    @staticmethod
    def _format_estimated_time(raw_count: int) -> str:
        """根据 raw 记忆条目数量预估 LLM 处理时间。

        每条 raw 记忆按 query_id 分组后，每组需要一次 LLM 调用，
        每次 LLM 调用平均约 8 秒（含网络延迟和 LLM 推理）。
        由于无法预知分组数量，按每条 8 秒估算（上界）。

        Args:
            raw_count: raw 记忆条目总数

        Returns:
            格式化后的时间字符串，如 "约15秒"、"约2分30秒"、"约1小时5分30秒"
        """
        # 估算：每条 raw 记忆约 8 秒（含 LLM 调用，按上界估算）
        total_seconds = int(raw_count * 8)
        if total_seconds < 60:
            return f"约{total_seconds}秒"
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return f"约{minutes}分{seconds}秒"
        else:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return f"约{hours}小时{minutes}分{seconds}秒"

    @staticmethod
    def _save_extraction_report(result: dict, logs_root: str):
        """将记忆提取的逐条明细保存为 Markdown 报告文件。

        Args:
            result: extract() 返回的统计字典
            logs_root: 日志根目录路径
        """
        from datetime import datetime
        from pathlib import Path

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = Path(logs_root) / f"memory_extraction_report_{timestamp}.md"

        processed = result.get('processed', 0)
        extracted = result.get('extracted', 0)
        marked_processed = result.get('marked_processed', 0)
        filtered = result.get('filtered', 0)
        timed_out = result.get('timed_out', 0)
        llm_none = result.get('llm_none', 0)
        llm_processed = marked_processed - filtered
        llm_extracted = llm_processed - llm_none
        details = result.get('details', [])

        lines = [
            f"# 记忆提取报告",
            f"",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
            f"## 统计摘要",
            f"",
            f"| 指标 | 数值 | 说明 |",
            f"|------|------|------|",
            f"| 处理条目 | {processed} | 原始 raw 记录总数 |",
            f"| 提取记忆 | {extracted} | LLM 从中提炼出的结构化记忆，已写入 Layer 1 |",
            f"| 标记已处理 | {marked_processed} | 原始 raw 记录被标记为 processed，不再参与后续提取 |",
            f"| 其中前置过滤 | {filtered} | 对话过短/无技术关键词，未调用 LLM 直接标记 |",
            f"| 其中LLM判定无价值 | {llm_none} | LLM 返回 NONE，认为无可提取的长期记忆 |",
            f"| 其中LLM成功提取 | {llm_extracted} | LLM 成功提取出结构化记忆后，原始条目标记已处理 |",
            f"| 超时跳过 | {timed_out} | LLM 超时/失败，保留 raw 状态待下次提取 |",
            f"",
            f"## 逐条明细",
            f"",
        ]

        if details:
            for d in details:
                qid = d.get('query_id', 0)
                turn = d.get('turn', 0)
                eid = d.get('id', '')
                user_in = d.get('user_input', '')[:200]
                preview = d.get('content_preview', '')[:300]
                action = d.get('action', '')
                reason = d.get('reason', '')
                lines.append(f"### [Q{qid} T{turn}] `{eid}`")
                lines.append(f"")
                lines.append(f"- **用户输入**: {user_in}")
                lines.append(f"- **内容预览**: {preview}")
                lines.append(f"- **去向**: {action}")
                lines.append(f"- **原因**: {reason}")
                lines.append(f"")
        else:
            lines.append("（无明细数据）")

        lines.append(f"---")
        lines.append(f"")
        lines.append(f"*本报告由 MyClaude 记忆系统自动生成*")

        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return str(report_path)

    @staticmethod
    def _save_compaction_report(result: dict, logs_root: str):
        """将记忆整理的详细报告保存为 Markdown 文件。

        Args:
            result: compact_detailed() 返回的统计字典
            logs_root: 日志根目录路径

        Returns:
            报告文件路径字符串
        """
        from datetime import datetime
        from pathlib import Path

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = Path(logs_root) / f"memory_compaction_report_{timestamp}.md"

        compaction_id = result.get('compaction_id', '')
        trigger = result.get('trigger', 'manual')
        mode = result.get('mode', 'full')
        merged = result.get('merged', 0)
        evicted = result.get('evicted', 0)
        l1_before = result.get('layer1_before', 0)
        l1_after = result.get('layer1_after', 0)
        duration_ms = result.get('duration_ms', 0)
        total_processed = result.get('total_processed', 0)

        lines = [
            f"# 记忆整理报告",
            f"",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
            f"## 统计摘要",
            f"",
            f"| 指标 | 数值 | 说明 |",
            f"|------|------|------|",
            f"| 整理ID | {compaction_id} | 本次整理的唯一标识 |",
            f"| 触发方式 | {trigger} | manual=手动, auto_light=自动轻量 |",
            f"| 整理模式 | {mode} | full=完整(合并+淘汰), light=仅合并 |",
            f"| 合并条目 | {merged} | 同主题合并、重复去重、因果链压缩的条目数 |",
            f"| 淘汰条目 | {evicted} | 分数过低被从 Layer 1 移除的条目数 |",
            f"| 总处理条目 | {total_processed} | 合并 + 淘汰的总数 |",
            f"| Layer 1 整理前行数 | {l1_before} | 整理前的 Layer 1 行数 |",
            f"| Layer 1 整理后行数 | {l1_after} | 整理后的 Layer 1 行数 |",
            f"| 耗时 | {duration_ms} ms | 整理操作总耗时 |",
            f"",
            f"## 说明",
            f"",
            f"- **合并（Merge）**: 对 Layer 1 中的条目执行规则化合并（同标签合并、重复去重）和 LLM 辅助合并（语义相似合并、因果链压缩）。",
            f"- **淘汰（Evict）**: 当 Layer 1 超过水位线时，按重要性评分从低到高淘汰，最多淘汰 20%，进化条目不淘汰。",
            f"- 整理元数据已写入 memory 系统的 metadata，可通过 `/mem show` 查看「整理日志」计数。",
            f"",
            f"---",
            f"",
            f"*本报告由 MyClaude 记忆系统自动生成*",
        ]

        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return str(report_path)

    @staticmethod
    def _save_evolution_report(result: dict, logs_root: str):
        """将记忆进化的详细报告保存为 Markdown 文件。

        Args:
            result: evolve() 返回的统计字典
            logs_root: 日志根目录路径

        Returns:
            报告文件路径字符串
        """
        from datetime import datetime
        from pathlib import Path

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = Path(logs_root) / f"memory_evolution_report_{timestamp}.md"

        evolution_id = result.get('evolution_id', '')
        evo_timestamp = result.get('timestamp', '')
        trigger = result.get('trigger', 'manual')
        types_executed = result.get('types_executed', [])
        duration_ms = result.get('duration_ms', 0)

        # 统计信息在 stats 子字典中
        stats_inner = result.get('stats', {})
        consumed = stats_inner.get('layer0_consumed', 0)
        evo_gen = stats_inner.get('evolutions_generated', 0)
        patterns = stats_inner.get('patterns_found', 0)
        generalizations = stats_inner.get('generalizations_found', 0)
        conflicts = stats_inner.get('conflicts_resolved', 0)
        trends = stats_inner.get('trends_found', 0)

        evolutions = result.get('evolutions', [])

        type_labels = {
            'PATTERN': '模式识别',
            'RESOLVED': '矛盾解决',
            'GENERALIZED': '归纳规则',
            'TREND': '趋势洞察',
        }

        lines = [
            f"# 记忆进化报告",
            f"",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
            f"## 统计摘要",
            f"",
            f"| 指标 | 数值 | 说明 |",
            f"|------|------|------|",
            f"| 进化ID | {evolution_id} | 本次进化的唯一标识 |",
            f"| 进化时间 | {evo_timestamp} | 进化操作的时间戳 |",
            f"| 触发方式 | {trigger} | manual=手动, auto=自动 |",
            f"| 执行的进化类型 | {', '.join(types_executed) if types_executed else '无'} | 本次实际产出结果的进化类型 |",
            f"| 消费记录 | {consumed} 条 | 从 Layer 0 中消费的未进化记录数 |",
            f"| 生成认知 | {evo_gen} 条 | 产出的高层认知条目数 |",
            f"| 模式识别 | {patterns} 个 | 发现的隐含模式/偏好/规则数 |",
            f"| 矛盾解决 | {conflicts} 个 | 发现并解决的矛盾信息数 |",
            f"| 归纳规则 | {generalizations} 条 | 归纳出的可复用通用解法数 |",
            f"| 趋势洞察 | {trends} 个 | 发现的项目演进趋势数 |",
            f"| 耗时 | {duration_ms} ms | 进化操作总耗时 |",
            f"",
            f"## 逐条明细",
            f"",
        ]

        if evolutions:
            for evo in evolutions:
                evo_id = evo.get('id', '')
                evo_type = evo.get('type', '')
                conclusion = evo.get('conclusion', '')
                confidence = evo.get('confidence', 0.0)
                sources = evo.get('sources', [])
                reasoning = evo.get('reasoning', '')
                type_label = type_labels.get(evo_type, evo_type)

                hypothesis_tag = " [假设]" if confidence < 0.6 else ""
                lines.append(f"### {evo_id} [{type_label}]{hypothesis_tag}")
                lines.append(f"")
                lines.append(f"- **结论**: {conclusion}")
                lines.append(f"- **置信度**: {confidence:.2f}")
                if reasoning:
                    lines.append(f"- **推理过程**: {reasoning}")
                if sources:
                    lines.append(f"- **来源条目**: {', '.join(sources)}")
                lines.append(f"")
        else:
            lines.append("（无进化结果）")

        lines.append(f"---")
        lines.append(f"")
        lines.append(f"*本报告由 MyClaude 记忆系统自动生成*")

        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return str(report_path)

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
            summary = f"Token统计: 输入(缓存命中)={token_stats['prompt_cache_hit']:,}, 输入(未命中)={token_stats['prompt_cache_miss']:,}, 输出={token_stats['completion_tokens']:,}, 总计={token_stats['total']:,}"
            self.query_loop.append_cli_result(summary)
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
                        self.query_loop.append_cli_result(f"单元测试用例生成完成。执行命令: {' '.join(cmd_list)}")
                    else:
                        cli_print.print_error(f"脚本执行失败，退出码: {process.returncode}")
                        self.query_loop.append_cli_result(f"单元测试用例生成失败，退出码: {process.returncode}")
                except Exception as e:
                    cli_print.print_error(f"执行失败: {e}")
                    self.query_loop.append_cli_result(f"单元测试用例生成异常: {e}")

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
                        self.query_loop.append_cli_result(f"系统测试用例生成完成。执行命令: {' '.join(cmd_list)}")
                    else:
                        cli_print.print_error(f"脚本执行失败，退出码: {process.returncode}")
                        self.query_loop.append_cli_result(f"系统测试用例生成失败，退出码: {process.returncode}")
                except Exception as e:
                    cli_print.print_error(f"执行失败: {e}")
                    self.query_loop.append_cli_result(f"系统测试用例生成异常: {e}")

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

            if sub_cmd in ("compaction", "com"):
                # 手动触发记忆整理
                memory = self.query_loop._memory
                if not hasattr(memory, "compact_detailed"):
                    cli_print.print_error("当前记忆后端不支持手动整理。")
                    self.query_loop.append_cli_result("记忆整理失败：当前后端不支持。")
                    return True

                cli_print.print_info("开始执行记忆整理...")
                try:
                    result = memory.compact_detailed()
                    if result.get("skipped"):
                        reason = result.get('reason', '未知原因')
                        cli_print.print_info(f"记忆整理已跳过: {reason}")
                        self.query_loop.append_cli_result(f"记忆整理已跳过: {reason}")
                    else:
                        merged = result.get('merged', 0)
                        evicted = result.get('evicted', 0)
                        l1_before = result.get('layer1_before', 0)
                        l1_after = result.get('layer1_after', 0)

                        # 保存详细报告到 log 目录
                        from src.utility.config_loader import global_cfg
                        logs_root = global_cfg.base_path.logs_root
                        report_path = self._save_compaction_report(result, logs_root)

                        cli_print.print_info(
                            f"记忆整理完成:\n"
                            f"  合并: {merged} 条\n"
                            f"  淘汰: {evicted} 条\n"
                            f"  Layer 1 行数: {l1_before} → {l1_after}\n"
                            f"  详细报告已保存到: {report_path}"
                        )
                        self.query_loop.append_cli_result(
                            f"记忆整理完成: 合并 {merged} 条, 淘汰 {evicted} 条, "
                            f"Layer 1 行数: {l1_before} → {l1_after}. 报告: {report_path}"
                        )
                except Exception as e:
                    cli_print.print_error(f"记忆整理执行失败: {e}")
                    self.query_loop.append_cli_result(f"记忆整理执行失败: {e}")
                return True

            elif sub_cmd in ("evolution", "evo"):
                # 手动触发记忆进化
                memory = self.query_loop._memory
                if not hasattr(memory, "evolve"):
                    cli_print.print_error("当前记忆后端不支持手动进化。")
                    self.query_loop.append_cli_result("记忆进化失败：当前后端不支持。")
                    return True

                cli_print.print_info("开始执行记忆进化...")
                try:
                    result = memory.evolve()
                    if result.get("skipped"):
                        reason = result.get('reason', '未知原因')
                        cli_print.print_info(f"记忆进化已跳过: {reason}")
                        self.query_loop.append_cli_result(f"记忆进化已跳过: {reason}")
                    else:
                        # 统计信息在 stats 子字典中
                        stats_inner = result.get('stats', {})
                        consumed = stats_inner.get('layer0_consumed', 0)
                        evo_gen = stats_inner.get('evolutions_generated', 0)
                        patterns = stats_inner.get('patterns_found', 0)
                        gen_rules = stats_inner.get('generalizations_found', 0)
                        conflicts = stats_inner.get('conflicts_resolved', 0)
                        trends = stats_inner.get('trends_found', 0)

                        # 保存详细报告到 log 目录
                        from src.utility.config_loader import global_cfg
                        logs_root = global_cfg.base_path.logs_root
                        report_path = self._save_evolution_report(result, logs_root)

                        cli_print.print_info(
                            f"记忆进化完成:\n"
                            f"  消费记录: {consumed} 条\n"
                            f"  生成认知: {evo_gen} 条\n"
                            f"  模式识别: {patterns} 个\n"
                            f"  矛盾解决: {conflicts} 个\n"
                            f"  归纳规则: {gen_rules} 条\n"
                            f"  趋势洞察: {trends} 个\n"
                            f"  详细报告已保存到: {report_path}"
                        )
                        self.query_loop.append_cli_result(
                            f"记忆进化完成: 消费记录 {consumed} 条, 生成认知 {evo_gen} 条, "
                            f"模式识别 {patterns} 个, 矛盾解决 {conflicts} 个, "
                            f"归纳规则 {gen_rules} 条, 趋势洞察 {trends} 个. 报告: {report_path}"
                        )
                except Exception as e:
                    cli_print.print_error(f"记忆进化执行失败: {e}")
                    self.query_loop.append_cli_result(f"记忆进化执行失败: {e}")
                return True

            elif sub_cmd in ("extract", "ext"):
                # 手动触发记忆提取（从 Layer 0 raw 条目中用 LLM 提取结构化记忆）
                import sys
                import time
                import threading

                memory = self.query_loop._memory
                if not hasattr(memory, "extract"):
                    cli_print.print_error("当前记忆后端不支持手动提取。")
                    return True

                # 检查 raw 条目数量，预估时间
                try:
                    stats = memory.stats()
                    raw_count = stats.get("layer0_raw", 0)
                except Exception:
                    raw_count = 0

                if raw_count == 0:
                    cli_print.print_info("没有需要提取的 raw 记忆。")
                    return True

                est_time = self._format_estimated_time(raw_count)
                cli_print.print_info(
                    f"开始执行记忆提取...\n"
                    f"  待处理 raw 记忆: {raw_count} 条\n"
                    f"  （按 query_id 分组，每组调用一次 LLM）\n"
                    f"  预计耗时: {est_time}"
                )

                # 设置进度回调
                progress_holder = {"completed": 0, "total": 0, "done": False, "result": None, "error": None}

                def _on_progress(completed: int, total: int, action: str):
                    progress_holder["completed"] = completed
                    progress_holder["total"] = total

                if hasattr(memory, "set_extract_progress_callback"):
                    memory.set_extract_progress_callback(_on_progress)

                # Spinner 动画
                is_tty = sys.stdout.isatty()
                spinner_chars = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')
                all_done = threading.Event()
                output_lock = threading.Lock()
                spin_start = time.time()

                def _spin():
                    i = 0
                    last_heartbeat = time.time()
                    while not all_done.is_set():
                        char = spinner_chars[i % len(spinner_chars)]
                        with output_lock:
                            if all_done.is_set():
                                break
                            if is_tty:
                                elapsed = int(time.time() - spin_start)
                                completed = progress_holder["completed"]
                                total = progress_holder["total"]
                                if total > 0:
                                    msg = f"  {char} 正在调用 LLM 提取记忆... {completed}/{total} 组 ({elapsed}s)"
                                else:
                                    msg = f"  {char} 正在调用 LLM 提取记忆... ({elapsed}s)"
                                sys.stdout.write(f"\r{msg.ljust(70)}")
                                sys.stdout.flush()
                            else:
                                now = time.time()
                                if now - last_heartbeat >= 5.0:
                                    elapsed = int(now - spin_start)
                                    completed = progress_holder["completed"]
                                    total = progress_holder["total"]
                                    if total > 0:
                                        print(f"  ... 仍在提取 {completed}/{total} 组 ({elapsed}s)")
                                    else:
                                        print(f"  ... 仍在提取 ({elapsed}s)")
                                    last_heartbeat = now
                        time.sleep(0.15)
                        i += 1

                spinner_thread = threading.Thread(target=_spin, daemon=True)
                spinner_thread.start()

                # 执行提取
                try:
                    result = memory.extract()
                except Exception as e:
                    result = {"error": str(e)}
                finally:
                    all_done.set()
                    spinner_thread.join(timeout=1.0)
                    with output_lock:
                        if is_tty:
                            sys.stdout.write(f"\r{' ' * 70}\r")
                            sys.stdout.flush()

                # 处理结果
                if "error" in result and result.get("error"):
                    cli_print.print_error(f"记忆提取执行失败: {result['error']}")
                    self.query_loop.append_cli_result(f"记忆提取执行失败: {result['error']}")
                    return True

                if result.get("skipped"):
                    reason = result.get('reason', '未知原因')
                    cli_print.print_info(f"记忆提取已跳过: {reason}")
                    self.query_loop.append_cli_result(f"记忆提取已跳过: {reason}")
                    return True

                processed = result.get('processed', 0)
                extracted = result.get('extracted', 0)
                archived = result.get('marked_processed', 0)
                filtered = result.get('filtered', 0)
                timed_out = result.get('timed_out', 0)
                llm_none = result.get('llm_none', 0)
                llm_archived = archived - filtered
                llm_extracted = llm_archived - llm_none

                # 保存逐条明细到报告文件
                from src.utility.config_loader import global_cfg
                logs_root = global_cfg.base_path.logs_root
                report_path = self._save_extraction_report(result, logs_root)

                cli_print.print_info(
                    f"记忆提取完成:\n"
                    f"  处理条目: {processed} 条（原始 raw 记录总数）\n"
                    f"  提取记忆: {extracted} 条（LLM 从中提炼出的结构化记忆，已写入 Layer 1）\n"
                    f"  标记已处理: {archived} 条（原始 raw 记录被标记为 processed，不再参与后续提取）\n"
                    f"    其中前置过滤: {filtered} 条（对话过短/无技术关键词，未调用 LLM 直接标记）\n"
                    f"    其中LLM判定无价值: {llm_none} 条（LLM 返回 NONE，认为无可提取的长期记忆）\n"
                    f"    其中LLM成功提取: {llm_extracted} 条（LLM 成功提取后，原始条目标记已处理）\n"
                    f"  超时跳过: {timed_out} 条（LLM 超时/失败，保留 raw 状态待下次提取）\n"
                    f"  逐条明细报告已保存到: {report_path}"
                )
                self.query_loop.append_cli_result(
                    f"记忆提取完成: 处理 {processed} 条, 提取 {extracted} 条, "
                    f"标记已处理 {archived} 条(前置过滤 {filtered}, LLM无价值 {llm_none}, LLM成功提取 {llm_extracted}), "
                    f"超时跳过 {timed_out} 条。报告: {report_path}"
                )
                return True

            elif sub_cmd == "show":
                # /mem show — 显示记忆系统概览信息
                memory = self.query_loop._memory
                try:
                    stats = memory.stats()
                except Exception as e:
                    cli_print.print_error(f"获取记忆统计信息失败: {e}")
                    return True

                if not stats:
                    cli_print.print_info("当前没有记忆数据。")
                    return True

                backend = stats.get("backend", "unknown")
                layer0_total = stats.get("layer0_total", 0)
                layer0_raw = stats.get("layer0_raw", 0)
                layer0_unprocessed = stats.get("layer0_unprocessed", 0)
                layer0_processed = stats.get("layer0_processed", 0)
                layer1_entries = stats.get("layer1_entries", 0)
                layer1_lines = stats.get("layer1_lines", 0)
                layer1_tokens = stats.get("layer1_tokens", 0)
                unconsumed = stats.get("unconsumed", 0)
                unevolved = stats.get("unevolved", 0)
                compaction_logs = stats.get("compaction_logs", 0)
                evolution_logs = stats.get("evolution_logs", 0)

                lines = [
                    "=" * 50,
                    "  记忆系统概览",
                    "=" * 50,
                    f"  后端类型: {backend}",
                    "",
                    f"  ── Layer 0（原始记忆） ──",
                    f"    总条目: {layer0_total}",
                    f"    未提取 (raw): {layer0_raw}",
                    f"    已提取 (unprocessed): {layer0_unprocessed}",
                    f"    已处理 (processed): {layer0_processed}",
                    "",
                    f"  ── Layer 1（正式记忆） ──",
                    f"    条数: {layer1_entries}",
                    "",
                    f"  ── 待整理与进化 ──",
                    f"    待整理 (unconsumed): {unconsumed}",
                    f"    待进化 (unevolved): {unevolved}",
                    "",
                    f"  ── 历史记录 ──",
                    f"    整理日志: {compaction_logs} 条",
                    f"    进化日志: {evolution_logs} 条",
                    "=" * 50,
                ]
                cli_print.print_info("\n".join(lines))
                self.query_loop.append_cli_result("\n".join(lines))
                return True

            elif sub_cmd in ("remove", "r"):
                # /mem remove | /mem r — 清除所有持久化记忆（Layer 0/1/2 + 元数据）
                stats = self.query_loop.clear_memory()
                if not stats:
                    cli_print.print_info("当前没有记忆。")
                    self.query_loop.append_cli_result("清除记忆：当前没有记忆。")
                elif "total" in stats and len(stats) == 1:
                    cli_print.print_info(f"已清除所有记忆（共 {stats['total']} 条）。")
                    self.query_loop.append_cli_result(f"已清除所有记忆（共 {stats['total']} 条）。")
                else:
                    layer0_total = stats.get("layer0_total", 0)
                    layer0_raw = stats.get("layer0_raw", 0)
                    layer0_unprocessed = stats.get("layer0_unprocessed", 0)
                    layer0_processed = stats.get("layer0_processed", 0)
                    layer1_entries = stats.get("layer1_entries", 0)

                    lines = [
                        "已清除所有记忆，详细统计如下：",
                        f"  原始记忆（Layer 0）: {layer0_total} 条",
                    ]
                    if layer0_raw > 0:
                        lines.append(f"    其中未提取（raw）: {layer0_raw} 条")
                    if layer0_unprocessed > 0:
                        lines.append(f"    其中已提取（unprocessed）: {layer0_unprocessed} 条")
                    if layer0_processed > 0:
                        lines.append(f"    其中已处理（processed）: {layer0_processed} 条")
                    lines.append(f"  正式记忆（Layer 1）: {layer1_entries} 条")
                    cli_print.print_info("\n".join(lines))
                    self.query_loop.append_cli_result("\n".join(lines))
                return True

            else:
                cli_print.print_error(
                    "未知的 /mem 子命令。可用:\n"
                    "  /mem show       — 查看记忆概览\n"
                    "  /mem extract    — 提取 raw 记忆 (简写 /mem ext)\n"
                    "  /mem compaction — 整理记忆 (简写 /mem com)\n"
                    "  /mem evolution  — 进化记忆 (简写 /mem evo)\n"
                    "  /mem remove     — 清除所有记忆 (简写 /mem r)"
                )
                return True

        elif cmd.startswith('/init'):
            # /init — 创建 MyClaude 项目工程树
            from src.cli.tree_visualizer import create_project_tree
            success = create_project_tree()
            if success:
                cli_print.print_info("项目工程树创建完成。")
                self.query_loop.append_cli_result("项目工程树创建完成。")
            else:
                cli_print.print_error("项目工程树创建失败，请检查目录是否存在。")
                self.query_loop.append_cli_result("项目工程树创建失败。")
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
                cli_print.print_error(result[7:].strip())
                self.query_loop.append_cli_result(f"h2m 失败: {result[7:].strip()}")
            else:
                cli_print.print_info(result[1:].strip())
                self.query_loop.append_cli_result(f"h2m 完成: {result[1:].strip()}")
            return True

        elif cmd == '/cs':
            # /cs — 统计项目代码行数
            from src.cli.code_statistics import code_statistics
            from io import StringIO
            import sys as _sys

            # 捕获 code_statistics 的输出用于记录到上下文
            old_stdout = _sys.stdout
            _sys.stdout = captured = StringIO()
            try:
                code_statistics()
            finally:
                _sys.stdout = old_stdout
            output = captured.getvalue().strip()
            if output:
                # 截断过长输出，只保留摘要信息
                self.query_loop.append_cli_result(output[:2000])
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
                    self.query_loop.append_cli_result(f"已保存全部对话到: {saved_path}")
                else:
                    cli_print.print_info(f"已保存最后一次交互到: {saved_path}")
                    self.query_loop.append_cli_result(f"已保存最后一次交互到: {saved_path}")
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


    # 不需要记录到 session 上下文的命令（纯 UI 操作或重置操作）
    _NO_LOG_COMMANDS = {'/quit', '/exit', '/q', '/cls', '/help', '/new session', '/opsx'}

    def _should_log_cli_command(self, command: str) -> bool:
        """判断该 CLI 命令是否需要记录到 session 上下文。

        注册的斜杠命令（如 /opsx:propose）走 QueryLoop，已有完整记录，不需在此处理。
        纯 UI 操作（/cls, /help, /t 等）和重置操作（/new session）不记录。
        """
        cmd = command.lower().strip()
        if cmd in self._NO_LOG_COMMANDS:
            return False
        if cmd.startswith('/t '):
            return False
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
                # 判断是否为注册的斜杠命令（走 QueryLoop 的，已有记录）
                command_info = self.dispatcher.parse_and_lookup(user_input)
                if not command_info and self._should_log_cli_command(user_input):
                    self.query_loop.record_cli_command(user_input)

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
            self.query_loop.append_cli_result(
                f"单元测试完成: 共 {total} 个用例, 成功 {passed}, 失败 {failed + error_count}, "
                f"不确定 {inconclusive}, 通过率 {pass_rate:.1f}%. 报告: {report_path}"
            )

        except Exception as e:
            cli_print.print_error(f"单元测试执行异常: {e}")
            self.query_loop.append_cli_result(f"单元测试执行异常: {e}")
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
            self.query_loop.append_cli_result(f"A2A 单元测试失败: 所有 {total_cases} 个用例均执行异常。")
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
        self.query_loop.append_cli_result(
            f"A2A 系统测试完成: 任务ID {task_id}, 共 {total} 个用例, 成功 {passed}, "
            f"失败 {total - passed}, 通过率 {pass_rate * 100:.1f}%, 状态: {status}. 报告: {report_display}"
        )
        self.query_loop.append_cli_result(
            f"A2A 单元测试完成: 任务ID {task_id}, 共 {total} 个用例, 成功 {passed}, "
            f"失败 {total - passed}, 通过率 {pass_rate * 100:.1f}%, 状态: {status}. 报告: {report_display}"
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
            self.query_loop.append_cli_result(
                f"系统测试完成: 共 {total} 个用例, 成功 {passed}, 失败 {failed + error_count}, "
                f"不确定 {inconclusive}, 通过率 {pass_rate:.1f}%. 报告: {report_path}"
            )

        except Exception as e:
            cli_print.print_error(f"系统测试执行异常: {e}")
            self.query_loop.append_cli_result(f"系统测试执行异常: {e}")
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
            self.query_loop.append_cli_result(f"A2A 系统测试失败: 所有 {total_cases} 个用例均执行异常。")
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
