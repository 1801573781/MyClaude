"""命令分发器，判断用户输入是否为斜杠命令，并将命令内容注入 LLM 上下文。"""

from src.command import CommandInfo
from src.command.registry import CommandRegistry


class CommandDispatcher:
    """在用户输入时，判断是否为斜杠命令，若是则将命令内容注入 LLM 上下文。"""

    def __init__(self, registry: CommandRegistry):
        self.registry = registry

    def parse_and_lookup(self, user_input: str) -> CommandInfo | None:
        """从用户输入中解析命令名并查找

        支持以下格式：
          /opsx:propose           → 命令名 /opsx:propose，无参数
          /opsx:propose add-auth  → 命令名 /opsx:propose，参数 "add-auth"
          /opsx:explore real-time → 命令名 /opsx:explore，参数 "real-time"
        """
        stripped = user_input.strip()
        if not stripped.startswith("/"):
            return None

        # 尝试匹配最长命令名（先长后短，避免 /opsx:propose 被误匹配为 /opsx:p）
        for cmd_name in sorted(
            self.registry.list_command_names(),
            key=len,
            reverse=True,
        ):
            if stripped == cmd_name or stripped.startswith(cmd_name + " "):
                return self.registry.get(cmd_name)

        return None

    def extract_argument(self, user_input: str, command_info: CommandInfo) -> str:
        """提取命令后的用户参数

        /opsx:propose           → ""
        /opsx:propose add-auth  → "add-auth"
        """
        stripped = user_input.strip()
        cmd_name = command_info.command_name
        if stripped == cmd_name:
            return ""
        return stripped[len(cmd_name):].strip()

    def build_context(
        self,
        command_info: CommandInfo,
        user_argument: str,
    ) -> dict:
        """组装命令上下文，供 QueryLoop 或 LLMApiMsg 使用"""
        return {
            "command_name": command_info.command_name,
            "command_content": command_info.content,
            "user_argument": user_argument,
            "description": command_info.description,
            "category": command_info.category,
        }
