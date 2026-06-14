# 单元测试用例生成器需求规格

## 1. 概述

### 1.1 目标
开发一个单元测试用例自动生成工具 `unit_test_generator_ex.py`，能够对指定的 Python 项目递归分析其模块、函数结构，结合 LLM 能力，自动生成结构化的单元测试用例（JSON 格式）。

### 1.2 输出物
- **需求规格文档**：`D:\AI\MyClaude\spec\unit_test_case_generator_spec.md`（本文档）
- **目标代码文件**：`D:\AI\MyClaude\src\tools\unit_test_generator_ex.py`

---

## 2. 运行方式

### 2.1 独立运行
`unit_test_generator_ex.py` 可以作为独立脚本运行，无需依赖 MyClaude 主循环。

### 2.2 命令行参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--root` | Python 项目的根目录（绝对路径） | `--root D:/AI/MyClaude` |
| `--output` | 输出测试用例文件的名称（绝对路径） | `--output D:/AI/MyClaude/code_output/test_cases.json` |

### 2.3 运行示例
```bash
python -m src.tools.unit_test_generator_ex --root D:/AI/MyClaude --output D:/AI/MyClaude/code_output/test_cases.json
```

---

## 3. 核心功能：递归遍历与分析

### 3.1 遍历规则
1. 从 `--root` 指定的根目录开始，递归遍历所有子目录和 `.py` 文件。
2. 读取根目录下的 `.gitignore` 文件，忽略其中指定的文件和目录。
3. 只分析以下类型的函数：
   - **全局函数**（模块级函数/独立函数）
   - **类中的静态方法**（`@staticmethod`）

### 3.2 Jedi 集成（优先级最高）
所有函数提取、参数分析、静态方法识别**必须优先使用 Jedi 库**的能力：
- 使用 `jedi.Script()` 或 `jedi.names()` 解析每个 `.py` 文件。
- 通过 Jedi 获取函数名、参数列表（参数名、默认值）、所属模块路径、是否为静态方法。
- 只有在 Jedi 无法处理的情况下（如 Jedi 无法解析的语法结构），才使用自定义解析逻辑作为兜底。

### 3.3 目标函数识别
- `target_module`：函数的 Python 导入路径，如 `src.utility.file_tool`。
- `target_function`：函数名，如 `file_create`。

---

## 4. 单元测试用例格式

### 4.1 输出格式
输出文件为 **JSON 数组**，每个元素是一个测试用例对象，不含任何多余描述文字。

### 4.2 用例字段

| 字段名 | 类型 | 说明 |
|-------|------|------|
| `id` | string | 唯一标识符，格式 `UT-{模块缩写}-{序号}`，如 `UT-FO-010` |
| `description` | string | 测试用例名称与简要描述 |
| `target_module` | string | 被测模块的 Python 导入路径，如 `src.utility.file_tool` |
| `target_function` | string | 被测函数名，如 `file_create` |
| `test_input` | string | 传递给被测函数的输入参数，使用键值对格式 |
| `expected_behavior` | string | 期望行为描述，包含通过/失败判定标准 |

### 4.3 `test_input` 格式规范（强制）
- 使用键值对格式：`'key1' : 'value1', 'key2' : 'value2'`
- 键名为被测函数的参数名。
- 严禁使用自然语言描述或 JSON 对象字符串。

### 4.4 用例示例
```json
{
    "id": "UT-FO-002",
    "description": "file_create 在目标文件已存在且非空时返回 BLOCKED",
    "target_module": "src.utility.file_tool",
    "target_function": "file_create",
    "test_input": "'root' : 'D:/AI/MyClaude/code_output', 'path' : 'existing.py', 'content' : 'new content'",
    "expected_behavior": "file_create 检测到 existing.py 已存在且非空，返回包含 [BLOCKED] 的警告信息，并拒绝覆盖。原始文件内容保持不变。"
}
```

---

## 5. 生成策略：代码与 LLM 分工

