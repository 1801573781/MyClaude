# OpenSpec 命令系统集成设计规格

## 1. 概述

### 1.1 目标

将 OpenSpec 的斜杠命令体系集成到 MyClaude 中，使 MyClaude 能够像 Claude Code 一样，通过扫描项目根目录下的 `.myclaude/` 文件夹，自动发现并注册斜杠命令（如 `/opsx:propose`、`/opsx:apply` 等），并将命令对应的 Markdown 文件内容作为上下文注入 LLM 会话。

### 1.2 设计原则

- **约定优于配置**：只要把 `.md` 文件放在 `.myclaude/` 目录下的约定位置，MyClaude 启动时自动扫描并注册为斜杠命令。
- **无侵入式集成**：不修改 MyClaude 核心循环（QueryLoop）的主流程，通过扩展点（命令分发器）接入。
- **文件路径即命令名**：`.myclaude/opsx/propose.md` → `/opsx:propose`，`.myclaude/opsx/apply.md` → `/opsx:apply`。
- **内容即提示词**：命令 `.md` 文件的全部内容作为系统上下文注入 LLM，引导 LLM 按照文件中的指令执行操作。

### 1.3 与 Claude Code 的对应关系

| Claude Code | MyClaude |
|-------------|----------|
| `.claude/commands/` | `.myclaude/commands/` |
| `.claude/commands/opsx/propose.md` | `.myclaude/commands/opsx/propose.md` |
| `/opsx:propose` | `/opsx:propose` |
| 启动时扫描 `.claude/commands/` | 启动时扫描 `.myclaude/commands/` |

---

## 2. 目录结构

### 2.1 .myclaude 目录布局

在 MyClaude 项目根目录下创建 `.myclaude/` 目录，命令文件放在 `.myclaude/commands/` 子目录下：

```
MyClaude/
├── .myclaude/
│   ├── commands/
│   │   └── opsx/
│   │       ├── propose.md      # /opsx:propose - 一步创建变更和所有规划文档
│   │       ├── apply.md        # /opsx:apply - 按照 tasks.md 任务清单编写代码
│   │       ├── explore.md      # /opsx:explore - 自由探讨、梳理需求和方案
│   │       ├── sync.md         # /opsx:sync - 将变更规格增量合并到主规格库
│   │       ├── archive.md      # /opsx:archive - 归档已完成的变更
│   │       └── update.md       # /opsx:update - 修订现有规划文档并保持一致性
│   ├── skills/                 # 技能目录（从外部复制）
│   └── settings.local.json     # 本地设置文件（从外部复制）
├── src/
├── config/
└── ...
```

> **注意**：`.myclaude/commands/opsx/` 下的 `.md` 命令文件、`.myclaude/skills/` 目录及 `.myclaude/settings.local.json` 均由用户从外部复制，MyClaude 不负责自动生成这些文件。CommandScanner 仅负责扫描 `.myclaude/commands/` 目录下的 `.md` 文件并注册为斜杠命令。

### 2.2 命令文件格式

每个 `.md` 文件包含两部分：

1. **YAML Front Matter**（可选）：包含命令元数据（name、description、allowed-tools、category、tags）。
2. **正文内容**：详细的指令文本，作为系统上下文注入 LLM。

示例（propose.md 的开头）：

```markdown
---
name: "OPSX: Propose"
description: Propose a new change - create it and generate all artifacts in one step
allowed-tools: Bash(openspec:*)
category: Workflow
tags: [workflow, artifacts, experimental]
---

Propose a new change - create the change and generate all artifacts in one step.
...
```

---

## 3. 命令映射规则

### 3.1 路径到命令名的转换

| 文件路径 | 斜杠命令 |
|---------|---------|
| `.myclaude/commands/opsx/propose.md` | `/opsx:propose` |
| `.myclaude/commands/opsx/apply.md` | `/opsx:apply` |
| `.myclaude/commands/opsx/explore.md` | `/opsx:explore` |
| `.myclaude/commands/opsx/sync.md` | `/opsx:sync` |
| `.myclaude/commands/opsx/archive.md` | `/opsx:archive` |
| `.myclaude/commands/opsx/update.md` | `/opsx:update` |

