# MyClaude.md

## 1. 项目 DNA
- **定位**：Claude Code 的 Python 复刻，基于国产大模型（MiniMax）的终端 AI 编程助手。
- **核心循环**：Query Loop —— 用户输入 → LLM 决策 → XML 工具执行 → 结果反馈 → 多轮循环直至 `<done>`。
- **技术栈**：Python 3.12 + OpenAI SDK（调用 MiniMax）+ Rich（终端 UI）+ PyYAML（配置）+ pathlib（路径管理）。
- **架构风格**：全同步（刻意不用 async/await），降低心智负担；终端显示与业务逻辑部分解耦。
- **非目标**：不做 Web UI、不做多模态、不做分布式并发、不追求完全解耦（接受 CLI 与 QueryLoop 在"何时打印"上存在合理耦合）。

## 2. 目录地图
MyClaude/
├── config.yaml              # 全局配置：模型、路径、CLI 参数
├── requirements.txt         # 核心依赖（openai, rich, pyyaml, numpy）
├── pytest.ini              # pytest 测试配置
├── .gitignore              # 忽略 code_output/, log/, context/, .idea/ 等
├── README.md               # 对外项目介绍（双语）
├── MyClaude.md             # 本文件：AI/开发者内部项目指南
│
├── skill/                  # Skill 行为模板：任务策略、工具组合、禁忌与示例
│   └── add_tests.md        # 生成单元测试的 Skill 模板（示例）
│
└── src/
├── myclaude.py         # CLI 入口，负责用户输入、命令分发、主循环
│
├── cli/
│   ├── mycli.py        # ClaudeStyleCLI：终端外壳，注册回调给 QueryLoop
│   └── cli_print.py    # Rich 渲染工具：打字机、Markdown、Syntax 高亮、状态动画
│
├── query/
│   ├── query_loop.py   # QueryLoop 引擎：LLM 多轮交互、工具编排、死循环检测
│   ├── chat_llm.py     # MiniMax API 封装：流式请求、截断检测、自动重试
│   └── session_log.py  # 会话日志：Markdown 格式化、持久化到 log/.md
│
├── message/
│   ├── llm_api_msg.py  # api_messages 组装：init / append_llm_response / append_tool_result
│   └── sys_prompt.md   # 系统提示词（Layer 1~7 + Negative Example）
│
├── llm_tool/
│   ├── tool_executor.py # XML 工具解析（parse_tools）与分发（execute_tool）
│   ├── file_tool.py     # 文件操作：create / view / str_replace，含重复创建保护
│   └── cmd_bash.py      # 本地 shell 命令执行（Windows CMD/PowerShell）
│
└── utility/
├── config_loader.py # YAML 加载 → SimpleNamespace 点号访问
└── normal_utility.py # strip_thinking、路径解析等通用函数
├── MyClaude.md         # MyClaude项目说明


## 2.5 文件系统规范

### 绝对路径强制（Mandatory）
所有文件操作必须使用**绝对路径**，严禁使用裸文件名或相对路径。
- 绝对路径必须包含完整盘符和目录层级，如 `D:/AI/MyClaude/src/query/chat_llm.py`
- 严禁使用 `src/query/chat_llm.py`、`code_output/test.py` 等相对路径
- 严禁使用裸文件名如 `spider_spec.md`
- 因为强制使用绝对路径，不再区分代码文件与需求文档的查看工具，统一使用 `<file_view path="绝对路径"/>` 查看任何文件

### 代码文件目录（正式模块）
代码文件（.py、.json、.yaml 等）必须存放在 `D:/AI/MyClaude/src/` 目录下。
- **严禁把代码文件直接放在 src/ 根目录**。即使只有一个文件，也必须放入子目录。
- **子目录由功能语义决定**。

| 功能类型 | 应放入的子目录 | 绝对路径示例 |
|---------|--------------|-------------|
| 终端显示、UI 渲染 | `src/cli/` | `D:/AI/MyClaude/src/cli/progress_bar.py` |
| LLM 交互、多轮循环、API 封装 | `src/query/` | `D:/AI/MyClaude/src/query/chat_llm.py` |
| 消息组装、系统提示词 | `src/message/` | `D:/AI/MyClaude/src/message/sys_prompt.md` |
| XML 工具解析、文件操作、命令执行 | `src/llm_tool/` | `D:/AI/MyClaude/src/llm_tool/file_tool.py` |
| 配置加载、通用工具函数 | `src/utility/` | `D:/AI/MyClaude/src/utility/config_loader.py` |

