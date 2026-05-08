---
name: add_tests
description: 为 Python 函数/模块生成 pytest 单元测试。触发词：加测试、写测试、生成测试、pytest、unit test、test、补测试。自动分析源码，创建 test_*.py，运行并修正测试代码。
---

# Skill: add_tests

## 1. 目标
为 MyClaude 项目中的指定 Python 函数、类或模块生成 **pytest** 单元测试，要求：
- 遵循 Arrange‑Act‑Assert 结构
- 覆盖正常路径、边界条件、异常输入
- 测试文件放在被测代码**同级目录**，命名为 `test_*.py`
- 创建后自动运行 `pytest`，并根据失败结果**修复测试代码**（不修改被测源码）

## 2. 触发条件（自动匹配）
当用户输入包含以下任一关键词时，**必须激活此 Skill**，并严格按下列流程执行：
- “加测试”、“写测试”、“生成测试”、“补测试”
- “test”、“unit test”、“pytest”
- “覆盖率”、“coverage”、“uncovered”

## 3. 路径规范（来自 MyClaude.md）
- **正式源码根目录**：`D:/AI/MyClaude/src/`
- **临时/探索性代码**：`D:/AI/MyClaude/code_output/`（优先适用规则见 MyClaude.md）
- **需求文档根目录**：`D:/AI/MyClaude/spec/`
- **所有文件操作必须使用绝对路径**，严禁相对路径或裸文件名。

## 4. 执行流程（分7轮，严禁跳过任何步骤）

### 第 1 轮：定位目标文件
- 如果用户只提供了文件名（例如 `math.py`），首先调用：
  <file_view path="D:/AI/MyClaude/src/"/>

### 第 2 轮：查看目标源码（必须，严禁编码）
- 调用 `file_view` 读取被测文件的完整内容：
  <file_view path="D:/AI/MyClaude/src/{子目录}/{目标文件名}.py"/>

### 第 3 轮：检查是否已有测试文件
- 查看同级目录：
  <file_view path="D:/AI/MyClaude/src/{子目录}/"/>

### 第 4 轮：生成测试文件（使用 `<create>`）
- 测试文件路径：`D:/AI/MyClaude/src/{子目录}/test_{目标文件名}.py`
- 内容模板（示例，必须根据实际函数适配）：
  <create path="D:/AI/MyClaude/src/{子目录}/test_{目标文件名}.py">
import pytest
from {模块名} import {函数名或类名}

def test_{函数名}_normal():
    # Arrange
    ...
    # Act
    result = {函数名}(...)
    # Assert
    assert result == expected

def test_{函数名}_edge_case():
    ...

def test_{函数名}_type_error():
    with pytest.raises(TypeError):
        {函数名}(None)
  </create>

### 第 5 轮：运行测试
- 执行命令：
  <bash>python -m pytest D:/AI/MyClaude/src/{子目录}/test_{目标文件名}.py -v</bash>

### 第 6 轮：分析测试结果并修正
根据 `pytest` 输出，采取不同行动（**严禁直接 `<done>`**）：

| pytest 结果 | 原因 | 下一步行动 |
|-------------|------|-------------|
| `SyntaxError` / `ImportError` | 测试代码有语法或导入错误 | 使用 `<str_replace>` 修正测试文件（基于已 `file_view` 的内容），然后回到第5轮 |
| `AssertionError` | 断言失败 | ⚠️ 区分两种子情况：<br> • **测试逻辑错误**（如浮点精度未用 `approx`）→ 修正测试文件，回到第5轮<br> • **被测代码确实有 bug** → 测试已正确暴露问题，进入第7轮（不修改被测代码） |
| `PASSED` | 全部通过 | 进入第7轮 |

### 第 7 轮：输出 `<done>`
只有确认所有测试通过，或被测代码 bug 已被测试正确暴露（无需修复），才输出：
<done>已为 {函数名} 生成单元测试，pytest 全部通过（或：测试正确暴露了 {bug描述}，请开发者修复源码）</done>

## 5. 故障恢复与特殊规则

### 5.1 创建文件被阻止
若 `<create>` 因文件已存在而返回 `[BLOCKED]`：
1. 立即 `<file_view>` 查看现有测试文件内容
2. 使用 `<str_replace>` 追加或修改测试用例
3. 严禁再次 `<create>` 覆盖

### 5.2 测试文件导入被测模块失败
- 检查被测模块是否在 `src/` 的正确子目录中
- 确保测试文件中的 `from {模块名} import ...` 使用了正确的 Python 包路径（例如 `from utils.math import add`）
- 若路径错误，修正 `<str_replace>` 中的 import 语句

### 5.3 禁止事项
- **严禁修改被测源码**（即使测试失败，也只用 `<done>` 报告问题）
- **严禁在未运行 pytest 之前输出 `<done>`**
- **严禁生成依赖外部环境（数据库、网络 API）的测试**（使用 mock 或跳过）
- **严禁在一个测试函数中断言多个无关逻辑**

## 6. 与 MyClaude 工具链的集成
- 本 Skill 完全基于现有工具：`<file_view>`, `<create>`, `<str_replace>`, `<bash>`, `<done>`
- 所有路径均使用**绝对路径**，符合 `MyClaude.md` 强制规范
- 测试运行依赖项目根目录的 `pytest.ini`（已配置 `testpaths = src code_output`）

## 7. 示例（完整7轮交互）

**用户输入：**
加测试：multiply_by_2

**第 1 轮：**
<file_view path="D:/AI/MyClaude/src/"/>

**系统返回目录结构，发现 `utils/math.py`**

**第 2 轮：**
<file_view path="D:/AI/MyClaude/src/utils/math.py"/>

**系统返回源码（包含 `def multiply_by_2(x): return x * 2`）**

**第 3 轮：**
<file_view path="D:/AI/MyClaude/src/utils/"/>

**系统返回（无测试文件）**

**第 4 轮：**
<create path="D:/AI/MyClaude/src/utils/test_math.py">
import pytest
from math import multiply_by_2

def test_multiply_by_2_positive():
    assert multiply_by_2(3) == 6

def test_multiply_by_2_zero():
    assert multiply_by_2(0) == 0

def test_multiply_by_2_negative():
    assert multiply_by_2(-5) == -10
</create>

**系统返回“已创建...”**

**第 5 轮：**
<bash>python -m pytest D:/AI/MyClaude/src/utils/test_math.py -v</bash>

**系统返回 `3 passed`**

**第 6 轮（分析结果）：**
根据 pytest 输出，全部通过，无需修正。

**第 7 轮：**
<done>已为 multiply_by_2 生成测试用例，pytest 全部通过</done>