**转换规则**：
1. 去掉 `.myclaude/commands/` 前缀。
2. 去掉 `.md` 后缀。
3. 将路径分隔符 `/` 替换为 `:`。
4. 结果前加 `/`。

公式：`.myclaude/commands/{namespace}/{command}.md` → `/{namespace}:{command}`

### 3.2 多级嵌套支持

支持任意层级的嵌套路径：

| 文件路径 | 斜杠命令 |
|---------|---------|
| `.myclaude/commands/opsx/propose.md` | `/opsx:propose` |
| `.myclaude/commands/test/unit/run.md` | `/test:unit:run` |
| `.myclaude/commands/deploy.md` | `/deploy` |

---

## 4. 命令注册与分发机制

### 4.1 命令扫描器（CommandScanner）

**职责**：启动时递归扫描 `.myclaude/` 目录，发现所有 `.md` 文件并注册为命令。

**扫描流程**：

```
1. 确定 .myclaude/commands 目录绝对路径（项目根目录 / ".myclaude/commands"）
2. 递归遍历 .myclaude/commands/ 下所有子目录
3. 对每个 .md 文件：
   a. 计算相对路径（相对于 .myclaude/commands/）
   b. 转换为斜杠命令名
   c. 解析 YAML Front Matter（可选）提取元数据
   d. 读取正文内容
   e. 注册到命令注册表
4. 扫描完成后，输出已注册命令列表
```

**扫描结果数据结构**：

```python
@dataclass
class CommandInfo:
    command_name: str          # 斜杠命令名，如 "/opsx:propose"
    file_path: str             # .md 文件的绝对路径
    description: str           # 从 YAML Front Matter 提取，无则为空
    category: str              # 从 YAML Front Matter 提取，无则为 "general"
    tags: list[str]            # 从 YAML Front Matter 提取，无则为空列表
    content: str               # .md 文件正文内容（去掉 YAML Front Matter）
```

### 4.2 命令注册表（CommandRegistry）

**职责**：维护所有已注册命令的映射表，提供查询接口。

```python
class CommandRegistry:
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

    def is_command(self, user_input: str) -> bool:
        """判断用户输入是否以已注册的斜杠命令开头"""
        for cmd_name in self._commands:
            if user_input.strip().startswith(cmd_name):
                return True
        return False
```

### 4.3 命令分发器（CommandDispatcher）

**职责**：在用户输入时，判断是否为斜杠命令，若是则将命令内容注入 LLM 上下文。

**分发流程**：

```
用户输入
  │
  ▼
是否以 "/" 开头？
  │ 是
  ▼
在 CommandRegistry 中查找匹配的命令
  │ 找到
  ▼
提取命令名后的用户参数（如 "/opsx:propose add-auth" 中的 "add-auth"）
  │
  ▼
组装上下文消息：
  [系统上下文] 命令 .md 文件的正文内容
  [用户消息] 用户参数（如果有）
  │
  ▼
将组装后的消息送入 QueryLoop 执行
  │
  ▼
LLM 按照命令指令执行操作（创建文件、运行命令等）
```

**参数提取规则**：

| 用户输入 | 命令名 | 参数 |
|---------|--------|------|
| `/opsx:propose` | `/opsx:propose` | `""`（空） |
| `/opsx:propose add-user-auth` | `/opsx:propose` | `"add-user-auth"` |
| `/opsx:apply add-auth` | `/opsx:apply` | `"add-auth"` |
| `/opsx:explore real-time collaboration` | `/opsx:explore` | `"real-time collaboration"` |

**上下文注入格式**：

```python
# 组装后的 api_messages 结构
[
    {
        "role": "system",
        "content": "# 命令指令: /opsx:propose\n\n" + command_info.content
    },
    {
        "role": "user",
        "content": user_argument  # 用户在命令后输入的参数
    }
]
```

> **注意**：根据 MyClaude 架构契约，MiniMax 严禁对话中间出现 `role="system"`。因此命令内容应作为第一条 `system` 消息（仅在会话开始时），或以 `[COMMAND_CONTEXT]` 前缀包装为 `role="user"` 消息注入。具体策略见第 6 节。

