"""
get_file_context 工具实现

合并召回函数级摘要（.project_function.md）和历史 Bug 问题单（bug_base/），
实现渐进式上下文披露（Progressive Context Disclosure）。

整个工具执行零 LLM 调用，所有操作均为本地纯文本解析和路径匹配。
"""

import re
from pathlib import Path
from typing import Any, Dict, Optional

# 项目根目录（从当前文件位置推断：src/tools/file_context_tool.py → src/tools/ → src/ → 项目根）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 函数摘要文件候选名（兼容 .project_function.md 和 .init_file_summary.md）
_SUMMARY_FILE_CANDIDATES = [".project_function.md", ".init_file_summary.md"]

# Bug 库默认存储路径
_DEFAULT_BUG_BASE_DIR = _PROJECT_ROOT / "memory_storage" / "memory_ex" / "bug_base"


# ─── 函数摘要提取 ─────────────────────────────────────────────


def _find_summary_file() -> Optional[Path]:
    """在项目根目录查找函数摘要文件"""
    for name in _SUMMARY_FILE_CANDIDATES:
        p = _PROJECT_ROOT / name
        if p.exists():
            return p
    return None


def _normalize_path(path: str) -> str:
    """路径标准化：统一正斜杠 + 小写"""
    return path.replace("\\", "/").lower()


def _extract_function_summary(file_path: str) -> str:
    """
    从函数摘要文件中提取指定文件的函数摘要段落。
    纯文本解析，不调用 LLM。

    解析规则：
    1. 按 "## " 分割为多个 section
    2. 每个 section 的标题是文件路径
    3. 匹配策略：精确匹配 + 后缀匹配（双向）
    """
    summary_file = _find_summary_file()
    if summary_file is None:
        return "[FILE_SUMMARY] 摘要文件未生成，建议执行 /init f"

    try:
        content = summary_file.read_text(encoding="utf-8")
    except Exception as e:
        return f"[FILE_SUMMARY] 读取摘要文件失败: {e}"

    target = _normalize_path(file_path)

    # 按 "## " 分割为多个 section
    sections = re.split(r"^## ", content, flags=re.MULTILINE)

    for section in sections[1:]:  # 跳过前导内容
        lines = section.split("\n", 1)
        title = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""

        # 剥离 "文件：" 或 "文件:" 前缀（.project_function.md 中的标题格式）
        title_clean = title
        if title.startswith("文件："):
            title_clean = title[3:]
        elif title.startswith("文件:"):
            title_clean = title[3:]

        title_norm = _normalize_path(title_clean)

        # 匹配策略：精确匹配 + 后缀匹配（双向）
        # title_norm 通常是相对路径（如 src/query/session_log.py）
        # target 可能是绝对路径（如 d:/ai/myclaude/src/query/session_log.py）
        # 后缀匹配可兼容两种情况
        if (
            title_norm == target
            or target.endswith(title_norm)
            or title_norm.endswith(target)
        ):
            return f"[FILE_SUMMARY]\n{body}"

    return "[FILE_SUMMARY] 该文件无摘要信息"


# ─── BugStore 初始化（单例缓存） ──────────────────────────────

_bug_store_instance = None
_bug_store_initialized = False


