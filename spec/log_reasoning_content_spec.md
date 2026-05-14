# 会话日志记录推理内容（Reasoning Content）需求规格

这是一篇需求规格文档，你所要做的是，根据这篇文档，生成相关代码。你自己的思考过程不要受此文档迷惑。

## 1. 概述
在 MyClaude 的会话日志（Session Log）中，LLM 返回的 `reasoning_content`（推理/思考过程）内容目前并未被记录。需要在会话日志中完整记录每次 LLM 请求返回的推理内容，以便后续分析和调试。

## 2. 背景
- 当前 `chat_llm.py` 的 `stream_chat()` 方法在流式读取时，已经能够从 `delta` 中获取 `reasoning_content`（如果提供商返回）。
- 但现有的 `session_log.py` 和 `query_loop.py` 并未将 `reasoning_content` 传递给日志记录模块。
- `normal_utility.py` 中的 `strip_thinking()` 函数只负责从文本中剥离思考内容，但不记录原始推理内容。

## 3. 功能需求

### 3.1 chat_llm.py 修改
- `stream_chat()` 方法当前返回 `(content: str, is_truncated: bool)`。
- 需要修改为返回 `(content: str, is_truncated: bool, reasoning_content: str)`。
- 在流式读取过程中，收集所有 `delta.reasoning_content` 片段，拼接成完整字符串。
- `_chat_with_retry()` 也需要同步修改，将 `reasoning_content` 透传出去。

### 3.2 query_loop.py 修改
- 在 `_single_turn()` 方法中，接收 `chat_llm` 返回的 `reasoning_content`。
- 将 `reasoning_content` 传递给 `session_log` 的记录方法。

### 3.3 session_log.py 修改
- 在每轮日志记录中，新增 `reasoning_content` 字段。
- 如果有推理内容，则在日志中以折叠块（`<details>`）形式记录，便于阅读。
- Markdown 格式示例：
```markdown
### 轮次 1 — 2026-01-15 10:30:00

**推理内容**：
<details>
<summary>展开查看推理过程</summary>

（此处为推理内容原文）

</details>

**LLM 输出**：
...
```

- HTML 格式对应使用 `<details><summary>` 标签。
- 如果 `reasoning_content` 为空字符串，则不输出推理内容区块。

### 3.4 兼容性要求
- 对于不支持 `reasoning_content` 的提供商（如基础版 OpenAI），返回空字符串即可。
- 下游代码（`query_loop`、`session_log`）需对空字符串做判断，避免输出空的折叠块。

## 4. 涉及文件

| 文件 | 修改内容 |
|------|---------|
| `src/query/chat_llm.py` | `stream_chat()` 返回值增加 `reasoning_content` |
| `src/query/query_loop.py` | 接收并传递 `reasoning_content` |
| `src/query/session_log.py` | 记录推理内容为折叠块 |

## 5. 非功能需求
- 不增加额外网络请求。
- 不改变现有日志的主体结构。
- 推理内容可能很长，日志文件体积会增大，但不做截断处理（保留完整推理过程）。

## 6. 验收标准
1. 使用支持 reasoning_content 的模型对话后，日志中出现 `<details>` 折叠块及完整推理内容。
2. 使用不支持 reasoning_content 的模型时，日志中不出现空的推理内容区块。
3. 对于修改修改的代码，构建单元测试，并且测试通过。