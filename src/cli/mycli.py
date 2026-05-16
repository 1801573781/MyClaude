from datetime import datetime
from src.cli import cli_print
from src.query.query_loop import QueryLoop


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

            self.query_loop.run(user_input,
                                cli_print.show_status,
                                cli_print.print_info,
                                cli_print.typewriter_then_markdown,
                                cli_print.print_tool_call,
                                cli_print.print_tool_result)

            cli_print.print_blank()
