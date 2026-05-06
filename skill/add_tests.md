# Skill: add_tests

## 触发条件
用户输入包含以下关键词之一：
- "加测试"、"写测试"、"补测试"、"生成测试"
- "test"、"unit test"、"pytest"
- "覆盖率"、"coverage"、"uncovered"

## 目标
为指定的 Python 函数/类/模块生成单元测试，使用 pytest 框架，遵循 Arrange-Act-Assert 结构。

## 路径规范（来自 MyClaude.md 上下文）
- 正式模块代码根目录：`D:/AI/MyClaude/src/`
- 临时测试代码目录：`D:/AI/MyClaude/code_output/`
- **严禁假设源码在固定子目录**。如果用户只提供文件名，必须先通过 `<code_view path="D:/AI/MyClaude/src/"/>` 查看目录结构定位真实路径。

## 前置检查（必须先做）
1. **定位文件**：如果用户只给文件名（如 `math.py`），先 `<code_view path="D:/AI/MyClaude/src/"/>` 查看目录结构，找到真实子目录（如 `src/utils/math.py`）。严禁假设在 `utils/` 或其他固定目录。
2. **查看源码**：用 `<code_view>` 查看目标源码，理解函数签名、输入输出类型、边界条件。
3. **查看现有测试**：用 `<code_view>` 查看目标文件同级目录是否已有 `test_*.py` 或 `*_test.py`。
4. **查看配置**：如果项目根目录有 `pytest.ini` 或 `pyproject.toml`，查看测试配置。

## 执行流程（展示完整分轮过程）

### 第一轮：定位文件并查看源码（严禁编码，严禁 done）
用户提到 `{目标文件名}.py`，未给子目录。先定位：
<code_view path="D:/AI/MyClaude/src/"/>

[系统返回目录结构，发现文件在 `src/{子目录}/{目标文件名}.py`]

再查看源码：
<code_view path="D:/AI/MyClaude/src/{子目录}/{目标文件名}.py"/>

[系统返回源码内容]

### 第二轮：查看同级目录现有测试（严禁 done）
<code_view path="D:/AI/MyClaude/src/{子目录}/"/>

[系统返回目录列表，确认无现有测试文件]

### 第三轮：创建测试文件（严禁 done）
<create path="D:/AI/MyClaude/src/{子目录}/test_{目标文件名}.py">
import pytest
from {目标文件名} import {目标函数名}

def test_{目标函数名}_normal():
    # Arrange
    ...
    # Act
    ...
    # Assert
    ...

def test_{目标函数名}_negative():
    ...

def test_{目标函数名}_float():
    ...

def test_{目标函数名}_type_error():
    with pytest.raises(TypeError):
        ...
</create>

[系统返回创建结果]

### 第四轮：运行测试（严禁 done）
<bash>python -m pytest D:/AI/MyClaude/src/{子目录}/test_{目标文件名}.py -v</bash>

[系统返回测试结果]

### 第五轮：根据 pytest 结果判断下一步（严禁盲目 done）

**情况 A：pytest 报告测试文件本身有错误（SyntaxError / ImportError / IndentationError）**
→ 测试代码写坏了，回到第三轮用 `<str_replace>` 修复测试文件。严禁直接 `<done>`。

**情况 B：pytest 报告断言失败（AssertionError）**
→ 先判断原因：
   - 若是**测试逻辑错误**（如浮点精度未用 `approx`、边界值写错）：回到第三轮修复测试文件。
   - 若是**被测代码功能错误**（如 `add(1,1)` 返回 3）：测试已正确暴露问题，任务完成，输出 `<done>`（用户只要求"加测试"，不要求修 bug）。

**情况 C：pytest 报告全部 passed**
→ 测试代码正确，任务完成：
<done>已为 {目标函数名} 生成测试用例，pytest 全部通过</done>

## 通用规则提炼
- 每轮只输出**一个**执行类工具，严禁同轮混用 `<done>`
- 严禁假设源码在固定子目录，必须通过 `code_view` 定位
- 创建测试前必须先 `code_view` 源码，严禁凭记忆构造测试
- 测试创建后必须 `bash` 运行 pytest，严禁未验证直接 `<done>`
- 区分"测试代码错误"（需修复测试）和"被测代码未通过测试"（测试正确，可结束）
- **示例中的 `{目标文件名}`、`{目标函数名}`、`{子目录}` 均为占位符，必须从用户输入中提取真实值，严禁照搬示例中的虚构名字**

## 禁忌
- 严禁修改被测源码来"迁就"测试。
- 严禁生成需要真实外部环境的测试（如连接数据库、调用真实 API）。
- 严禁在一个测试函数里断言多个无关逻辑。
- 严禁使用相对路径导入被测模块。

## 输出格式模板
```python
def test_{函数名}_{场景描述}():
    # Arrange
    ...
    # Act
    ...
    # Assert
    ...
