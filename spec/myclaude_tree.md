# 需求规格：MyClaude 目录地图可视化生成器

## 1. 项目背景
MyClaude 项目需要一份可自动生成的目录地图，用于在终端直观展示任意 Python 项目的文件结构及每个文件的精炼概述。本需求要求编写一个独立 Python 脚本，运行时递归扫描项目目录，读取 `.gitignore` 排除规则，读取每个文件内容并交由大模型生成 ≤20 字的概述，最终输出标准树形目录。

## 2. 功能需求

### 2.1 核心功能
- **递归扫描**：扫描指定项目根目录下的所有文件（递归进入子目录）
- **读取 .gitignore**：运行时自动读取项目根目录下的 `.gitignore` 文件，按标准 Git 忽略语法排除目录和文件
  - 支持 glob 模式（如 `*.pyc`, `build/`）
  - 支持目录标记（以 `/` 结尾表示目录）
  - 支持否定模式（`!` 前缀）
  - 支持注释（`#` 前缀）
  - 若项目根目录无 `.gitignore`，则不做额外排除（仅适用扩展名兜底规则）
- **LLM 生成概述**：对每个未被排除的文件，读取其内容（设定上限 50KB，超出则截断），调用 DeepSeek LLM API 生成 ≤20 字的精炼概述
  - Prompt 模板：`请为文件 "{file_name}" 生成不超过20字的用途概述。文件内容如下：\n{content_snippet}\n只返回概述文本，不要解释。`
- **缓存机制**：概述结果持久化到被扫描项目根目录下的 `.tree_cache.md`，以 `文件绝对路径 + 文件修改时间` 为 key，避免重复调用 API
- **树形输出**：以 ASCII/Unicode 树形符号（`├──`, `└──`, `│`）渲染目录层级，每个文件节点右侧显示其 LLM 生成的概述

### 2.2 输出格式
- 树形连接线使用标准 Unicode 符号
- 文件名与概述之间至少保留 2 个空格
- 概述统一对齐于同一纵向参考线（建议固定列宽 45~55 字符处）
- 文件夹节点以 `/` 结尾标识，不显示概述（仅文件显示概述）

### 2.3 颜色要求
- **无特殊配色要求**：适配终端默认颜色即可（黑底白字或白底黑字）
- 若使用 `rich` 库，可使用默认主题，禁止强制指定 Dark Theme 或特定 RGB 色值

## 3. 技术约束

- **语言**：Python 3.12
- **依赖**：`rich`（终端树形渲染）、`openai`（调用 DeepSeek 兼容接口）
- **单文件**：脚本必须为单个 `.py` 文件
- **运行方式**：`python tree_visualizer.py &lt;项目根目录路径&gt; [--no-cache]`，直接在终端打印输出
- **编码**：UTF-8，确保中文正常显示
- **API 配置**：从环境变量 `DEEPSEEK_API_KEY` 读取 Key，模型固定为 `deepseek-chat`，base_url 为 `https://api.deepseek.com/v1`

## 4. 文件过滤规则

### 4.1 .gitignore 驱动（主要）
扫描前必须先读取项目根目录的 `.gitignore`，按标准语法计算忽略规则。被忽略的文件和目录不出现在树中。

### 4.2 扩展名兜底（补充）
无论 `.gitignore` 是否存在，以下二进制/非文本文件类型强制排除（大小写不敏感）：

`.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.ico`, `.mp4`, `.avi`, `.mov`, `.mp3`, `.wav`, `.zip`, `.tar`, `.gz`, `.rar`, `.7z`, `.exe`, `.dll`, `.so`, `.dylib`, `.bin`, `.dat`, `.db`, `.sqlite`, `.sqlite3`, `.pyc`, `.pyo`, `.class`, `.o`, `.a`

超过 50KB 的文件：仅读取前 50KB 内容提交给 LLM，并在概述后标记 `(截断)`。

## 5. 缓存机制

- **缓存文件**：`.tree_cache.md`，存放于被扫描项目的根目录下
- **缓存格式**：Markdown 表格，表头为 `| 文件路径 | 修改时间 | 概述 |`，每行一条记录
- **缓存 key**：`{file_absolute_path}:{mtime}` 的组合字符串
- **命中逻辑**：若缓存中存在该文件路径且修改时间一致，直接复用概述，不再调用 API
- **强制刷新**：支持 `--no-cache` 命令行参数，忽略缓存重新生成

缓存文件示例：

```markdown
# Tree Cache

| 文件路径 | 修改时间 | 概述 |
|---------|---------|------|
| D:/AI/MyClaude/config.yaml | 1715155200 | 全局配置：模型、路径、CLI 参数 |
| D:/AI/MyClaude/src/myclaude.py | 1715155300 | CLI 入口：输入与主循环 |
| D:/AI/MyClaude/src/cli/mycli.py | 1715155400 | ClaudeStyleCLI 外壳 |
```

## 6. 验收标准
运行 python tree_visualizer.py D:/AI/MyClaude 后，终端输出树形目录结构
每个文件节点右侧均有 LLM 生成的概述，且字数 ≤20
.gitignore 中声明忽略的文件/目录不出现在树中
二进制扩展名文件不出现在树中
二次运行时若文件未修改，应直接复用 .tree_cache.md，不再调用 API
树形层级缩进严格对齐，不得错位
无强制配色要求，终端默认颜色即可


## 7. 输出示例

MyClaude/
├── config.yaml              # 全局配置：模型、路径、CLI 参数
├── requirements.txt         # 核心依赖：openai, rich, pyyaml
├── pytest.ini              # pytest 测试配置
├── .gitignore              # 忽略临时与 IDE 目录
├── README.md               # 对外项目介绍（双语）
├── MyClaude.md             # AI/开发者内部项目指南
├── skill/
│   └── add_tests.md        # 生成单元测试 Skill 模板
└── src/
    ├── myclaude.py         # CLI 入口：输入与主循环
    ├── cli/
    │   ├── mycli.py        # ClaudeStyleCLI 外壳
    │   └── cli_print.py    # Rich 渲染工具集
    ├── query/
    │   ├── query_loop.py   # QueryLoop 多轮引擎
    │   ├── chat_llm.py     # MiniMax API 流式封装
    │   └── session_log.py  # 会话日志持久化
    ├── message/
    │   ├── llm_api_msg.py  # API 消息组装管理
    │   └── sys_prompt.md   # 系统提示词 Layer 1~7
    ├── llm_tool/
    │   ├── tool_executor.py # XML 工具解析与分发
    │   ├── file_tool.py     # 文件操作：增删改查
    │   └── cmd_bash.py      # 本地 Shell 命令执行
    └── utility/
        ├── config_loader.py # YAML 配置加载器
        └── normal_utility.py # 路径解析等通用函数

**注意**：示例中的概述为示意性内容，实际运行时应由 DeepSeek LLM 根据每个文件的真实内容动态生成。