**新建子目录命名规范**：使用小写、下划线分隔、语义明确，如 `src/crawler/`、`src/parser/`、`src/api_client/`。

### 需求规格目录
需求文档（.md）必须存放在 `D:/AI/MyClaude/spec/` 目录下。
- 示例：`D:/AI/MyClaude/spec/spider_spec.md`

### 临时输出目录（探索/测试）
当用户表达**探索、验证、草稿、测试**意图时，代码文件放在 `D:/AI/MyClaude/code_output/` 目录，**严禁放入 `src/`**。

**触发词（包括但不限于）**："做个测试"、"测试一下"、"跑个测试"、"临时测试"、"生成一个文件看看"、"随便写一个"、"写个示例"、"写个 demo"、"草稿"、"试试"、"验证一下"、"临时"。

**优先级规则**：即使请求中同时包含需求规格，只要用户明确使用了上述探索性/测试性词语，**优先适用临时输出，而不是代码目录**。需求文档仅作为参考，代码文件仍然放入 `code_output/`。

**特别注意**：单独的"测试"一词歧义较大（可能指"生成测试代码"），优先根据上下文判断。如果用户明确表达"试试"、"临时"、"草稿"等意图，优先适用临时输出。

- 示例：`D:/AI/MyClaude/code_output/demo.py`、`D:/AI/MyClaude/code_output/temp_*.py`

### Skill 目录
Skill 行为模板（任务策略、工具组合规范、禁忌与示例）存放在 `D:/AI/MyClaude/skill/` 目录下。
- 文件名使用小写、下划线分隔，如 `add_tests.md`、`refactor.md`、`debug.md`
- Skill 不是代码文件，严禁放入 `src/`

### 正确与错误示例（路径格式）
正确：
- `<create path="D:/AI/MyClaude/src/tools/spider.py">...</create>`
- `<file_view path="D:/AI/MyClaude/spec/spider_spec.md"/>`
- `<file_view path="D:/AI/MyClaude/src/query/chat_llm.py"/>`
- `<create path="D:/AI/MyClaude/code_output/test.py">...</create>`

错误（严禁）：
- `<create path="D:/AI/MyClaude/src/spider.py">...</create>` ← 缺少子目录
- `<create path="D:/AI/MyClaude/src/config.yaml">...</create>` ← 配置文件禁止放 src/
- `<file_view path="spider_spec.md"/>` ← 裸文件名
- `<create path="code_output/test.py">...</create>` ← 相对路径
- `<create path="D:/AI/MyClaude/code_output/src/demo.py">...</create>` ← 路径嵌套错误

### 需求文档读取流程
当用户输入中包含需求规格引用，且**未表达测试/草稿/探索意图**时：
1. 从用户输入中提取文件名（如 `spider_spec.md`）
2. 调用 `<file_view path="D:/AI/MyClaude/spec/spider_spec.md"/>` 读取内容
3. 等待系统返回文档内容
4. 基于文档内容，调用 `<create>` 生成代码
5. 等待系统返回创建结果
6. 最后调用 `<done>` 结束

[注：LLM 行为规则（如工具与终止分离、失败恢复）由系统提示词 sys_prompt.md 统一管理，本文件仅描述项目路径规范。]


## 3. 架构契约
- **同步流式**：`chat_llm.stream_chat()` 使用 OpenAI 同步流式（`stream=True`），返回 `(content: str, is_truncated: bool)`。禁止在核心循环引入 async/await。
- **回调注入**：`QueryLoop.run()` 接收 `on_context_mgr`、`on_llm_text`、`on_tool_call` 等 Callable，由 `ClaudeStyleCLI` 注册 Rich 显示行为。引擎不直接 import cli_print。
- **消息格式**：发给 LLM 的 `api_messages` 必须是 `List[Dict[str, str]]`，仅含 `role` 和 `content`。**MiniMax 严禁对话中间出现 `role="system"`**（报错 2013）。
- **工具协议**：LLM 输出 XML 标签。`parse_tools()` 提取后返回 `(remaining_text, tools_list)`。`tools_list` 元素为 `{"llm_tool": "...", "params": {...}}`。
- **路径解析**：所有文件路径通过 `Path(root) / path` 拼接。若传入绝对路径，直接透传；相对路径则拼接到 `code_output_root`。
- **截断与重试**：`_chat_with_retry()` 检测到 `finish_reason == "length"` 时自动翻倍 `max_tokens`（上限 64000），最多重试 3 次。
- **死循环熔断**：QueryLoop 内维护 `last_tool_sig`，连续两轮工具签名完全一致时强制终止。
- **日志格式**：`session_log.py` 输出 Markdown（非 JSON），中文 `ensure_ascii=False`，`\\n` 还原为真实换行。

