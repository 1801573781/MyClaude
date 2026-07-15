"""
AskUserQuestion 工具核心实现。

允许 LLM 在任务执行过程中主动向用户提出问题，获取人类反馈后继续推进任务。
本质上是 Human-in-the-loop（人机回环）机制的核心基础设施。
"""

from __future__ import annotations

from src.cli import cli_print


def ask_user_question(question: str, choices: list[str] | None = None) -> dict:
    """
    向用户提问并获取回答。

    Args:
        question: LLM 生成的问题文本
        choices: 可选的预设选项列表。为 None 或空列表时表示 open-ended（自由文本输入）。

    Returns:
        dict: {"role": "user", "content": "[USER_ANSWER] 用户输入的回答内容"}
    """
    # 委托 CLI 层渲染问题并获取用户原始输入
    raw_input = cli_print.print_ask_user_question(question, choices)

    # 编号映射：如果提供了 choices 且用户输入的是有效编号，映射为对应选项文本
    user_answer = raw_input

    if choices:
        stripped = raw_input.strip()
        if stripped.isdigit():
            idx = int(stripped)
            if 1 <= idx <= len(choices):
                user_answer = choices[idx - 1]

    # 空输入处理
    if not user_answer.strip():
        user_answer = "（用户未提供输入）"

    # 包装为标准返回格式，严格遵守项目架构契约
    return {
        "role": "user",
        "content": f"[USER_ANSWER] {user_answer}",
    }