def _get_bug_store():
    """
    延迟初始化 BugStore 实例（单例缓存）。
    尝试多种初始化方式以兼容不同构造签名。
    """
    global _bug_store_instance, _bug_store_initialized
    if _bug_store_initialized:
        return _bug_store_instance

    _bug_store_initialized = True

    bug_base_dir = str(_DEFAULT_BUG_BASE_DIR)

    # 方式1：直接导入 BugStore
    try:
        from src.memory_ex.bug_base.bug_store import BugStore

        for kwargs in [
            {"base_dir": bug_base_dir},
            {"storage_dir": bug_base_dir},
            {"bug_base_dir": bug_base_dir},
            {"root": bug_base_dir},
            {},
        ]:
            try:
                store = BugStore(**kwargs)
                if hasattr(store, "get_by_file"):
                    _bug_store_instance = store
                    return store
            except TypeError:
                continue
        # 尝试位置参数
        try:
            store = BugStore(bug_base_dir)
            if hasattr(store, "get_by_file"):
                _bug_store_instance = store
                return store
        except TypeError:
            pass
    except ImportError:
        pass

    # 方式2：通过 BugBase 获取
    try:
        from src.memory_ex.bug_base.bug_base import BugBase

        base = BugBase()
        for attr in ("store", "bug_store", "_store"):
            obj = getattr(base, attr, None)
            if obj and hasattr(obj, "get_by_file"):
                _bug_store_instance = obj
                return obj
        for method_name in ("get_store", "get_bug_store"):
            method = getattr(base, method_name, None)
            if callable(method):
                obj = method()
                if obj and hasattr(obj, "get_by_file"):
                    _bug_store_instance = obj
                    return obj
    except Exception:
        pass

    return None


# ─── Bug 格式化与排序 ─────────────────────────────────────────


def _get_field(obj: Any, name: str, default: str = "") -> str:
    """从对象或字典中获取字段值，统一返回字符串"""
    if isinstance(obj, dict):
        val = obj.get(name, default)
    else:
        val = getattr(obj, name, default)
    if isinstance(val, list):
        return " ".join(str(v) for v in val)
    return str(val) if val else default


def _format_bug_record(bug: Any) -> str:
    """格式化单条 Bug 记录为输出文本"""
    bug_id = _get_field(bug, "bug_id", _get_field(bug, "id", ""))
    title = _get_field(bug, "title", "")
    module = _get_field(bug, "module", "")
    affected_files = _get_field(bug, "affected_files", "")
    affected_functions = _get_field(bug, "affected_functions", "")
    root_cause = _get_field(bug, "root_cause", "")
    fix_pattern = _get_field(bug, "fix_pattern", "")
    caution = _get_field(bug, "caution", "")

    header = (
        f"## {bug_id} — {title}"
        if bug_id and title
        else f"## {title or bug_id}"
    )

    lines = [header]
    if module:
        lines.append(f"- **模块**: {module}")
    if affected_files:
        lines.append(f"- **文件**: {affected_files}")
    if affected_functions:
        lines.append(f"- **函数**: {affected_functions}")
    if root_cause:
        lines.append(f"- **根因**: {root_cause}")
    if fix_pattern:
        lines.append(f"- **修复方式**: {fix_pattern}")
    if caution:
        lines.append(f"- **注意事项**: {caution}")

    return "\n".join(lines)


# intent 关键词中的停用词，这些词过于通用，容易导致误匹配
_INTENT_STOPWORDS = frozenset({
    # 中文通用词
    "分析", "理解", "为什么", "改进", "查看", "修改", "实现", "功能",
    "问题", "原因", "方式", "方法", "这个", "那个", "什么", "怎么",
    "如何", "需要", "进行", "通过", "使用", "相关", "系统", "代码",
    "文件", "模块", "可以", "应该", "当前", "已经", "一些", "主要",
    "基本", "处理", "操作", "执行", "生成", "创建", "添加", "删除",
    "更新", "检查", "确认", "判断", "获取", "设置", "加载", "读取",
    "写入", "返回", "调用", "传入", "接收", "解析", "转换", "格式",
    "配置", "参数", "数据", "结果", "内容", "信息", "类型", "对象",
    "中", "的", "了", "在", "和", "与", "或", "被", "将", "给",
    "从", "到", "为", "对", "基于", "关于",
    # 英文通用词
    "the", "a", "an", "is", "are", "was", "to", "of", "in", "on",
    "at", "by", "for", "with", "from", "as", "and", "or", "if",
    "it", "its", "this", "that",
})

# Bug 相关性最低分数阈值：低于此分数的 Bug 视为无关
_MIN_RELEVANCE_SCORE = 2