## 4. 开发纪律（Mandatory）
- **[红线]禁止在 api_messages 中间插入 system 角色**。倒数提醒、工具结果、上下文补充一律用 `role="user"`，前缀加 `[系统提醒]` 或 `[TOOL_RESULT]` 区分。
- **[红线]file_create 不得覆盖已存在文件**。若文件存在且非空，返回警告信息，迫使 LLM 改用 `<str_replace>` 或 `<file_view>`。
- **[红线]parse_tools 必须兼容 `<done>` 无闭合标签**。正则使用 `<done>(.*?)(?:</done>|$)`，防止 LLM 漏写闭合导致循环无法退出。
- **[红线]工具执行结果一律返回 dict，不是 list**。`execute_tool()` 返回 `{"role": "user", "content": "..."}`，确保 `api_messages.append()` 不会嵌套列表。
- **[规范]新增配置项必须同步修改 config.yaml 与 config_loader.py**，使用 `SimpleNamespace` 支持点号访问（`global_cfg.model.api_key`）。
- **[规范]Rich 显示接口必须通过 cli_print 封装**，禁止在业务代码里直接写 `console.print()`。
- **[规范]成员函数之间空两行**（PyCharm Code Style），有默认值的 dataclass 字段必须放在无默认值字段之后。

## 5. 常见任务速查
| 任务 | 应该改的文件 | 关键注意事项 |
|------|-------------|-------------|
| 新增 LLM 提供商 | `query/chat_llm.py` + `config.yaml` | 保持 `stream_chat()` 返回 `(content, is_truncated)` 签名；MiniMax 与 OpenAI 的 `finish_reason` 语义一致即可复用 |
| 新增 XML 工具（如 `<search>`） | `llm_tool/tool_executor.py`（parse_tools/execute_tool）+ `llm_tool/file_tool.py` | 在 `parse_tools()` 的 `patterns` 列表里加正则；返回 `{"role":"user", "content":"..."}` |
| 修改终端样式/主题 | `cli/cli_print.py` | 新增封装函数如 `print_tool_call()`，不要在 `query_loop.py` 里直接调 `console.print()` |
| 修改系统提示词 | `message/sys_prompt.md` | 保持 Layer 1~7 结构；Negative Example 紧跟 Output Example；Layer 6 声明 Windows 环境 |
| 添加配置项 | `config.yaml` + `utility/config_loader.py` | 用 `_dict_to_namespace()` 递归转换；YAML 中文字段不要加引号（除非含特殊符号） |
| 接入记忆系统 | `query/query_loop.py`（注入时机）+ 新增 `memory/` 模块 | 参考之前设计的 `MemoryStore` / `MemoryRetrieval` / `MemoryInjector`；注意 MiniMax 的 token 上限 |
| 修改会话日志格式 | `query/session_log.py` + `query/query_loop.py`（调用点） | 保持 Markdown 输出；时间戳用 `%Y-%m-%d %H : %M : %S`；批次之间用分隔线 |
| 双平台推送 | 命令行：`git push`（默认 Gitee）/ `git push github master`（GitHub） | origin 已指向 Gitee；github 为备用远程 |

## 6. 快速启动（给 AI 自己用的上下文）
```bash
# 安装依赖
pip install openai rich pyyaml numpy pytest

# 配置 API Key
# 编辑 config.yaml → model.api_key: "sk-..."

# 运行
python -m src.myclaude

## 7. 当前已知限制
LLM 偶尔不输出 <done>，依赖 QueryLoop 的 not tools 兜底分支退出。
strip_thinking() 对 MiniMax 中文思考过程的过滤依赖正则，可能存在误伤。
尚未接入完整的分层记忆系统（Working/Short-term/Long-term），目前仅通过 api_messages 维护短期上下文。
终端打字机效果与 Markdown 渲染的切换逻辑基于关键词启发式（def  / import ），非 100% 准确。