---

## 5. 命令清单

### 5.1 默认快速工作流（core profile）

| 命令 | 文件 | 用途 |
|------|------|------|
| `/opsx:propose` | `.myclaude/commands/opsx/propose.md` | 一步创建变更和所有规划文档（proposal.md、design.md、tasks.md） |
| `/opsx:explore` | `.myclaude/commands/opsx/explore.md` | 在正式提议前，与 AI 自由探讨、梳理需求和方案 |
| `/opsx:apply` | `.myclaude/commands/opsx/apply.md` | 让 AI 严格按照 tasks.md 中的任务清单来编写代码 |
| `/opsx:sync` | `.myclaude/commands/opsx/sync.md` | 将变更中的规格增量合并到主规格库（openspec/specs/） |
| `/opsx:archive` | `.myclaude/commands/opsx/archive.md` | 功能完成后，归档变更，保持工作区整洁 |
| `/opsx:update` | `.myclaude/commands/opsx/update.md` | 修订现有规划文档并保持一致性，不编辑代码 |

### 5.2 命令依赖关系

```
/opsx:explore  →  /opsx:propose  →  /opsx:apply  →  /opsx:sync  →  /opsx:archive
                        ↑                                    │
                        └────── /opsx:update ────────────────┘
```

- **explore**：可选的前置步骤，梳理需求。
- **propose**：创建变更脚手架和规划文档。
- **apply**：根据 tasks.md 实现代码。
- **update**：在任意阶段修订规划文档。
- **sync**：将规格增量合并到主规格库。
- **archive**：归档已完成的变更。

---

## 6. 与 MyClaude 现有架构的集成

### 6.1 模块划分

新增 `src/command/` 子目录，存放命令系统相关代码：

```
src/
├── command/
│   ├── __init__.py
│   ├── scanner.py            # CommandScanner - 扫描 .myclaude/commands/ 目录
│   ├── registry.py           # CommandRegistry - 命令注册表
│   └── dispatcher.py         # CommandDispatcher - 命令分发与上下文注入
├── cli/
│   └── mycli.py              # 修改：在用户输入处接入命令分发
├── query/
│   └── query_loop.py         # 无需修改（命令内容通过 api_messages 注入）
└── ...
```

### 6.2 启动时扫描

在 `mycli.py` 初始化阶段，调用 CommandScanner 扫描 `.myclaude/` 目录：

```python
# mycli.py 中的伪代码
from src.command.scanner import CommandScanner
from src.command.registry import CommandRegistry

def init_commands(project_root: str) -> CommandRegistry:
    scanner = CommandScanner(project_root, cmd_dir=".myclaude/commands")
    registry = scanner.scan()
    return registry
```

### 6.3 用户输入处理

在 `mycli.py` 的用户输入处理循环中，增加命令分发逻辑：

```python
# mycli.py 中的伪代码
from src.command.dispatcher import CommandDispatcher

dispatcher = CommandDispatcher(registry)

def handle_user_input(user_input: str) -> str | None:
    """处理用户输入，返回 None 表示非命令（正常对话），返回 str 表示已处理的命令上下文"""
    if user_input.strip().startswith("/"):
        command_info = dispatcher.parse_and_lookup(user_input)
        if command_info:
            user_arg = dispatcher.extract_argument(user_input, command_info)
            # 将命令内容注入 api_messages
            return dispatcher.build_context(command_info, user_arg)
    return None
```

### 6.4 上下文注入策略

由于 MyClaude 使用 DeepSeek/GLM API，**严禁在对话中间插入 `role="system"`**。采用以下策略：

**方案 A（推荐）：命令内容作为会话重置**

当用户输入斜杠命令时，视为一次新的任务会话：
1. 清空当前 `api_messages`（或保存到日志后重置）。
2. 将命令 `.md` 内容作为新的 `system` 消息（会话的第一条，合法）。
3. 将用户参数作为第一条 `user` 消息。
4. 启动 QueryLoop 执行。

**方案 B：命令内容作为 user 消息前缀**

