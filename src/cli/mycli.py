from datetime import datetime
from src.cli import cli_print
from src.query.query_loop import QueryLoop
from src.cli.cli_print import save_buffer_to_file, reset_reasoning


class MyClaudeCLI:
    """MyClaude Code 风格的 CLI 界面"""


    def __init__(self):
        self.query_loop = QueryLoop()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")


    def handle_command(self, command: str) -> bool:
        """处理命令，返回是否应该继续对话"""
        cmd = command.lower().strip()

        if cmd in ['/quit', '/exit', '/q']:
            cli_print.print_info("Goodbye! Thanks for using MyClaude CLI.")
            return False

        elif cmd == '/clear':
            cli_print.clear_screen()
            cli_print.print_header(self.session_id)
            cli_print.print_info("Conversation cleared!")
            return True

        elif cmd == '/help':
            cli_print.print_welcome()
            return True

        elif cmd == '/tokens':
            # cli_print.show_token_count(self.messages)
            req_tokens, rsp_tokens = self.query_loop.get_tokens()
            cli_print.show_token_count(req_tokens, rsp_tokens)
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
                cli_print.print_error("记忆模块未启用，无法执行此操作。")
            else:
                cli_print.print_info(f"已清除所有记忆（共 {total} 条）。")
            return True

        elif cmd.startswith('/h2m'):
            # /h2m <p1> <p2> [<p3>] [<p4>] — HTML 转 Markdown
            # 参数值如果包含空格，用双引号或单引号包裹
            import shlex
            try:
                args = shlex.split(command[4:].strip())
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
            from src.tools.h2m import convert_html_to_markdown
            result = convert_html_to_markdown(p1, p2, p3, p4)
            if result.startswith("[ERROR]"):
                cli_print.print_error(result[7:].strip())  # 去掉 "[ERROR] " 前缀
            else:
                cli_print.print_info(result[1:].strip())  # 去掉 "✅ " 前缀
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
        cli_print.clear_screen()
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

            # 每次对话前重置推理历史，避免 /t 命令跨会话显示旧的思考内容
            cli_print.reset_reasoning()

            self.query_loop.run(user_input,
                                cli_print.show_status,
                                cli_print.print_info,
                                cli_print.typewriter_then_markdown,
                                cli_print.print_tool_call,
                                cli_print.print_tool_result,
                                cli_print.typewriter_then_collapse)

            cli_print.print_blank()
