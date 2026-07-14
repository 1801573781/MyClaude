"""命令注册表，维护所有已注册命令的映射表。"""

from src.command import CommandInfo


class CommandRegistry:
    """维护所有已注册命令的映射表，提供查询接口。"""

    def __init__(self):
        self._commands: dict[str, CommandInfo] = {}

    def register(self, info: CommandInfo) -> None:
        """注册一个命令"""
        self._commands[info.command_name] = info

    def get(self, command_name: str) -> CommandInfo | None:
        """根据命令名获取命令信息"""
        return self._commands.get(command_name)

    def list_commands(self) -> list[CommandInfo]:
        """列出所有已注册命令"""
        return list(self._commands.values())

    def list_command_names(self) -> list[str]:
        """列出所有已注册命令名"""
        return list(self._commands.keys())

    def is_command(self, user_input: str) -> bool:
        """判断用户输入是否以已注册的斜杠命令开头"""
        for cmd_name in self._commands:
            if user_input.strip().startswith(cmd_name):
                return True
        return False
