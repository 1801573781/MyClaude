# tree_cache 加载需求规格

## 1. 背景
项目在 `D:/AI/MyClaude/.tree_cache.md` 维护了一份项目目录树缓存。当前系统已支持加载系统提示词（`sys_prompt.md`）和项目上下文（`MyClaude.md`），但尚未加载 `.tree_cache.md`。本需求旨在将 `.tree_cache.md` 也纳入 `api_messages`，为 LLM 提供目录结构感知能力。

## 2. 功能需求

### FR-1: 加载 .tree_cache.md 内容
- 在 `llm_api_msg.py` 中增加对 `D:/AI/MyClaude/.tree_cache.md` 的读取逻辑。
- 如果文件不存在，静默跳过（不报错、不阻塞主流程）。

### FR-2: 加载时机
- 加载时机与系统提示词（`sys_prompt.md`）、项目上下文（`MyClaude.md`）完全一致。
- 仅加载一次，不在每轮对话中重复加载。

### FR-3: 加载顺序
- 具体插入位置：项目上下文之后、用户输入之前。
- 即 `api_messages` 中的顺序为：
  1. 系统提示词（`sys_prompt.md`）
  2. 项目上下文（`MyClaude.md`）
  3. **目录树缓存（`.tree_cache.md`）** ← 新增
  4. 用户输入

### FR-4: 消息格式
- 使用 `role="user"` 插入（遵循 MiniMax 严禁中间出现 `role="system"` 的约束）。
- 内容前缀加 `[项目目录树]` 标识，与现有的 `[项目上下文]` 前缀风格一致。

### FR-5: 代码修改范围
- 仅修改 `llm_api_msg.py` 中负责构建初始消息的函数。
- 无关代码严禁改动。

## 3. 非功能需求
- 不影响现有系统提示词和项目上下文的加载逻辑。
- `.tree_cache.md` 不存在时不报错，日志可选择性记录一条 warning。
- 单元测试覆盖：文件存在时正确加载、文件不存在时静默跳过。

## 4. 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `D:/AI/MyClaude/src/message/llm_api_msg.py` | 修改 | 在消息构建函数中插入 tree_cache 加载逻辑 |
| `D:/AI/MyClaude/.tree_cache.md` | 读取 | 已存在，作为数据源 |
| `D:/AI/MyClaude/code_output/test_tree_cache_loader.py` | 新建 | 单元测试（临时输出目录，因为是测试代码） |

## 5. 验收标准
- [ ] `.tree_cache.md` 存在时，其内容出现在 `api_messages` 中，位于项目上下文之后。
- [ ] `.tree_cache.md` 不存在时，`api_messages` 正常构建，无报错。
- [ ] 单元测试通过。