"""命令系统模块，提供斜杠命令的扫描、注册与分发。"""

from dataclasses import dataclass, field


@dataclass
class CommandInfo:
    """斜杠命令信息，由 CommandScanner 扫描 .md 文件后生成。"""

    command_name: str          # 斜杠命令名，如 "/opsx:propose"
    file_path: str             # .md 文件的绝对路径
    description: str = ""      # 从 YAML Front Matter 提取，无则为空
    category: str = "general"  # 从 YAML Front Matter 提取，无则为 "general"
    tags: list[str] = field(default_factory=list)  # 从 YAML Front Matter 提取
    content: str = ""          # .md 文件正文内容（去掉 YAML Front Matter）