不重置会话，将命令内容以 `[COMMAND_CONTEXT]` 前缀包装为 `user` 消息：

```python
api_messages.append({
    "role": "user",
    "content": f"[COMMAND_CONTEXT] /opsx:propose\n\n{command_content}\n\n[USER_INPUT] {user_arg}"
})
```

> 推荐使用方案 A，因为斜杠命令通常代表一个独立任务的开始，重置上下文可以避免历史消息干扰。

### 6.5 与 llm_api_msg.py 的集成

`llm_api_msg.py` 中的 `LLMApiMsg` 类负责组装 `api_messages`。命令注入通过新增方法支持：

```python
class LLMApiMsg:
    def build_command_messages(
        self,
        command_name: str,
        command_content: str,
        user_argument: str,
        project_context: str,
        directory_tree: str
    ) -> list[dict[str, str]]:
        """组装斜杠命令的 api_messages"""
        messages = []
        # system 消息：系统提示词 + 命令指令
        system_prompt = self._load_system_prompt()
        command_prompt = f"# 斜杠命令: {command_name}\n\n{command_content}"
        messages.append({
            "role": "system",
            "content": f"{system_prompt}\n\n{command_prompt}\n\n{project_context}\n\n{directory_tree}"
        })
        # user 消息：用户参数
        if user_argument.strip():
            messages.append({
                "role": "user",
                "content": user_argument
            })
        else:
            messages.append({
                "role": "user",
                "content": "请按照上述命令指令开始执行。"
            })
        return messages
```

### 6.6 与 cli_print.py 的集成

新增 CLI 显示函数，用于命令相关的终端输出：

```python
# cli_print.py 中新增
def print_command_list(registry: CommandRegistry) -> None:
    """打印已注册的命令列表"""
    ...

def print_command_invoked(command_name: str, user_arg: str) -> None:
    """打印命令调用提示"""
    ...
```

---

## 7. CommandScanner 详细设计

### 7.1 扫描算法

```python
class CommandScanner:
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.cmd_dir = self.project_root / ".myclaude" / "commands"

    def scan(self) -> CommandRegistry:
        """扫描 .myclaude/commands/ 目录，返回填充好的 CommandRegistry"""
        registry = CommandRegistry()
        if not self.cmd_dir.exists():
            return registry

        for md_file in self.cmd_dir.rglob("*.md"):
            command_name = self._path_to_command(md_file)
            info = self._parse_file(md_file, command_name)
            registry.register(info)

        return registry

    def _path_to_command(self, file_path: Path) -> str:
        """将文件路径转换为斜杠命令名"""
        relative = file_path.relative_to(self.cmd_dir)
        # 去掉 .md 后缀
        parts = list(relative.parts)
        parts[-1] = parts[-1].removesuffix(".md")
        # 用 : 连接，前面加 /
        return "/" + ":".join(parts)

    def _parse_file(self, file_path: Path, command_name: str) -> CommandInfo:
        """解析 .md 文件，提取元数据和正文"""
        content = file_path.read_text(encoding="utf-8")

        # 解析 YAML Front Matter
        description = ""
        category = "general"
        tags = []

        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                front_matter = content[3:end].strip()
                # 简单解析 YAML（或使用 pyyaml）
                import yaml
                meta = yaml.safe_load(front_matter)
                if meta:
                    description = meta.get("description", "")
                    category = meta.get("category", "general")
                    tags = meta.get("tags", [])
                content = content[end + 3:].strip()

        return CommandInfo(
            command_name=command_name,
            file_path=str(file_path),
            description=description,
            category=category,
            tags=tags,
            content=content
        )
```

### 7.2 错误处理

- `.myclaude/commands/` 目录不存在：返回空注册表，打印提示信息（不报错）。
- `.md` 文件读取失败（编码问题）：跳过该文件，打印警告。
- YAML Front Matter 解析失败：使用默认值，正文为整个文件内容。

---

## 8. CommandDispatcher 详细设计

### 8.1 命令解析