# 最多返回的 Bug 条数
_MAX_BUG_RETURN = 3


def _score_bug_by_intent(bug: Any, intent: str) -> float:
    """
    根据 intent 关键词匹配计算 Bug 的相关性得分。
    自动过滤停用词，避免通用词导致误匹配。

    权重：title ×3, affected_functions ×2, root_cause ×1, fix_pattern ×1, caution ×1
    """
    if not intent:
        return 0.0

    raw_keywords = set(re.findall(r"[\w\u4e00-\u9fff]+", intent.lower()))
    # 过滤停用词和单字符关键词
    keywords = {kw for kw in raw_keywords if kw not in _INTENT_STOPWORDS and len(kw) >= 2}
    if not keywords:
        return 0.0

    score = 0.0
    for field, weight in [
        ("title", 3),
        ("affected_functions", 2),
        ("root_cause", 1),
        ("fix_pattern", 1),
        ("caution", 1),
    ]:
        text = _get_field(bug, field, "").lower()
        score += sum(weight for kw in keywords if kw in text)

    return score


def _retrieve_bugs(file_path: str, intent: str) -> str:
    """
    从 BugStore 检索与文件路径相关的 Bug 记录。
    纯本地路径匹配（BugStore.get_by_file），不调用 LLM。
    基于 intent 关键词相关性排序，过滤低分项，限制返回数量。
    """
    if not intent or not intent.strip():
        return "[BUG_ALERT] 空（未提供 intent，跳过 Bug 召回）"

    store = _get_bug_store()
    if store is None:
        return "[BUG_ALERT] 无相关历史问题"

    try:
        bugs = store.get_by_file(file_path)
    except Exception:
        return "[BUG_ALERT] 无相关历史问题"

    if not bugs:
        return "[BUG_ALERT] 无相关历史问题"

    # 按 intent 关键词相关性排序
    scored = [(bug, _score_bug_by_intent(bug, intent)) for bug in bugs]
    scored.sort(key=lambda x: x[1], reverse=True)

    # 过滤低于阈值的 Bug，避免无关召回
    relevant = [(bug, score) for bug, score in scored if score >= _MIN_RELEVANCE_SCORE]

    if not relevant:
        return "[BUG_ALERT] 无相关历史问题"

    # 限制返回数量
    relevant = relevant[:_MAX_BUG_RETURN]

    bug_texts = [_format_bug_record(bug) for bug, _ in relevant]
    return "[BUG_ALERT]\n" + "\n\n".join(bug_texts)


# ─── 工具主入口 ───────────────────────────────────────────────


def get_file_context(params: Dict[str, Any]) -> Dict[str, str]:
    """
    get_file_context 工具的主入口。

    合并召回函数级摘要和历史 Bug 问题单，供 LLM 在阅读或修改文件前
    了解文件结构和历史问题。整个执行过程零 LLM 调用。

    参数:
        params: {"path": "文件绝对路径", "intent": "任务简述"}

    返回:
        {"role": "user", "content": "[TOOL_RESULT] get_file_context(...):\\n[FILE_SUMMARY]...\\n\\n[BUG_ALERT]..."}
    """
    file_path = params.get("path", "")
    intent = params.get("intent", "")

    if not file_path:
        return {
            "role": "user",
            "content": "[TOOL_RESULT] get_file_context: path 参数不能为空",
        }

    summary_text = _extract_function_summary(file_path)
    bug_text = _retrieve_bugs(file_path, intent)

    # 缩进输出，使 [FILE_SUMMARY] 和 [BUG_ALERT] 视觉上归属于工具调用
    def _indent(text: str, prefix: str = "  ") -> str:
        return "\n".join(prefix + line if line.strip() else line for line in text.split("\n"))

    result = f"{_indent(summary_text)}\n\n{_indent(bug_text)}"
    return {
        "role": "user",
        "content": f"[TOOL_RESULT] get_file_context({file_path}):\n{result}",
    }