### 5.1 由代码直接生成（不调用 LLM）
以下字段由 `unit_test_generator_ex.py` 自身逻辑生成：
- `id`：根据模块缩写和序号自动编号。
- `target_module`：从文件路径和 Jedi 分析结果推导。
- `target_function`：从 Jedi 分析结果获取。
- `test_input` 中的 **key**（参数名）：从 Jedi 获取的参数列表。

### 5.2 由 LLM 生成
以下内容通过调用 LLM 生成：
- `test_input` 中的 **value**（参数值）：
  - 正常值（合法输入）
  - 边界值（临界输入）
  - 异常值（非法输入、类型错误、None、空字符串等）
- `description`：测试用例的名称与描述。
- `expected_behavior`：期望行为，必须与 `test_input` 的 value 严格对应。

### 5.3 LLM 调用策略
采用 **批量调用** 方式，将同一模块或同一文件的所有待测试函数一次性提交给 LLM，一次性生成所有用例的 value、description、expected_behavior，减少 API 调用次数，提高效率。

---

## 6. LLM 调用规范

### 6.1 配置参数
LLM 调用参数从全局配置中读取：
```python
model_provider = global_cfg.model.provider
provider_cfg = getattr(global_cfg, model_provider)
api_key = provider_cfg.api_key
base_url = provider_cfg.base_url
model_name = provider_cfg.model_name
```

### 6.2 Prompt 设计要点
Prompt 必须引导 LLM 做到以下几点：
1. **理解被测函数**：提供函数签名（参数名、类型提示、默认值）、函数文档字符串、函数所在模块的上下文。
2. **生成多样化的输入值**：为每个参数生成以下类型的值：
   - 正常值：符合函数预期的合法输入。
   - 边界值：如空字符串、零、空列表、None、极大值、极小值等。
   - 异常值：类型不匹配的值、超出范围的值、不存在的路径等。
3. **严格一致的 expected_behavior**：`expected_behavior` 必须精确反映给定 `test_input` 下函数的预期行为，包括返回值、抛出的异常、副作用等。如果 `expected_behavior` 写错了，整个测试用例将失去意义。

---

## 7. 兜底检查与纠错

### 7.1 检查时机
在 LLM 生成测试用例后，`unit_test_generator_ex.py` 必须对所有用例进行兜底检查。

### 7.2 检查项目

| 检查项 | 说明 |
|-------|------|
| 格式完整性 | 每个用例必须包含所有必填字段（id, description, target_module, target_function, test_input, expected_behavior）。 |
| test_input 格式 | `test_input` 必须是键值对格式 `'key' : 'value', ...`，严禁出现自然语言或 JSON 对象。 |
| test_input 键完整性 | `test_input` 中的键必须与被测函数的参数列表完全匹配，不允许缺失或多余。 |
| test_input 值类型 | 值的数据类型应与函数参数的类型提示（如果存在）兼容。如果参数有类型提示 `int`，值不能是字符串 `"hello"`。 |
| id 唯一性 | 所有用例的 `id` 不能重复。 |
| 非空检查 | `description` 和 `expected_behavior` 不能为空。 |

### 7.3 错误处理流程
1. 如果 LLM 生成的用例存在格式或类型错误，收集所有错误信息。
2. 将错误信息反馈给 LLM，要求 LLM 重新生成有问题的用例。
3. 重试最多 3 次。如果 3 次后仍有问题，将该用例标记为最终错误。

### 7.4 错误输出
所有最终未能修复的错误用例，单独输出到一个错误文件中：
- 错误文件路径：`{output文件名}_errors.json`
- 格式：JSON 数组，每个元素包含原始用例和对应的错误描述。

```json
[
    {
        "original_case": { ... },
        "errors": ["test_input 缺少参数 'path'", "expected_behavior 为空"]
    }
]
```

---

## 8. 输出文件结构

### 8.1 主输出文件
`--output` 指定的路径，内容为 JSON 数组，仅包含**通过兜底检查**的测试用例。

### 8.2 错误文件
与主输出文件同名，但后缀改为 `_errors.json`，仅包含**无法修复的错误用例**。
