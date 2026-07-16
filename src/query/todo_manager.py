"""TodoWrite 有状态进度管理工具的核心实现。

包含 TodoItem / TodoList 数据模型和 TodoManager 生命周期管理器。
由 QueryLoop 持有，负责解析 <todowrite> XML、维护 todo 状态、
生成注入上下文的 [TODO_STATUS] 消息。
"""

import re
from dataclasses import dataclass, field
from enum import Enum


class TodoStatus(Enum):
    """Todo 条目的三种状态。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class TodoItem:
    """单个 Todo 条目。

    Attributes:
        content:     任务描述，如 "创建 proposal.md"
        status:      状态枚举
        active_form: 进行中时的动作描述（可选，仅 in_progress 时有意义）
    """

    content: str
    status: TodoStatus = TodoStatus.PENDING
    active_form: str = ""


@dataclass
class TodoList:
    """Todo 条目列表，支持整体替换语义。"""

    items: list[TodoItem] = field(default_factory=list)

    def is_empty(self) -> bool:
        """是否为空列表。"""
        return len(self.items) == 0

    def all_completed(self) -> bool:
        """是否所有条目都已完成。"""
        return bool(self.items) and all(
            item.status == TodoStatus.COMPLETED for item in self.items
        )

    def current_in_progress(self) -> TodoItem | None:
        """返回当前处于 in_progress 的条目（如有）。"""
        for item in self.items:
            if item.status == TodoStatus.IN_PROGRESS:
                return item
        return None

    def completed_count(self) -> int:
        """已完成的条目数。"""
        return sum(
            1 for item in self.items if item.status == TodoStatus.COMPLETED
        )

    def status_signature(self) -> str:
        """生成状态摘要签名，用于死循环熔断判断。

        返回形如 "cip" 的字符串，c=completed, i=in_progress, p=pending。
        如果两轮签名一致，说明 todo 状态没有实质变化。
        """
        mapping = {
            TodoStatus.COMPLETED: "c",
            TodoStatus.IN_PROGRESS: "i",
            TodoStatus.PENDING: "p",
        }
        return "".join(mapping[item.status] for item in self.items)


class TodoManager:
    """管理 TodoList 的生命周期，由 QueryLoop 持有。

    职责：
        1. 解析 <todowrite> XML，整体替换 TodoList
        2. 生成注入 api_messages 的 [TODO_STATUS] 消息
        3. 在 <done> 或新对话时 reset
        4. 提供展示数据给 CLI 回调
    """

    # 匹配 <todowrite> 块内的每个 <todo> 子块
    _TODO_BLOCK_RE = re.compile(
        r"<todo>\s*(?:<content>(.*?)</content>)?"
        r"\s*(?:<status>(.*?)</status>)?"
        r"\s*(?:<active_form>(.*?)</active_form>)?\s*</todo>",
        re.DOTALL,
    )

    def __init__(self):
        self._todo_list: TodoList = TodoList()

    @property
    def todo_list(self) -> TodoList:
        """当前持有的 TodoList（只读访问）。"""
        return self._todo_list

    def update_from_xml(self, xml_content: str) -> dict:
        """从 <todowrite> XML 内容更新 todo 列表（整体替换）。

        Args:
            xml_content: <todowrite> 标签内部的完整 XML 文本

        Returns:
            {"role": "user", "content": "[TODO_UPDATED] ..."} 格式的消息，
            用于追加到 api_messages。
        """
        items: list[TodoItem] = []

        for match in self._TODO_BLOCK_RE.finditer(xml_content):
            raw_content = (match.group(1) or "").strip()
            raw_status = (match.group(2) or "pending").strip().lower()
            raw_active_form = (match.group(3) or "").strip()

            if not raw_content:
                continue

            try:
                status = TodoStatus(raw_status)
            except ValueError:
                status = TodoStatus.PENDING

            active_form = raw_active_form if status == TodoStatus.IN_PROGRESS else ""

            items.append(
                TodoItem(
                    content=raw_content,
                    status=status,
                    active_form=active_form,
                )
            )

        self._todo_list = TodoList(items=items)

        total = len(self._todo_list.items)
        in_prog = sum(
            1 for i in self._todo_list.items if i.status == TodoStatus.IN_PROGRESS
        )
        completed = self._todo_list.completed_count()

        return {
            "role": "user",
            "content": (
                f"[TODO_UPDATED] {total} tasks, "
                f"{completed} completed, {in_prog} in_progress."
            ),
        }

    def get_context_message(self) -> str | None:
        """生成注入到 api_messages 的 [TODO_STATUS] 消息。

        Returns:
            格式化的状态字符串，或 None（列表为空时）。
        """
        if self._todo_list.is_empty():
            return None

        total = len(self._todo_list.items)
        completed = self._todo_list.completed_count()
        lines = [f"[TODO_STATUS] 当前进度: {completed}/{total}"]

        for item in self._todo_list.items:
            if item.status == TodoStatus.COMPLETED:
                lines.append(f"  ✅ {item.content} (completed)")
            elif item.status == TodoStatus.IN_PROGRESS:
                suffix = f" — {item.active_form}" if item.active_form else ""
                lines.append(f"  ▶ {item.content} (in_progress){suffix}")
            else:
                lines.append(f"  ○ {item.content} (pending)")

        return "\n".join(lines)

    def reset(self):
        """重置 todo 列表（<done> 后或新对话时调用）。"""
        self._todo_list = TodoList()

    def get_display_data(self) -> TodoList:
        """返回当前 todo 列表供 CLI 渲染。"""
        return self._todo_list

    def has_todo(self) -> bool:
        """当前是否持有非空 todo 列表。"""
        return not self._todo_list.is_empty()
