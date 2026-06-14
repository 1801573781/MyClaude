# MyClaude 系统需求规格文档

> **版本**: v1.0  
> **生成日期**: 2026-05-28  
> **来源**: 基于 MyClaude 源代码逆向提取  
> **用途**: 作为系统测试用例的"期望行为"来源

---

## 目录

1. [总体架构与核心循环](#1-总体架构与核心循环)
2. [工具协议层](#2-工具协议层)
3. [文件操作层](#3-文件操作层)
4. [消息管理层](#4-消息管理层)
5. [记忆系统层](#5-记忆系统层)
6. [会话日志层](#6-会话日志层)
7. [LLM 交互层](#7-llm-交互层)
8. [命令执行层](#8-命令执行层)
9. [技能系统层](#9-技能系统层)
10. [CLI 交互与显示层](#10-cli-交互与显示层)
11. [错误处理与恢复层](#11-错误处理与恢复层)

---

## 1. 总体架构与核心循环

### 1.1 系统定位

MyClaude 是一个运行在 Windows CMD/PowerShell 环境下的终端 AI 编程助手。它接收用户的自然语言指令，通过多轮对话循环调用 LLM，解析 LLM 输出的 XML 工具标签，执行文件操作、命令执行等工具，并将结果反馈给 LLM，直至任务完成。

**技术栈**: Python 3.12 + OpenAI SDK + Rich（终端 UI）+ PyYAML（配置）+ pathlib（路径管理）。全同步架构，不使用 async/await。

### 1.2 核心循环（Query Loop）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| QL-001 | **多轮对话循环** | 系统收到用户输入后，进入多轮对话循环（最多 `max_turns` 轮，可配置）。每轮：发送请求给 LLM → 解析响应 → 执行工具 → 判断是否继续。 |
| QL-002 | **正常退出条件** | 当 LLM 输出 `<done>` 标签时，系统正常结束当前会话。 |
| QL-003 | **兜底退出条件** | 当 LLM 未输出 `<done>` 但响应中无工具调用时：编码模式下提示"LLM 未调用 done 工具，自动结束"；聊天模式下直接结束。 |
| QL-004 | **强制终止** | 当对话轮次达到 `max_turns` 且未退出时，系统打印"达到最大轮次限制，强制结束"并终止。 |
| QL-005 | **死循环熔断** | 系统维护 `last_tool_sig`：当连续两轮 LLM 输出的工具签名（工具名 + 参数摘要）完全一致时，强制终止循环。 |
| QL-006 | **工具执行** | 每轮对话中，系统先执行所有非 `<done>` 工具（按 LLM 输出顺序），将结果追加到消息历史，最后处理 `<done>`（如有）。 |
| QL-007 | **完整轮次记忆** | 每轮对话结束后，系统将本轮（用户输入 + LLM 思考 + LLM 应答 + 工具执行结果）打包为一条完整记忆，调用 Memory 模块的 `add()` 存储。 |
| QL-008 | **会话生命周期** | 每次 `run()` 调用创建新 Session：初始化 `api_messages`、`SessionLog`、`is_chat_mode`。会话开始时执行 `memory.maintain()`，结束时执行 `memory.compact()` + `memory.maintain()`。 |

### 1.3 回调注入模式

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| QL-009 | **回调注入** | `QueryLoop.run()` 接收 `on_context_mgr`、`print_info`、`print_llm_rsp`、`print_tool_call`、`print_tool_result`、`print_llm_reasoning` 等回调函数，由 CLI 模块注册。QueryLoop 不直接导入 CLI 模块的打印函数。 |
| QL-010 | **on_context_mgr** | 用于在等待 LLM 回复期间显示"Thinking"状态（如终端闪烁指示器）。 |

### 1.4 模式区分

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| QL-011 | **聊天/编码模式区分** | `is_chat_mode` 初始为 `True`。当 LLM 输出任何工具调用（包括 `<done>`），切换为 `False`（编码模式）。聊天模式下无工具时直接结束；编码模式下无工具时打印提示。 |

---

## 2. 工具协议层

### 2.1 支持的 XML 工具

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| TP-001 | **`<file_view>`** | 查看文件内容或目录列表。属性：`path`（必填）、`limit`（可选，最多返回行数）、`offset`（可选，起始行号，1-based）。 |
| TP-002 | **`<create>`** | 创建新文件。属性：`path`（必填）、`summary`（必填，≤50 字符）。内容放在标签体内。 |
| TP-003 | **`<str_replace>`** | 替换文件中的指定内容。属性：`path`（必填）、`summary`（必填，≤50 字符）。子标签：`<old>`（旧文本）、`<new>`（新文本）。 |
| TP-004 | **`<bash>`** | 执行 Shell 命令。命令文本放在标签体内。 |
| TP-005 | **`<use_skill>`** | 加载技能。属性：`name`（技能名）。 |
| TP-006 | **`<done>`** | 标记任务完成。可选内容：完成说明文本。不需要闭合标签也可识别。 |

### 2.2 工具解析（parse_tools）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| TP-007 | **按顺序解析** | 按 XML 标签在响应文本中的出现顺序提取工具调用。返回 `(remaining_text, tools_list)`，其中 `remaining_text` 为去除工具标签后的纯文本。 |
| TP-008 | **容器块保护** | 位于 `<create>` 或 `<str_replace>` 标签体内的 XML 标签（如 `<done>`、`<file_view>` 等）不被视为工具调用。 |
| TP-009 | **容错解析** | `<str_replace>` 的 `<new>` 块支持降级解析：优先匹配 `</new>`，找不到时尝试 `</old>` 或 `</str_replace>` 作为结束标记。 |
| TP-010 | **<done> 宽松匹配** | `<done>` 正则使用 `<done>(.*?)(?:</done>\|$)`，允许省略闭合标签。 |
| TP-011 | **reasoning_content 兜底** | 当 LLM 主响应中未解析到工具时，尝试从 `reasoning_content` 中提取工具调用（宽松匹配）。 |

### 2.3 工具执行（execute_code_tool）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| TP-012 | **结果格式** | 每个工具执行结果包装为 `{"role": "user", "content": "[工具名] 工具执行结果：..."}` 或 `[工具名] 工具执行结果：\n...`（bash 结果换行）。 |
| TP-013 | **file_view 截断** | 当返回目录列表超过 30 行时，截断前 30 行并附加"（共 N 项，已截断...）"提示。 |
| TP-014 | **create 结果** | 返回 `"文件已创建：{path}，摘要：{summary}"`。如果 summary 为空，返回 `"已创建 {path}（{size} 字符）"`。 |
| TP-015 | **str_replace 结果** | 返回 `"文件已修改：{path}，摘要：{summary}"`。如果 summary 为空，返回 `"文件已修改：{path}，替换了 1 处（{old_len} → {new_len} 字符）"`。 |
| TP-016 | **use_skill 结果** | 成功时返回 `"已激活技能 '{name}'，完整指令如下：\n\n{full_content}"`。失败时返回以 `[CRITICAL ERROR]` 开头的错误信息，并附可用技能列表。 |

---

## 3. 文件操作层

### 3.1 文件创建（file_create）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| FO-001 | **新文件创建** | 将指定内容写入指定路径。文件不存在时创建新文件，成功返回包含文件大小或摘要的确认信息。 |
| FO-002 | **已存在文件保护** | 当目标文件已存在且非空时，返回 `[BLOCKED]` 警告信息，拒绝覆盖。必须改用 `<str_replace>` 或先 `<file_view>` 查看。 |
| FO-003 | **目录自动创建** | 当目标路径的父目录不存在时，自动创建父目录。 |
| FO-004 | **编码容错** | 写入文件时使用 UTF-8 编码，失败时尝试 GBK 等编码。 |

### 3.2 文件替换（file_str_replace）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| FO-005 | **精确替换** | 在文件中查找 `<old>` 指定的文本，替换为 `<new>` 指定的文本。仅替换**首次**匹配。 |
| FO-006 | **匹配失败处理** | 当 `<old>` 文本在文件中不存在时，返回 `[BLOCKED] 未找到匹配片段`。 |
| FO-007 | **多行替换** | 支持跨多行的 `<old>` 和 `<new>` 文本（包括代码块）。 |

### 3.3 文件查看（file_view）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| FO-008 | **文件内容查看** | 给定文件路径，返回文件内容文本。 |
| FO-009 | **目录列表查看** | 给定目录路径，返回目录内容列表，文件标记为 `[FILE]`，目录标记为 `[DIR]`。 |
| FO-010 | **分页支持** | 支持 `limit`（最多行数）和 `offset`（起始行，1-based）参数。 |
| FO-011 | **路径安全** | 禁止访问敏感系统目录（如 C:\Windows\System32），返回 `[BLOCKED]`。 |
| FO-012 | **不存在处理** | 当路径不存在时，返回 `错误：路径不存在`。 |

### 3.4 路径解析规则

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| FO-013 | **绝对路径透传** | 传入绝对路径时直接使用，不拼接任何根目录。 |
| FO-014 | **相对路径映射** | 传入相对路径时拼接到 `code_output_root`（由全局配置指定）。路径安全检测基于拼接后的绝对路径。 |

---

## 4. 消息管理层

### 4.1 API 消息格式

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| MM-001 | **消息结构** | 发送给 LLM 的 `api_messages` 为 `List[Dict[str, str]]`，每条消息仅含 `role` 和 `content` 两个字段。 |
| MM-002 | **禁止中间 system 角色** | 在对话中途（第一轮之后）严禁出现 `role="system"` 的消息（MiniMax 兼容性要求，会报错 2013）。系统提醒、工具结果、记忆上下文一律使用 `role="user"`，前缀加 `[系统提醒]` 或 `[TOOL_RESULT]` 区分。 |

### 4.2 消息注入顺序

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| MM-003 | **首轮消息初始化** | 第一轮对话时，按顺序注入：系统提示词（sys_prompt.md）→ 项目上下文（MyClaude.md）→ 项目目录树 → Installed Skills 清单 → 记忆上下文 → 用户输入。 |
| MM-004 | **记忆上下文注入** | 第一轮时，调用 `memory.get_context_for_query(user_input)` 获取记忆上下文，以 `role="user"` 追加到消息列表。如果返回空字符串，不注入。 |
| MM-005 | **工具结果注入** | 每轮工具执行后，将结果以 `role="user"` 追加到消息列表，前缀为 `[工具名] 工具执行结果：`。 |
| MM-006 | **LLM 响应注入** | 每轮 LLM 返回后，将去除 thinking 的原始响应以 `role="assistant"` 追加到消息列表。 |
| MM-007 | **最后轮次提醒** | 当 `turn == max_turns` 且非聊天模式时，追加一条 `role="user"` 的命令式提醒："命令：如果你已完成所有修改，请立即调用 `<done>` 结束任务。不要继续调用其他工具。" |

### 4.3 Thinking 内容处理

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| MM-008 | **strip_thinking** | 调用 `strip_thinking()` 移除 LLM 响应中的思考过程标签（如 Claude 风格的 `<thinking>` 块、MiniMax 中文思考过程的 XML 标签）。 |
| MM-009 | **thinking 显示控制** | 当 `show_thinking=False`（默认）时，向用户展示去除 thinking 后的内容；当 `show_thinking=True` 时，展示保留 thinking 的原始内容。日志始终保留完整原始内容。 |

---

## 5. 记忆系统层

### 5.1 统一接口（MemoryInterface）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| ME-001 | **add(role, content, metadata)** | 添加一条记忆到短期记忆并持久化。返回记忆 ID。 |
| ME-002 | **get(memory_id)** | 按 ID 获取单条记忆。返回 dict（含 id、role、content、timestamp、metadata）或 None。 |
| ME-003 | **search(query, top_k, filters)** | 语义检索，返回最相关的 top_k 条记忆。支持 role、tag、time_range 等过滤。 |
| ME-004 | **get_working_memory()** | 获取工作记忆的格式化文本。返回空字符串（当无工作记忆时）。 |
| ME-005 | **get_context_for_query(query)** | 根据用户查询获取记忆上下文（工作记忆 + 检索结果），返回格式化的 Markdown 文本。 |
| ME-006 | **update(memory_id, fields)** | 更新指定记忆的字段。返回是否成功。 |
| ME-007 | **delete(memory_id)** | 删除单条记忆。返回是否成功。 |
| ME-008 | **clear_all()** | 清空全部记忆（包括工作/短期/长期及备份文件）。返回删除条数。 |
| ME-009 | **compact()** | 触发记忆压缩（短期 → 长期摘要）。返回压缩条数。 |
| ME-010 | **stats()** | 返回记忆统计信息（各层数量、存储大小）。 |
| ME-011 | **maintain()** | 执行维护（遗忘过期记忆、优化索引）。返回清理条数。 |

### 5.2 三层记忆架构

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| ME-012 | **工作记忆** | 仅存于内存，最大容量可配置（默认 20 Turns）。超过容量时丢弃最旧的条目（FIFO）。 |
| ME-013 | **短期记忆** | 持久化存储（JSON），最大容量可配置（默认 200 条）。超过容量时触发 `compact()`。每条记忆有 TTL（默认 86400 秒），过期后被 `maintain()` 清理。 |
| ME-014 | **长期记忆** | 持久化存储（JSON），最大容量可配置（默认 2000 条）。由压缩器生成的摘要记忆。 |

### 5.3 Memory1 后端（Embedding + FAISS）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| ME-015 | **向量检索** | 使用 Embedding 函数将记忆文本转为向量，存入 FAISS 索引。检索时计算查询向量与记忆向量的语义相似度。 |
| ME-016 | **混合打分** | 检索排序采用混合打分：语义相关性 0.6 + 时间衰减 0.25 + 重要性权重 0.15。 |
| ME-017 | **指数遗忘** | 基于半衰期（默认 72 小时）计算时间衰减权重。`maintain()` 清理超过 TTL 的短期记忆并从 FAISS 索引中移除。 |
| ME-018 | **降级机制** | 当 Embedding 不可用（API 调用失败、缺少依赖、配置错误）时，检索降级为关键词匹配。 |
| ME-019 | **持久化与恢复** | 记忆存储在 JSON 文件中，支持滚动备份（默认 3 个备份文件）。启动时从持久化存储重建 FAISS 索引。JSON 文件损坏时自动从最新备份恢复。 |
| ME-020 | **索引重建** | 记忆变更后按需重建 FAISS 索引。若 FAISS 不可用（未安装），降级为纯 JSON 暴力搜索。 |

### 5.4 Memory2 后端（LLM 召回）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| ME-021 | **LLM 召回检索** | 检索时将所有候选记忆格式化后提交给 LLM，由 LLM 直接评分筛选。不使用 Embedding/FAISS。 |
| ME-022 | **预过滤** | 检索前按时间窗口（默认 30 天）、标签、候选数量（默认 200 条）预过滤候选记忆。 |
| ME-023 | **LLM 压缩** | 短期记忆超量时，调用 LLM 将多条记忆压缩为一条长期摘要。标记原始记忆为"已压缩"。 |
| ME-024 | **压缩降级** | LLM 压缩失败时，降级为简单的 FIFO 删除（删除最旧的超量条目）。 |
| ME-025 | **评分阈值** | 检索结果按 LLM 评分过滤，低于 `min_relevance`（默认 0.50）的记忆不注入。 |

### 5.5 记忆注入格式

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| ME-026 | **前缀标识** | 注入的记忆上下文以 `[系统提醒] 以下是与当前任务相关的历史记忆，仅供参考：` 或 `[系统提醒] 以下是与当前任务可能相关的历史记忆：` 开头。 |
| ME-027 | **分块展示** | 工作记忆以 `[当前任务上下文]` 区块展示，每个条目格式为 `- [Turn N] {角色图标} {内容}`（截断 300 字符）。检索记忆以 `[相关历史记忆]` 区块展示，含 ID、内容、相关性分数（如 `(相关性: 0.85)`）。 |
| ME-028 | **Token 预算** | 注入器按配置的 `max_tokens`（默认 2000 tokens）截断过长记忆上下文。 |
| ME-029 | **去重合并** | 工作记忆与检索结果合并时，按内容前缀去重，相同内容只保留一次。 |

### 5.6 记忆生命周期

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| ME-030 | **新任务开始时** | 每个 `run()` 调用开始时，执行 `memory.maintain()`（遗忘过期记忆，不删除持久化数据）。 |
| ME-031 | **会话结束时** | 会话退出前，执行 `memory.compact()` + `memory.maintain()`（压缩 + 遗忘）。 |
| ME-032 | **添加时自动压缩** | `add()` 调用后，若短期记忆数量超过 `short_term_max`，自动触发 `compact()`。 |
| ME-033 | **初始化降级** | 记忆模块初始化失败时，降级为 `NoopMemory`（空实现），系统不崩溃，所有记忆操作返回安全默认值。 |

---

## 6. 会话日志层

### 6.1 输出格式

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| SL-001 | **双格式支持** | 支持 Markdown（`.md`）和 HTML（`.html`）两种日志格式，由配置 `log.format` 决定。 |
| SL-002 | **文件命名** | 日志文件名格式：`MyClaude YYYY-MM-DD HH-MM-SS.{ext}`，存储于 `logs/` 目录。 |

### 6.2 日志结构（Markdown）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| SL-003 | **Turn 级标记** | 每个 Turn 以 `## 🔄 Turn N` 开头，附时间戳 `**🕐 YYYY-MM-DD HH : MM : SS**`。 |
| SL-004 | **角色标识** | 消息以角色图标 + 大写角色名标记：`### ⚙️ SYSTEM`、`### 👤 USER`、`### 🤖 ASSISTANT`。 |
| SL-005 | **工具记录** | 工具调用以 `### 🔧 Tool: \`工具名\`` 标记，参数和结果格式化展示。 |
| SL-006 | **推理内容** | 推理内容以 `<details>` 折叠块记录，摘要为"展开查看推理过程"。 |
| SL-007 | **批次分隔** | Turn 之间用 `═════...═════`（80 个 `═` 字符）分隔线分隔。 |
| SL-008 | **会话标题** | 日志开头记录会话时间和文件名：`**🕐 时间**` + `> 📄 Session: \`文件名\``。 |

### 6.3 日志结构（HTML）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| SL-009 | **Turn 折叠** | 每个 Turn 为可折叠的 `<div class="entry">`。默认仅展开最后一个 Turn，其他折叠。 |
| SL-010 | **多级折叠** | Turn 内按内容类型分为 11 个 Section 子折叠：⚙️ 系统宪法、⚙️ 系统提示、📦 系统技能、📋 项目宪法、🗂️ 项目目录、🧠 记忆召回、👤 用户输入、💭 LLM 思考、🤖 LLM 应答、🔧 工具执行、🧠 记忆召回（空时占位）。 |
| SL-011 | **记忆召回合格式** | 记忆召回合中，系统提醒前缀与各条记忆拆分，每条记忆生成带 📌 图标的独立折叠块。工作记忆显示 Turn 编号，检索记忆显示相关性分数。 |
| SL-012 | **空记忆占位** | 当无记忆召回时，自动插入占位 section，显示"没有召回到相关记忆"（灰色斜体）。 |
| SL-013 | **Session Banner** | 日志顶部显示会话标题栏：📄 文件名 + 🕐 时间，紫色渐变背景。 |

### 6.4 Python 语法高亮（HTML）

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| SL-014 | **关键字** | Python 关键字（def、class、import、return 等）以深蓝色（#0033B3）渲染。 |
| SL-015 | **字符串** | 字符串（单引号/双引号/三引号）以绿色（#008000）渲染。 |
| SL-016 | **注释** | 注释（# 开头）以灰色（#808080）渲染。 |
| SL-017 | **数字** | 数值字面量以蓝色（#0000FF）渲染。 |
| SL-018 | **装饰器** | 装饰器（@ 开头）以暗黄色（#BBB529）渲染。 |
| SL-019 | **内置函数** | 常用内置函数（print、len、range 等）以紫色（#7B0099）渲染。 |
| SL-020 | **XML 标签保护** | 代码中的 XML 工具标签（`<create>`、`<str_replace>` 等）被正确转义，不破坏 HTML 结构。 |

### 6.5 去重与统计

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| SL-021 | **api_messages 去重** | 使用 MD5 哈希比较每个 Turn 的 `api_messages`，仅记录与上一 Turn 不同的新增消息。系统提示词、项目宪法、目录树等固定内容只在第一个 Turn 完整记录。 |
| SL-022 | **Token 近似统计** | 请求 token = 发送消息总字节数 // 2；响应 token = LLM 输出字符数 // 2。 |
| SL-023 | **flush 机制** | 每个 Turn 结束后，缓冲内容一次性写入文件（Markdown 追加，HTML 重构）。 |

---

## 7. LLM 交互层

### 7.1 流式聊天

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| LL-001 | **同步流式** | 使用 OpenAI SDK 的同步流式调用（`stream=True`），返回 `(content: str, is_truncated: bool, reasoning_content: str)`。 |
| LL-002 | **内容收集** | 从流式 chunk 中逐块收集 `choice.delta.content` 拼接为完整响应。 |
| LL-003 | **推理内容收集** | 从流式 chunk 中逐块收集 `choice.delta.reasoning_content` 拼接为完整推理内容（如 DeepSeek-R1 的思维链）。 |
| LL-004 | **finish_reason 检测** | 检测每个 chunk 的 `finish_reason`：`"length"` 表示因 max_tokens 不足截断；`"stop"` 表示正常结束；其他表示异常。 |

### 7.2 截断重试

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| LL-005 | **自动翻倍重试** | 当检测到 `finish_reason == "length"` 时，自动将 `max_tokens` 翻倍（初始值可配置，默认 9000），上限 64000，最多重试 3 次。 |
| LL-006 | **重试后仍截断** | 重试次数用尽或 token 达到上限后，返回带截断标记 `[ERROR: 输出被截断，max_tokens 不足]` 的结果，由调用方（QueryLoop）决定后续处理。 |
| LL-007 | **非截断原因正常返回** | `finish_reason` 为 `"stop"` 时直接返回，不触发重试。其他异常结束原因在响应末尾追加 `[流结束原因: ...]` 提示。 |

### 7.3 异常处理

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| LL-008 | **API 错误** | `APIError`（含内容安全拦截、账号异常）返回 `[API_ERROR: {error_body}]`。 |
| LL-009 | **网络/限流错误** | `APIConnectionError` 或 `RateLimitError` 返回 `[API_ERROR: 网络/限流问题，{e}]`。 |
| LL-010 | **流式读取异常** | 流读取过程中意外中断时返回 `[API_ERROR: 流式读取异常，{e}]`。 |
| LL-011 | **所有异常不崩溃** | 任何异常都会被捕获并以 `[API_ERROR: ...]` 格式返回，不导致系统崩溃。 |

### 7.4 多提供商支持

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| LL-012 | **配置驱动** | 通过 `config.yaml` 的 `model.provider` 选择提供商，从 `model_key.yaml` 读取对应 API Key 和 Base URL。 |
| LL-013 | **兼容 MiniMax** | 消息格式中禁止中间出现 `role="system"`，finish_reason 语义与 OpenAI 对齐。 |

---

## 8. 命令执行层

### 8.1 Bash 工具

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| CM-001 | **命令执行** | 使用 `subprocess.run()` 执行 Shell 命令，捕获标准输出（stdout）和标准错误（stderr）。 |
| CM-002 | **超时控制** | 支持命令执行超时（默认 120 秒），超时后终止进程并返回超时错误信息。 |
| CM-003 | **输出合并** | 标准输出和标准错误合并返回，格式化为 `{stdout}\n{stderr}`（如 stderr 非空）。 |
| CM-004 | **环境约束** | 运行在 Windows CMD/PowerShell 环境，命令必须使用 Windows 原生语法（如 `dir` 而非 `ls`）。 |
| CM-005 | **异常处理** | `subprocess.TimeoutExpired` 返回超时提示；其他异常返回 `[ERROR] {异常信息}`。 |

---

## 9. 技能系统层

### 9.1 技能加载

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| SK-001 | **三层加载** | 第一层：加载技能元数据（名称、描述、触发词）；第二层：加载完整指令文件（SKILL.md）；第三层：加载资源文件（如有）。 |
| SK-002 | **缓存机制** | 已加载的技能指令缓存于内存，避免重复读取磁盘。 |
| SK-003 | **技能清单** | 系统提示词中包含 `## Installed Skills (L1 Metadata)` 章节，列出所有可用技能的名称、描述和触发词。 |

### 9.2 技能触发

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| SK-004 | **`<use_skill>` 触发** | LLM 输出 `<use_skill name="技能名"/>` 时，系统调用 `skill_loader.load_full_skill(name)` 加载完整技能指令。 |
| SK-005 | **成功加载** | 返回 `"已激活技能 '{name}'，完整指令如下：\n\n{full_content}"`，LLM 必须严格按照技能指令执行。 |
| SK-006 | **失败处理** | 技能不存在或无法加载时，返回 `[CRITICAL ERROR] 技能 '{name}' 不存在或无法加载。可用的技能列表：[...] 你必须立即输出 <done> 并报告错误，禁止继续执行任何其他工具。` |

### 9.3 系统提示词生成

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| SK-007 | **技能清单注入** | 系统提示词中自动包含所有已安装技能的 L1 元数据（名称、描述、触发词），供 LLM 决定何时调用 `<use_skill>`。 |
| SK-008 | **角色模板** | 系统提示词从 `config/role/{role}/` 目录加载，支持 `{role}_sys_prompt.md` 作为主体模板。 |

---

## 10. CLI 交互与显示层

### 10.1 终端显示

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| CL-001 | **Rich 终端 UI** | 使用 Rich 库实现终端渲染，包括格式化文本、面板、进度条、Markdown 渲染。 |
| CL-002 | **打字机效果** | LLM 响应以打字机效果逐字输出（通过 `Live` / `Panel` 动态刷新）。 |
| CL-003 | **Markdown 渲染** | 对 LLM 输出的 Markdown 内容进行终端适配渲染（标题、列表、代码块、加粗等）。 |
| CL-004 | **代码块切换** | 当检测到 LLM 输出包含 `def `、`import ` 等代码关键字时，切换为纯文本模式（避免 Markdown 渲染破坏代码格式）。 |
| CL-005 | **Thinking 指示器** | 在等待 LLM 回复期间显示"Thinking-N"闪烁动画，表示系统未死机。 |
| CL-006 | **工具执行提示** | 展示正在执行的工具名称、参数和结果（摘要形式）。 |

### 10.2 命令系统

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| CL-007 | **`/clear` 命令** | 清除当前会话的记忆。调用 `query_loop.clear_memory()`，返回清除条数并打印提示。 |
| CL-008 | **`/stats` 命令** | 展示当前会话的 token 统计（请求 + 响应）。 |
| CL-009 | **`/exit` 或 `/quit`** | 退出 CLI 程序。 |
| CL-010 | **命令分发** | 以 `/` 开头的输入视为 CLI 内部命令，解析后执行对应操作。不以 `/` 开头的输入视为用户任务，传递给 `QueryLoop.run()`。 |

### 10.3 会话元素

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| CL-011 | **记忆召回提示** | 每次任务开始时，打印 `[记忆召回] 已召回 N 条相关记忆`（即使 N=0 也打印）。 |
| CL-012 | **任务完成提示** | LLM 输出 `<done>` 后，展示完成信息。 |
| CL-013 | **强制终止提示** | 达到最大轮次限制时，展示"达到最大轮次限制 (N)，强制结束"。 |

---

## 11. 错误处理与恢复层

### 11.1 系统级错误处理

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| ER-001 | **未捕获异常不传播** | QueryLoop 主循环中的异常被捕获并记录日志，不导致程序崩溃。 |
| ER-002 | **配置缺失降级** | 配置项缺失时使用硬编码默认值（如 `max_turns=20`、`ttl_seconds=86400` 等）。 |
| ER-003 | **模块初始化降级** | 记忆模块、Embedding 模块初始化失败时降级为空实现或关键词匹配，系统继续运行。 |

### 11.2 工具级错误处理

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| ER-004 | **文件已存在** | `<create>` 目标文件存在时返回 `[BLOCKED]`，LLM 应改用 `<str_replace>` 或 `<file_view>`。 |
| ER-005 | **替换匹配失败** | `<str_replace>` 的 `<old>` 不匹配时返回 `[BLOCKED] 未找到匹配片段`，LLM 应先 `<file_view>` 再重试。 |
| ER-006 | **路径不存在** | `<file_view>` 目标路径不存在时返回 `错误：路径不存在`。 |
| ER-007 | **命令执行失败** | `<bash>` 命令返回非零退出码时，返回包含 stderr 的错误信息。 |
| ER-008 | **技能加载失败** | `<use_skill>` 技能名不存在时返回 `[CRITICAL ERROR]`，LLM 应立即输出 `<done>` 并停止。 |

### 11.3 日志恢复

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| ER-009 | **日志写入失败** | 日志文件写入失败时打印警告，不中断主流程。 |
| ER-010 | **记忆存储损坏恢复** | JSON 存储文件损坏时，自动从滚动备份文件（.bak1/.bak2/.bak3）恢复最新有效版本。 |

---

## 12. 系统测试用例规范

> **目标**：基于本规格文档生成的系统测试用例，必须能够被 `D:\AI\MyClaude\src\A2A_EX\system_test\` 自动化测试框架直接解析和执行，实现机器驱动的回归测试与新功能验证。

### 12.1 自动化对接原则

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| TC-001 | **机器可执行** | 每个测试用例必须包含一个可在沙箱中直接执行的 command（如 `python -m src.myclaude --test-mode --prompt "..." --test-output "..."` ），而非"在 CLI 中输入 xxx"、"观察 LLM 响应"等面向人工操作的描述。 |
| TC-002 | **结构化格式** | 测试用例必须采用 JSON 格式，字段固定、可被 `new_feature_runner.py` 直接解析，而非自由文本或 Markdown 表格。 |
| TC-003 | **可自动判定** | 每个测试用例必须包含明确的期望行为和可量化的通过标准（True/False 条件），供 `judge.py` 的 LLM 自动判定，而非"观察是否正常"等模糊描述。 |

### 12.2 系统测试用例必需字段

> **注意**：以下字段与 `src/A2A_EX/shared/models.py` 中的 `TestCase` Pydantic 模型严格对齐，确保测试用例能被 `new_feature_runner.py` 直接解析和执行。

| 字段名 | 类型 | 说明 |
|-------|------|------|
| `id` | string | 唯一标识符，如 `TC-QL-001`，格式为 `TC-{模块缩写}-{序号}` |
| `description` | string | 测试用例名称与简要描述 |
| `user_prompt` | string | 发送给 MyClaude 的自然语言测试指令 |
| `expected_behavior` | string | 期望行为描述，供 `judge.py` 的 LLM 评判。应包含明确的通过/失败判定标准（如输出应包含/不应包含的内容、应生成/修改的文件等） |
| `check_type` | string（可选） | 评判类型提示，默认 `"general"`。可选值：`file_created`（文件创建）、`file_modified`（文件修改）、`tool_chain`（工具链完整性）、`log_generated`（日志生成）、`startup`（启动检查）、`memory_aware`（记忆感知）、`skill_triggered`（技能触发）、`path_safety`（路径安全）、`general`（通用） |

### 12.3 系统测试执行流程

> **MyClaude 测试模式**：通过 `mycli.py` 的 `run_test_mode()` 方法执行。传入 `--test-output` 参数时，MyClaude 在执行过程中**保持原有屏幕输出和日志记录不变**，同时额外输出一个结构化 JSON 文件，供 A2A_EX 测试框架读取和评判。

#### 12.3.1 --test-output JSON 结构（供评判 LLM 使用）

MyClaude 通过 `--test-output <path>` 输出的 JSON 文件结构如下：

```json
{
  "exit_code": 0,
  "tool_calls": [
    {
      "tool": "create",
      "params": {"path": "D:/AI/MyClaude/code_output/hello.py", "summary": "创建hello函数"},
      "result": "文件已创建：D:/AI/MyClaude/code_output/hello.py，摘要：创建hello函数"
    },
    {
      "tool": "done",
      "params": {"message": "任务完成"},
      "result": ""
    }
  ],
  "key_outputs": [
    "我来为你创建一个 Python 函数 hello()...",
    "已完成"
  ],
  "is_truncated": false,
  "error": null
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `exit_code` | int | 退出码：0 = 正常完成，1 = 发生未捕获异常 |
| `tool_calls` | array | 工具调用序列，每个元素含 `tool`（工具名）、`params`（参数字典，路径为绝对路径）、`result`（工具执行结果的纯文本，截断 500 字符）。不含 Rich ANSI 转义码干扰 |
| `key_outputs` | array of string | **关键输出片段**：LLM 在各轮对话中输出的纯文本内容（`typewriter_then_markdown` 回调收到的文本）。这些是 LLM 的自然语言回答，**不包含** `create`、`str_replace` 等工具标签的完整 XML 内容（工具调用已在 `tool_calls` 中结构化记录）。用于评判 LLM 的"话术"是否符合预期（如是否出现 `[BLOCKED]`、`达到最大轮次限制` 等提示语） |
| `is_truncated` | bool | LLM 输出是否因 `max_tokens` 不足被截断 |
| `error` | string\|null | 异常信息，正常为 null |

#### 12.3.2 测试执行全流程

```
1. new_feature_runner.py 读取 JSON 测试用例文件
2. 通过 sandbox.run_myclaude_command_with_test_output() 启动 MyClaude：
   python -m src.myclaude --test-mode --prompt "..." --test-output "D:/tmp/test_xxx.json"
3. MyClaude run_test_mode() 执行：
   a. 保持原有屏幕输出和日志记录不变
   b. 同时收集结构化数据（tool_calls / key_outputs / exit_code）
   c. 执行完毕后写入 JSON 文件
4. new_feature_runner.py 读取 JSON 文件，调用 _build_actual_output() 合并为评判输入
5. LLMJudge.evaluate() 根据 expected_behavior 判定 PASS / FAIL / INCONCLUSIVE
```

### 12.4 禁止的测试用例写法（系统测试）

| 需求编号 | 禁止写法 | 原因 |
|---------|---------|------|
| TC-004 | "在 CLI 中输入..." | 面向人工操作，无法被 Runner 解析 |
| TC-005 | "观察 LLM 响应是否..." | 模糊的肉眼判断，无法自动判定 |
| TC-006 | "等待 LLM 输出..." | 无明确超时和判定条件 |
| TC-007 | "检查文件内容是否正确" | 无具体的字符串或正则匹配规则（但可通过 expected_behavior 中的描述 + LLM 自动评判） |
| TC-008 | 自由文本段落描述测试步骤 | 应使用结构化字段而非自然语言段落 |
| TC-013 | **依赖 LLM 以特定方式出错的期望行为** | 如"即使 LLM 输出的 <done> 标签省略了闭合标签 </done>..."。LLM 行为是概率性的，无法通过 prompt 可靠控制。此类需求应降级为单元测试，直接构造输入字符串测试解析器 |

### 12.5 系统测试用例 JSON 示例

> **注意**：以下示例与 `src/A2A_EX/shared/models.py` 中的 `TestCase` 模型完全对齐，可被 `new_feature_runner.py` 直接解析。
> **执行流程**：`new_feature_runner.py` 在沙箱中调用 `sandbox.run_myclaude_command_with_test_output(user_prompt=case.user_prompt)` 执行 MyClaude（自动附加 `--test-output` 生成结构化 JSON），读取 JSON 后调用 `_build_actual_output()` 合并工具调用和关键输出，再调用 `judge.evaluate(expected=case.expected_behavior, check_type=case.check_type, ...)` 由 LLM 根据 `expected_behavior` 描述自动评判是否通过。

```json
{
  "id": "TC-QL-001",
  "description": "编码模式下 LLM 输出 done 后正常退出",
  "user_prompt": "写一个 Python 函数 hello()，输出 'Hello, world!'",
  "expected_behavior": "MyClaude 应使用 <create> 工具创建文件，文件内容包含 'Hello, world!' 字符串，并且正常退出（输出 <done> 标签）。key_outputs 中不应出现 [ERROR] 或 '达到最大轮次限制' 等异常提示。exit_code 为 0。",
  "check_type": "tool_chain"
}
```

```json
{
  "id": "TC-FO-002",
  "description": "创建已存在文件时返回 BLOCKED 且不覆盖原内容",
  "user_prompt": "先在 D:/AI/MyClaude/code_output/ 目录下创建文件 blocked_test.py，内容为 print('v1')。创建完成后，再次创建同名文件 blocked_test.py，内容改为 print('v2')",
  "expected_behavior": "第一次 <create> 应成功。第二次 <create> 时，tool_calls 中应包含结果为 [BLOCKED] 的工具调用，拒绝覆盖已存在的文件。不应出现文件被覆盖为 print('v2') 的情况。",
  "check_type": "file_created"
}
```

```json
{
  "id": "TC-TP-010",
  "description": "简单创建文件任务能正常退出循环",
  "user_prompt": "在 D:/AI/MyClaude/code_output/ 目录下创建文件 done_test.py，内容为 x = 42，创建后立即结束任务",
  "expected_behavior": "MyClaude 应使用 <create> 工具创建文件 done_test.py，随后输出 <done> 标签退出循环。exit_code 为 0，tool_calls 中应包含 create 和 done 两个工具调用。key_outputs 中不应出现 '达到最大轮次限制' 或死循环迹象。",
  "check_type": "tool_chain"
}
```

### 12.6 系统测试用例生成规则

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| TC-009 | **一对一映射** | 本规格文档中每个需求编号（如 QL-001、FO-002）应至少对应一个自动化测试用例。 |
| TC-010 | **命令封装** | 对于需要多步交互的测试场景，应将交互逻辑封装为 Python 测试脚本（如通过 `subprocess` 或 SDK 调用 MyClaude），测试用例的 `input.command` 指向该脚本。 |
| TC-011 | **沙箱隔离** | 涉及文件创建/删除的测试用例应在 Docker 沙箱中执行，避免污染开发环境。 |
| TC-012 | **输出为 JSON 文件** | 生成的系统测试用例应保存为 `.json` 文件（单个或多个），存放于 `D:/AI/MyClaude/tests/` 目录下，文件名格式为 `system_test_cases_{序号}.json`。 |

---

## 13. 单元测试用例规范

> **目标**：对于不依赖完整 MyClaude 进程、LLM 网络调用、或依赖"LLM 以特定方式出错"的验证需求，生成纯 Python pytest 单元测试。单元测试可 100% 确定性地验证解析器、工具函数、路径处理等纯逻辑，不依赖 LLM 行为。

### 13.1 适用场景（必须生成单元测试的情况）

| 需求编号 | 适用场景 | 示例 |
|---------|---------|------|
| UT-001 | **LLM 出错行为验证** | 如"即使 LLM 输出了缺少闭合标签的 `<done>`，系统仍能正确识别"。LLM 的实际行为无法通过 prompt 可靠控制，必须通过单元测试直接构造输入字符串来验证解析器容错 |
| UT-002 | **纯算法/解析逻辑** | 如 `parse_tools()` 正则匹配、`strip_thinking()` 字符串处理、路径拼接逻辑 |
| UT-003 | **内部状态验证** | 如 `api_messages` 中无 `role=system` 消息、`is_chat_mode` 状态切换 |
| UT-004 | **错误处理路径** | 如文件不存在的返回值、JSON 损坏恢复——这些可通过 mock 或 fixture 直接触发 |

### 13.2 单元测试文件规范

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| UT-005 | **文件命名** | 单元测试文件存放于 `D:/AI/MyClaude/tests/` 目录下，文件名格式为 `test_{模块名}.py`（如 `test_tool_executor.py`、`test_file_tool.py`）。 |
| UT-006 | **函数命名** | 测试函数名格式为 `test_{需求编号的小写}_{简述}`（如 `test_tp_010_done_without_closing_tag`）。 |
| UT-007 | **独立性** | 每个单元测试不依赖 LLM API 调用、不依赖 Docker、不依赖完整 MyClaude 进程。所有输入通过参数直接构造。 |

### 13.3 单元测试用例 JSON 必需字段

**注意**：以下字段为单元测试用例 JSON 的描述规范，与系统测试（第 12.2 节）字段不同。单元测试 JSON 描述"测什么、怎么测"，实际执行由 pytest 或测试脚本完成，不走 A2A_EX 框架。

| 字段名 | 类型 | 说明 |
|-------|------|------|
| `id` | string | 唯一标识符，如 `UT-TP-010`，格式为 `UT-{模块缩写}-{序号}` |
| `description` | string | 测试用例名称与简要描述 |
| `target_module` | string | 被测模块的 Python 导入路径，如 `src.llm_tool.tool_executor` |
| `target_function` | string | 被测函数名，如 `parse_tools` |
| `test_input` | string | 传递给被测函数的主输入参数。**统一使用键值对格式** `'key1' : 'value1', 'key2' : 'value2'`，键名对应被测函数的参数名。例如：`parse_tools` 使用 `'response' : '...', 'reasoning_content' : '...'`；`file_create` 使用 `'root' : '...', 'path' : '...', 'content' : '...'`；`execute_code_tool` 使用 `'llm_tool' : 'create', 'path' : '...'`。格式详情见下方 **UT-009 强制规范**。严禁使用自然语言描述或 JSON 对象字符串。 |

| `expected_behavior` | string | 期望行为描述，供 pytest 测试代码参考。应包含明确的通过/失败判定标准（如返回的 tools_list 应包含哪些工具、remaining_text 应不含哪些标签等） |
| `check_type` | string（**必填**） | 评判类型提示。可选值：`file_created`（文件创建）、`file_modified`（文件修改）、`tool_chain`（工具链完整性）、`log_generated`（日志生成）、`startup`（启动检查）、`memory_aware`（记忆感知）、`skill_triggered`（技能触发）、`path_safety`（路径安全）、`general`（通用）。与 `judge.py` 的 `check_hints` 强关联。**严禁省略此字段，严禁填错——错误的 check_type 会导致 judge 使用不匹配的评判维度，造成误判。** |

⚠️ **UT-008 check_type 填写规则（Mandatory）—— 极其重要，必须逐字阅读**

`check_type` 的取值**必须**与具体的被测函数及其验证目标精确匹配，不可随意填写 `"general"`。

> 🔴 **致命警告**：`check_type` 不是装饰字段，它直接影响 `judge.py` 的评判维度和准确度。**填错的 check_type 等于错误的评判标准，导致测试用例失效**。你必须：
> 1. **先理解 `target_function` 的职责**——它在整个系统中负责什么？是解析 XML？是创建文件？还是校验路径？**这一步绝不能跳过**。
> 2. **再根据 `target_function` 的职责推导应填的 check_type**——例如：`parse_tools` → 工具解析 → `tool_chain`；`file_create` → 文件创建 → `file_created`；`file_str_replace` → 文件修改 → `file_modified`。**函数名本身已经提示了 check_type**。
> 3. **然后对照 `expected_behavior` 的验证重点做二次确认**——期望行为关注的是"文件是否被创建"还是"工具链是否完整"？如果与函数职责推导的结果一致，确认无误；如果不一致，以函数职责为准（因为 judge 是按 check_type 选择评判逻辑，不是按 expected_behavior 文本）。
> 4. **仅当函数职责与上表中所有专用 check_type 都不匹配时，才填 `"general"`**——这是最后的选择，不是默认选择。

> 🔴 **parse_tools 特别提醒（最高频误填）**：`parse_tools` 相关测试**极易被误填为 `"general"`**，因为"工具解析"听起来像通用逻辑。但 `parse_tools` 的本质是**工具链的入口和调度器**，其测试验证的是 XML 工具标签能否被正确识别、提取、组合成完整的工具调用链——这正是 `"tool_chain"` 的定义。**凡是 target_function 为 `parse_tools` 的测试，check_type 一律填 `"tool_chain"`，严禁填 `"general"`。**
>
> 同理，以下函数与 check_type 的映射关系也必须严格遵守：
> - `file_create` / `file_create_from_ai` → `file_created`
> - `file_str_replace` → `file_modified`
> - `execute_code_tool` → `tool_chain`
> - `get_context_for_query` / `add` / `search` → `memory_aware`
> - `load_full_skill` → `skill_triggered`
> - `tool_bash` → `general`（纯命令执行，如涉及路径安全则用 `path_safety`）
> - `strip_thinking` / `normal_utility` → `general`
> - 配置加载 / 初始化函数 → `startup`

填写前必须充分理解 `target_function` 的职责和 `expected_behavior` 的验证重点，然后从下表中选取最精确的取值：

| 验证目标 | 应填 `check_type` | 典型场景 |
|---------|------------------|---------|
| 验证文件是否被正确创建/内容 | `file_created` | `file_create()` 相关测试 |
| 验证文件内容是否被正确修改 | `file_modified` | `file_str_replace()` 相关测试 |
| 验证工具解析链路完整性 | `tool_chain` | `parse_tools()` 多工具组合解析 |
| 验证日志/输出内容 | `log_generated` | `SessionLog` 输出格式测试 |
| 验证模块启动/初始化 | `startup` | 配置加载、记忆模块初始化 |
| 验证记忆召回/注入 | `memory_aware` | `get_context_for_query()` 测试 |
| 验证技能加载/触发 | `skill_triggered` | `use_skill` 相关测试 |
| 验证路径安全/校验 | `path_safety` | 路径拦截、BLOCKED 测试 |
| 其他通用逻辑验证 | `general` | 字符串处理、状态切换等 |

**严禁行为**：不对被测函数做任何分析，直接填写 `"general"`。这属于测试用例缺陷。

⚠️ **UT-009 test_input 格式强制规范（Mandatory）—— 极其重要，必须逐字阅读**

`test_input` 不是自由格式的描述性文本。`unit_test_runner._build_args()` 对不同 `target_function` 有严格的不同解析逻辑。**格式化错误的 `test_input` 将导致 `KeyError`、`TypeError` 等崩溃**，使测试用例无法运行。

> 🔴 **致命警告**：`test_input` 必须使用 `_build_args` 明确的能够解析的格式，**严禁使用 JSON 对象字符串**（如 `{"path": "...", "content": "..."}`）作为 `test_input`。`_build_args` 不支持解析嵌套 JSON 字符串。你必须根据被测函数不同，采用对应的正确格式。

**各 target_function 的 test_input 正确格式**：

> **parse_tools**（src.llm_tool.tool_executor）：使用键值对格式，键名对应函数参数名 `(response, reasoning_content)`，`reasoning_content` 可省略（有默认值 `""`）。例如：
>   - 仅含 response：`"'response' : '...\n任务完成\n'"`
>   - 含 reasoning_content（用于测试 TP-011 兜底解析）：`"'response' : '根据需求，我将创建文件。', 'reasoning_content' : '用户要求创建文件...让我使用 create 工具。...'"`
>   - **严禁**使用 `{"llm_tool": "...", "params": {...}}` 等 JSON 格式。`parse_tools` 接收的是 LLM 原始文本，不是已解析的工具 dict。

> **execute_code_tool**（src.llm_tool.tool_executor）：`test_input` 是包含工具信息的**字符串**。`_build_args` 通过正则（`re.search(r'(file_view|create|str_replace|bash|done|use_skill)', test_input)`）提取工具名，并通过 `path='...'`、`summary='...'`、`name='...'`、`old='...'`、`new='...'` 等键值对提取参数。`content`/`body` 参数也通过类似键值对提取。例如：
>   - `"调用 execute_code_tool 执行 create 工具（path='D:/test.py', summary='测试', content='print(123)'）"`
>   - **严禁**使用 `{"llm_tool": "create", "params": {...}}` 等 JSON 对象字符串。`_build_args` 的 execute_code_tool 分支**不解析 JSON 对象字符串**，它只通过正则提取键值对和工具名。

> **file_create**（src.utility.file_tool）：使用键值对格式，键名对应函数参数名 `(root, path, content)`：
>   - 正确：`"'root' : 'D:/AI/MyClaude/code_output', 'path' : 'test_fo001.py', 'content' : 'x = 1'"`
>   - 错误：`"绝对路径 'D:/...' 和内容 'x = 1'"`（自然语言，`_build_args` 无法解析）

> **file_str_replace**（src.utility.file_tool）：键值对格式，键名对应参数名 `(root, path, old, new)`：
>   - `"'root' : 'D:/AI/MyClaude/code_output', 'path' : 'test.py', 'old' : 'x=1', 'new' : 'x=2'"`

> **file_view**（src.utility.file_tool）：键值对格式，键名对应参数名 `(root, path, limit, offset)`：
>   - `"'root' : 'D:/AI/MyClaude', 'path' : 'README.md'"`
>   - 可选参数：`"'root' : 'D:/AI/MyClaude', 'path' : 'AGENTS.md', 'limit' : 10, 'offset' : 5"`

> **resolve_path**（src.utility.file_tool）：键值对格式 `(root, path)`：
>   - `"'root' : 'D:/AI/MyClaude/code_output', 'path' : 'test.py'"`

> **tool_bash**（src.llm_tool.cmd_bash）：键值对格式，键 `command`：
>   - `"'command' : 'dir C:\\'"`

> **strip_thinking**（src.utility.normal_utility）：键值对格式，键 `content`：
>   - `"'content' : 'thinking...actual output'"`

> **stream_chat**（src.query.chat_llm）：键值对格式，键 `messages`：
>   - `"'messages' : 'hello'"`

> **其他函数**：统一使用键值对格式 `'key1' : 'value1', 'key2' : 'value2'`，键名对应函数参数名。`_build_args` 自动按函数签名映射为位置参数和关键字参数。

**错误示例（严禁）**：
```json
{
  "id": "UT-TP-012",
  "description": "execute_code_tool 执行 create 创建文件",
  "target_module": "src.llm_tool.tool_executor",
  "target_function": "execute_code_tool",
  "test_input": "{'llm_tool': 'create', 'params': {'path': 'D:/test.py'}}",
  "expected_behavior": "execute_code_tool 应创建文件 D:/test.py",
  "check_type": "tool_chain"
}
```
 ← 这种 JSON 对象字符串格式会导致 `KeyError: 'path'`，因为 `_build_args` 的 `execute_code_tool` 分支不解析 JSON 对象。

**正确示例**：
```json
{
  "id": "UT-TP-012",
  "description": "execute_code_tool 执行 create 工具创建测试文件",
  "target_module": "src.llm_tool.tool_executor",
  "target_function": "execute_code_tool",
  "test_input": "'llm_tool' : 'create', 'path' : 'D:/AI/MyClaude/code_output/test.py', 'summary' : 'test', 'content' : 'print(123)'",
  "expected_behavior": "execute_code_tool 应解析键值对参数，调用 file_create 在 D:/AI/MyClaude/code_output/ 下创建 test.py，内容为 'print(123)'。返回结果包含成功创建信息。",
  "check_type": "tool_chain"
}
```
 ← `_build_args` 通过键值对解析出 `llm_tool`、`path`、`summary`、`content`，构造 `tool_dict` 和参数。

⚠️ **UT-013 target_function 强制校验规则（Mandatory）—— 极其重要，必须逐字阅读**

`target_function` 必须是目标模块中**实际导出的函数或方法名**，一个字符都不能错。**填错函数名将导致 `AttributeError`，测试用例直接崩溃**。

> 🔴 **致命警告**：以下函数名在源码中不存在，必须使用右侧的正确名称：
>
| ❌ 错误名称（会崩溃） | ✅ 正确名称 | 说明 |
> |---------------------|------------|------|
> | `cmd_bash` | `tool_bash` | `src/llm_tool/cmd_bash.py` 中定义的是 `tool_bash()` |
> | `load_full_skill` | **不可作为 `target_function`** | `load_full_skill()` 是 `SkillLoader` 类的实例方法，不是模块级函数。`_build_args` 无法构造类实例。此类测试必须降级为手动编写的 pytest 文件，不能放入 JSON 单元测试用例中 |
> | 任何大小写不一致的名称 | 与源码完全一致的名称 | Python 区分大小写，`File_Create` ≠ `file_create` |

**生成测试用例前必须执行的校验步骤**：
1. 打开目标模块的源码文件（通过 ``）
2. 搜索 `def 目标函数名(` 确认函数确实存在
3. 如果是类方法（`def method(self, ...)`），该函数**不可作为 JSON 测试用例的 `target_function`**——必须降级为手动 pytest 文件
4. 确认无误后方可填写 `target_function`

⚠️ **UT-012 reasoning_input 强制检查规则（Mandatory）**

在生成单元测试用例 JSON 时，必须执行以下自检步骤：
1. 扫描所有 `target_function` 为 `parse_tools` 的用例
2. 检查每个用例的 `test_input` 字段——是否包含任何 XML 工具标签（`<create>`、`<str_replace>`、`<file_view>`、`<bash>`、`<done>`）
3. 完成以上检查后方可输出最终 JSON 文件

 **违反此规则的后果**：生成的测试用例将遗漏 `reasoning_content` 兜底解析场景，导致 TP-011 需求无法被覆盖，属于严重缺陷。

 **正确示例**：
 ```json
 {
   "id": "UT-TP-011",
   "description": "主响应无工具时从 reasoning_content 兜底提取工具调用",
   "target_module": "src.llm_tool.tool_executor",
   "target_function": "parse_tools",
   "test_input": "'response' : '根据需求，我将创建文件。', 'reasoning_content' : '用户要求创建文件...让我使用 create 工具。<create path='D:/AI/MyClaude/code_output/test.py' summary='测试文件'>print('hello')</create>'",   
   "expected_behavior": "...",
   "check_type": "tool_chain"
 }
> ```
 **错误示例（严禁）**：
 ```json
 {
   "id": "UT-TP-011",
   "description": "主响应无工具时从 reasoning_content 兜底提取工具调用",
   "target_module": "src.llm_tool.tool_executor",
   "target_function": "parse_tools",
   "test_input": "'response' : '根据需求，我将创建文件。', 'reasoning_content' : '用户要求创建文件...让我使用 create 工具。<create path='D:/AI/MyClaude/code_output/test.py' summary='测试文件'>print('hello')</create>'",
   "expected_behavior": "...",
   "check_type": "tool_chain"
}
 ```
 ← `reasoning_input` 缺失，导致测试用例无法验证兜底逻辑。

### 13.4 单元测试 JSON 示例（与系统测试格式分离）

> **注意**：以下示例遵循 13.3 节的字段规范，`test_input` 和 `reasoning_input` 字段分离。

```json
{
  "id": "UT-TP-010",
  "description": "done 标签省略闭合标签时 parse_tools 仍能正确识别",
  "target_module": "src.llm_tool.tool_executor",
  "target_function": "parse_tools",  
  "test_input": "'response' : '<create path='D:/test.py' summary='test'/>...\n<done>任务完成\n', 'reasoning_content' : ''",
  "expected_behavior": "parse_tools 应返回 tools_list 中包含 done 工具调用，remaining_text 不含 <done> 标签。不因缺少 </done> 闭合标签而抛出异常或死循环。",
  "check_type": "tool_chain"
}
```

```json
{
  "id": "UT-FO-001",
  "description": "file_create 在文件不存在时成功创建新文件",
  "target_module": "src.utility.file_tool",
  "target_function": "file_create",
  "test_input": "'root' : 'D:/AI/MyClaude/code_output', 'path' : 'test_fo001.py', 'content' : 'x = 1'",
  "expected_behavior": "file_create 应在 D:/AI/MyClaude/code_output/ 目录下创建 test_fo001.py 文件，文件内容为 'x = 1'。返回结果不应包含 [BLOCKED] 或 [ERROR]，应包含成功创建的信息。",
  "check_type": "file_created"
}
```

```json
{
  "id": "UT-TP-011",
  "description": "主响应无工具时从 reasoning_content 兜底提取工具调用",
  "target_module": "src.llm_tool.tool_executor",
  "target_function": "parse_tools",
  "test_input": "'response' : '根据需求，我将创建文件。', 'reasoning_content' : '用户要求创建文件...让我使用 create 工具。<create path='D:/AI/MyClaude/code_output/test.py' summary='测试文件'>print('hello')</create>'",
  "expected_behavior": "parse_tools 在主 content 中未解析到工具时，应从 reasoning_content 中兜底提取工具。返回的 tools_list 应包含一个 create 工具调用，remaining_text 为原 content 文本。不因主 content 无工具而返回空的 tools_list。",
  "check_type": "tool_chain"
}
```

### 13.5 系统测试与单元测试的分界线

| 验证目标 | 应生成的测试类型 | 原因 |
|---------|---------------|------|
| LLM 正常输出 `<create>` + `<done>` 完成文件创建 | 系统测试 | 需要 LLM 真正参与 |
| LLM 在文件已存在时输出 `[BLOCKED]` | 系统测试 | 需要 LLM 理解文件存在并调整行为 |
| LLM 使用 `dir` 而非 `ls` 执行命令 | 系统测试 | 需要 LLM 理解环境约束 |
| `parse_tools()` 能正确解析缺少 `</done>` 闭合的标签 | **单元测试** | 纯解析逻辑，LLM 输出不可控 |
| `file_create()` 对已存在文件返回 `[BLOCKED]` | **单元测试** | 纯函数逻辑，可直接控制输入 |
| `api_messages` 中无 `role=system` 消息 | **单元测试** | 内部状态验证，可通过 mock 控制 |
| `strip_thinking()` 正确移除思考标签 | **单元测试** | 纯字符串处理逻辑 |
| MyClaude 完整启动并加载配置 | 系统测试 | 需要完整的进程和配置环境 |

### 13.6 单元测试用例输出

| 需求编号 | 需求描述 | 行为规格 |
|---------|---------|---------|
| UT-010 | **输出为 JSON 文件** | 生成的单元测试用例描述保存为 `.json` 文件，存放于 `D:/AI/MyClaude/tests/` 目录下，文件名格式为 `unit_test_cases_{序号}.json`。同时生成对应的 `.py` pytest 测试文件。 |
| UT-011 | **与系统测试分离** | 系统测试 JSON（`system_test_cases_*.json`）和单元测试 JSON（`unit_test_cases_*.json`）分开存放和生成，互不混淆。类型通过文件名前缀区分。 |

---

> **文档结束**  
> 本规格文档覆盖了 MyClaude 系统的全部核心模块，共 11 个大类、约 120 条需求条目。每条需求均来源于源代码中的接口签名、分支逻辑、参数默认值、日志字符串和异常处理路径。基于本规格文档生成的测试用例分为两类：
> - **系统测试**（第 12 章）：通过 A2A_EX 框架在沙箱中启动完整 MyClaude 进程，由 LLM 自动评判。存放于 `D:/AI/MyClaude/tests/system_test_cases_*.json`
> - **单元测试**（第 13 章）：纯 Python pytest 测试，验证解析器、工具函数等纯逻辑。存放于 `D:/AI/MyClaude/tests/unit_test_cases_*.json` 和 `D:/AI/MyClaude/tests/test_*.py`
>
> 凡测试用例的"期望行为"依赖于"LLM 必须以某种特定方式出错"的，不属于系统测试范围，必须降级为单元测试。
