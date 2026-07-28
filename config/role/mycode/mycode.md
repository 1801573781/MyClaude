# MyClaude.md

## 1. 项目 DNA
- **定位**：Claude Code 的 Python 复刻，基于LLM的终端 AI 编程助手。
- **核心循环**：Query Loop —— 用户输入 → LLM 决策 → XML 工具执行 → 结果反馈 → 多轮循环直至 `<done>`。
- **技术栈**：Python + OpenAI SDK（调用 LLM）+ Rich（终端 UI）+ PyYAML（配置）+ pathlib（路径管理）。

## 2 文件系统规范

### 绝对路径强制（Mandatory）
所有文件操作必须使用**绝对路径**，严禁使用裸文件名或相对路径。
- 绝对路径必须包含完整盘符和目录层级，如 `${project_root}/src/query/chat_llm.py`
- 严禁使用 `src/query/chat_llm.py`、`code_output/test.py` 等相对路径
- 严禁使用裸文件名如 `spider_spec.md`
- 因为强制使用绝对路径，不再区分代码文件与需求文档的查看工具，统一使用 `<file_view path="绝对路径"/>` 查看任何文件

### 代码文件目录（正式模块）
代码文件（.py、.json、.yaml 等）必须存放在 `${project_root}/src/` 目录下。
- **严禁把代码文件直接放在 src/ 根目录**。即使只有一个文件，也必须放入子目录。
- **子目录由功能语义决定**。

| 功能类型 | 应放入的子目录 | 绝对路径示例 |
|---------|--------------|-------------|
| 终端显示、UI 渲染 | `src/cli/` | `${project_root}/src/cli/progress_bar.py` |
| LLM 交互、多轮循环、API 封装 | `src/query/` | `${project_root}/src/query/chat_llm.py` |
| 消息组装、系统提示词 | `src/message/` | `${project_root}/src/message/sys_prompt.md` |
| XML 工具解析、文件操作、命令执行 | `src/llm_tool/` | `${project_root}/src/llm_tool/file_tool.py` |
| 配置加载、通用工具函数 | `src/utility/` | `${project_root}/src/utility/config_loader.py` |

**新建子目录命名规范**：使用小写、下划线分隔、语义明确，如 `src/crawler/`、`src/parser/`、`src/api_client/`。

### 需求规格目录
需求文档（.md）必须存放在 `${project_root}/spec/` 目录下。
- 示例：`${project_root}/spec/spider_spec.md`

### 临时输出目录（探索/测试）
当用户表达**探索、验证、草稿、测试**意图时，代码文件放在 `${project_root}/code_output/` 目录，**严禁放入 `src/`**。

**触发词（包括但不限于）**："做个测试"、"测试一下"、"跑个测试"、"临时测试"、"生成一个文件看看"、"随便写一个"、"写个示例"、"写个 demo"、"草稿"、"试试"、"验证一下"、"临时"。

**优先级规则**：即使请求中同时包含需求规格，只要用户明确使用了上述探索性/测试性词语，**优先适用临时输出，而不是代码目录**。需求文档仅作为参考，代码文件仍然放入 `code_output/`。

**特别注意**：单独的"测试"一词歧义较大（可能指"生成测试代码"），优先根据上下文判断。如果用户明确表达"试试"、"临时"、"草稿"等意图，优先适用临时输出。

- 示例：`${project_root}/code_output/demo.py`、`${project_root}/code_output/temp_*.py`

### Skill 目录
Skill 行为模板（任务策略、工具组合规范、禁忌与示例）存放在 `${project_root}/skill/` 目录下。
- 文件名使用小写、下划线分隔，如 `add_tests.md`、`refactor.md`、`debug.md`
- Skill 不是代码文件，严禁放入 `src/`

### 正确与错误示例（路径格式）
正确：
- `<create path="${project_root}/src/tools/spider.py">...</create>`
- `<file_view path="${project_root}/spec/spider_spec.md"/>`
- `<file_view path="${project_root}/src/query/chat_llm.py"/>`
- `<create path="${project_root}/code_output/test.py">...</create>`

错误（严禁）：
- `<create path="${project_root}/src/spider.py">...</create>` ← 缺少子目录
- `<create path="${project_root}/src/config.yaml">...</create>` ← 配置文件禁止放 src/
- `<file_view path="spider_spec.md"/>` ← 裸文件名
- `<create path="code_output/test.py">...</create>` ← 相对路径
- `<create path="${project_root}/code_output/src/demo.py">...</create>` ← 路径嵌套错误

### 需求文档读取流程
当用户输入中包含需求规格引用，且**未表达测试/草稿/探索意图**时：
1. 从用户输入中提取文件名（如 `spider_spec.md`）
2. 调用 `<file_view path="${project_root}/spec/spider_spec.md"/>` 读取内容
3. 等待系统返回文档内容
4. 基于文档内容，调用 `<create>` 生成代码
5. 等待系统返回创建结果
6. 最后调用 `<done>` 结束

[注：LLM 行为规则（如工具与终止分离、失败恢复）由系统提示词 sys_prompt.md 统一管理，本文件仅描述项目路径规范。]


## 3. 架构契约
- **同步流式**：`chat_llm.stream_chat()` 使用 OpenAI 同步流式（`stream=True`），返回 `(content: str, is_truncated: bool)`。禁止在核心循环引入 async/await。
- **回调注入**：`QueryLoop.run()` 接收 `on_context_mgr`、`on_llm_text`、`on_tool_call` 等 Callable，由 `ClaudeStyleCLI` 注册 Rich 显示行为。引擎不直接 import cli_print。
- **消息格式**：发给 LLM 的 `api_messages` 必须是 `List[Dict[str, str]]`，仅含 `role` 和 `content`。
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

## 5. 快速启动（给 AI 自己用的上下文）
```bash
# 安装依赖
pip install openai rich pyyaml numpy pytest

# 配置 API Key
# 编辑 config.yaml → model.api_key: "sk-..."

# 运行
python -m src.myclaude