```python
class CommandDispatcher:
    def __init__(self, registry: CommandRegistry):
        self.registry = registry

    def parse_and_lookup(self, user_input: str) -> CommandInfo | None:
        """从用户输入中解析命令名并查找"""
        stripped = user_input.strip()
        if not stripped.startswith("/"):
            return None

        # 尝试匹配最长命令名
        # 先尝试完整匹配（可能有参数），再尝试精确匹配
        for cmd_name in sorted(
            self.registry.list_command_names(),
            key=len,
            reverse=True
        ):
            if stripped == cmd_name or stripped.startswith(cmd_name + " "):
                return self.registry.get(cmd_name)

        return None

    def extract_argument(self, user_input: str, command_info: CommandInfo) -> str:
        """提取命令后的用户参数"""
        stripped = user_input.strip()
        cmd_name = command_info.command_name
        if stripped == cmd_name:
            return ""
        # 去掉命令名前缀，剩余部分即为参数
        return stripped[len(cmd_name):].strip()
```

### 8.2 上下文组装

```python
    def build_context(
        self,
        command_info: CommandInfo,
        user_argument: str
    ) -> dict:
        """组装命令上下文"""
        return {
            "command_name": command_info.command_name,
            "command_content": command_info.content,
            "user_argument": user_argument,
            "description": command_info.description,
            "category": command_info.category,
        }
```

---

## 9. 配置支持

### 9.1 config.yaml 新增配置项

```yaml
command:
  cmd_dir: ".myclaude/commands"     # 命令目录名（相对于项目根目录）
  auto_scan: true                   # 启动时自动扫描
  profile: "core"                   # 工作流模式：core 或 expanded
```

### 9.2 config_loader.py 适配

在 `config_loader.py` 中新增 `command` 配置段的加载逻辑，使用 `SimpleNamespace` 支持点号访问：

```python
# 访问方式
config.command.cmd_dir       # ".myclaude"
config.command.auto_scan     # True
config.command.profile       # "core"
```

---

## 10. 扩展工作流（expanded profile）

### 10.1 扩展命令

当 `profile` 设置为 `expanded` 时，`.myclaude/commands/opsx/` 目录下可包含额外的命令文件：

| 命令 | 文件 | 用途 |
|------|------|------|
| `/opsx:new` | `.myclaude/commands/opsx/new.md` | 仅创建变更脚手架，不生成文档 |
| `/opsx:continue` | `.myclaude/commands/opsx/continue.md` | 逐步生成下一个规划文档 |
| `/opsx:ff` | `.myclaude/commands/opsx/ff.md` | 快速前进，一次性生成所有规划文档 |
| `/opsx:verify` | `.myclaude/commands/opsx/verify.md` | 验证代码与规格文档一致性 |
| `/opsx:bulk-archive` | `.myclaude/commands/opsx/bulk-archive.md` | 批量归档多个已完成变更 |
| `/opsx:onboard` | `.myclaude/commands/opsx/onboard.md` | 引导式教程 |

### 10.2 模式切换

- `core` 模式：仅加载 6 个核心命令（propose、explore、apply、sync、archive、update）。
- `expanded` 模式：加载全部命令（核心 + 扩展）。

切换方式：修改 `config.yaml` 中的 `command.profile` 字段，或在 `.myclaude/commands/` 目录中手动添加/删除 `.md` 文件。

> **简化策略**：实际上由于采用文件扫描机制，只要 `.md` 文件存在于 `.myclaude/commands/` 目录中即自动注册。`profile` 配置仅用于控制哪些扩展命令文件被初始创建。运行时扫描不区分 profile，发现即注册。

---

## 11. cmd 目录文件初始化

### 11.1 初始化流程

`.myclaude/commands/` 目录下的命令文件、`.myclaude/skills/` 目录及 `.myclaude/settings.local.json` 均由用户从外部复制，MyClaude **不负责自动生成**这些文件。CommandScanner 仅负责在启动时扫描 `.myclaude/commands/` 目录并注册发现的 `.md` 文件。

如果 `.myclaude/commands/` 目录不存在，MyClaude 正常启动，仅提示无可用斜杠命令。

### 11.2 命令文件来源

`.myclaude/commands/opsx/` 目录下的 `.md` 文件内容直接来自 OpenSpec 的 `.claude/commands/opsx/` 目录，由用户手动复制，保持原文不变。文件清单：

