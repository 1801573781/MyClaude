"""Bug库系统（Bug Base）— Bug 记录与召回子系统。

与 Memory 系统并行运行，核心区别：
- 内容：具体的 Bug 记录，而非泛化知识
- 召回方式：两级召回（路径确定性匹配 + LLM 语义精筛）
- 召回时机：工具执行前，根据即将修改的文件路径
- 准入门槛：无门槛，每个 Bug 都进入
- 生命周期：代码变更后标记为已修复，归档
"""

from .bug_store import BugRecord, BugStore
from .bug_extractor import BugExtractor
from .bug_retriever import BugRetriever
from .bug_injector import BugInjector
from .bug_base import BugBase

__all__ = [
    "BugRecord",
    "BugStore",
    "BugExtractor",
    "BugRetriever",
    "BugInjector",
    "BugBase",
]