1. `propose.md` — 一步创建变更和所有规划文档
2. `apply.md` — 按照 tasks.md 任务清单编写代码
3. `explore.md` — 自由探讨、梳理需求和方案
4. `sync.md` — 将变更规格增量合并到主规格库
5. `archive.md` — 归档已完成的变更
6. `update.md` — 修订现有规划文档并保持一致性

---

## 12. 终端交互体验

### 12.1 命令列表展示

用户输入 `/help` 或 `/opsx` 时，展示已注册的命令列表：

```
┌─────────────────────────────────────────────────────────────────┐
│                    OpenSpec 命令列表                             │
├──────────────────┬──────────────────────────────────────────────┤
│ 命令              │ 用途                                         │
├──────────────────┼──────────────────────────────────────────────┤
│ /opsx:propose    │ 一步创建变更和所有规划文档                    │
│ /opsx:explore    │ 自由探讨、梳理需求和方案                      │
│ /opsx:apply      │ 按照 tasks.md 任务清单编写代码               │
│ /opsx:sync       │ 将变更规格增量合并到主规格库                  │
│ /opsx:archive    │ 归档已完成的变更                             │
│ /opsx:update     │ 修订现有规划文档并保持一致性                  │
└──────────────────┴──────────────────────────────────────────────┘
```

### 12.2 命令调用提示

当用户输入斜杠命令时，终端显示命令调用信息：

```
⚡ 命令: /opsx:propose
📝 参数: add-user-auth
📋 指令来源: .myclaude/commands/opsx/propose.md
─────────────────────────────────
[LLM 开始执行命令指令...]
```

### 12.3 未知命令提示

```
⚠ 未知命令: /opsx:unknown
可用命令: /opsx:propose, /opsx:explore, /opsx:apply, /opsx:sync, /opsx:archive, /opsx:update
```

---

## 13. 实现计划

### 13.1 实现步骤

| 步骤 | 内容 | 涉及文件 |
|------|------|---------|
| 1 | 用户从外部复制命令 .md 文件到 `.myclaude/commands/opsx/` 目录 | `.myclaude/commands/opsx/*.md` |
| 2 | 实现 CommandInfo 数据类 | `src/command/__init__.py` |
| 3 | 实现 CommandRegistry | `src/command/registry.py` |
| 4 | 实现 CommandScanner | `src/command/scanner.py` |
| 5 | 实现 CommandDispatcher | `src/command/dispatcher.py` |
| 6 | 修改 mycli.py，接入命令扫描与分发 | `src/cli/mycli.py` |
| 7 | 修改 llm_api_msg.py，新增 build_command_messages 方法 | `src/utility/llm_api_msg.py` |
| 8 | 修改 cli_print.py，新增命令相关显示函数 | `src/cli/cli_print.py` |
| 9 | 修改 config.yaml 和 config_loader.py，新增 command 配置段 | `config/config.yaml`, `src/utility/config_loader.py` |
| 10 | 测试：启动 MyClaude，输入 /opsx:propose 验证命令分发 | — |

### 13.2 依赖关系

```
步骤 1 ─┐
步骤 2 ─┼─→ 步骤 3 ─→ 步骤 4 ─→ 步骤 5 ─┐
                                        ├─→ 步骤 6 ─→ 步骤 10
步骤 7 ─────────────────────────────────┤
步骤 8 ─────────────────────────────────┤
步骤 9 ─────────────────────────────────┘
```

### 13.3 验收标准

1. MyClaude 启动时自动扫描 `.myclaude/commands/` 目录，注册所有 `.md` 文件为斜杠命令。
2. 用户输入 `/opsx:propose add-auth` 时，命令内容被正确注入 LLM 上下文，LLM 开始执行 propose 流程。
3. 用户输入 `/opsx:explore` 时，进入探索模式，LLM 不编写代码，仅进行思考和讨论。
4. 未知命令给出友好提示，不崩溃。
5. `.myclaude/commands/` 目录不存在时，MyClaude 正常启动，仅提示无可用命令。
6. 命令文件更新后，重启 MyClaude 即可生效（无需修改代码）。

---

## 14. 注意事项与约束

### 14.1 架构契约遵守

- **同步流式**：命令系统的扫描、注册、分发均为同步操作，不引入 async/await。
- **消息格式**：命令上下文注入 `api_messages` 时，遵守 `List[Dict[str, str]]` 格式。
- **DeepSeek/GLM 限制**：`role="system"` 仅出现在 `api_messages[0]`，对话中间严禁 system 角色。
- **工具协议**：命令执行期间，LLM 仍使用 XML 工具标签（`<create>`、`<str_replace>`、`<bash>` 等），QueryLoop 的工具解析与执行逻辑无需修改。
- **Rich 显示**：命令相关终端输出通过 `cli_print.py` 封装，禁止在业务代码中直接调用 `console.print()`。

### 14.2 安全约束

- 命令 `.md` 文件内容仅作为 LLM 上下文，不直接执行任何代码。
- LLM 根据命令指令自行决定是否调用工具（如 `bash` 执行 `openspec` CLI 命令）。
- 所有工具调用仍受 QueryLoop 的工具执行器（`tool_executor.py`）管控。

### 14.3 性能考虑

- 命令扫描在启动时一次性完成，不在每次用户输入时重复扫描。
- 命令文件内容缓存在内存中（`CommandInfo.content`），不在每次调用时重新读取文件。
- 如需支持热更新（运行时重新扫描），可提供 `/reload` 命令手动触发。

---

## 15. 附录：OpenSpec 命令文件内容摘要

### 15.1 propose.md

- **用途**：一步创建变更和所有规划文档。
- **输入**：变更名称（kebab-case）或描述。
- **流程**：
  1. 如无输入，询问用户想构建什么。
  2. 运行 `openspec new change "<name>"` 创建变更目录。
  3. 运行 `openspec status --change "<name>" --json` 获取构建顺序。
  4. 按依赖顺序创建 artifacts（proposal.md、design.md、tasks.md）。
  5. 显示最终状态，提示运行 `/opsx:apply`。
- **关键约束**：context 和 rules 是约束条件，不写入输出文件。

### 15.2 apply.md

- **用途**：根据 tasks.md 任务清单编写代码。
- **输入**：变更名称（可选，可从上下文推断）。
- **流程**：
  1. 选择变更。
  2. 检查状态，理解 schema。
  3. 获取 apply 指令。
  4. 读取上下文文件。
  5. 循环实现每个待完成任务。
  6. 完成后提示归档。
- **关键约束**：保持代码变更最小化，遇到问题暂停并询问。

### 15.3 explore.md

- **用途**：探索模式，思考而非实现。
- **输入**：任意主题（模糊想法、具体问题、变更名称等）。
- **流程**：无固定步骤，自由探索。
- **关键约束**：严禁编写代码，可以创建 OpenSpec 规划文档。

### 15.4 sync.md

- **用途**：将变更中的增量规格同步到主规格库。
- **输入**：变更名称（可选）。
- **流程**：
  1. 选择变更。
  2. 查找增量规格文件。
  3. 对每个增量规格，智能合并到主规格（ADDED/MODIFIED/REMOVED/RENAMED）。
  4. 显示同步摘要。
- **关键约束**：智能合并，保留未提及的现有内容。

### 15.5 archive.md

- **用途**：归档已完成的变更。
- **输入**：变更名称（可选）。
- **流程**：
  1. 选择变更。
  2. 检查 artifact 完成状态。
  3. 检查任务完成状态。
  4. 评估增量规格同步状态。
  5. 执行归档（移动到 archive 目录）。
  6. 显示摘要。
- **关键约束**：不阻断归档，仅警告并确认。

### 15.6 update.md

- **用途**：修订现有规划文档，保持一致性。
- **输入**：变更名称（可选）。
- **流程**：
  1. 选择变更。
  2. 获取变更的 artifacts。
  3. 理解用户的修订请求。
  4. 读取并协调各文档之间的一致性。
  5. 逐个确认并应用修订。
  6. 指引下一步操作。
- **关键约束**：仅编辑规划文档，严禁编辑实现代码。
