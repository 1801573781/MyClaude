"""记忆系统主实现类。

对应设计文档第八章 8.4 节。

MemoryEx 实现 MemoryExInterface 全部方法（兼容 MemoryInterface + 扩展方法），
内部协调各子模块（存储、提取器、整理器、进化器、召回器、注入器）。

不持有 ContextCompressor（由 query_loop.py 直接持有）。
"""

import logging
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from src.memory_ex.memory_interface import MemoryExInterface
from src.memory_ex.memory_store import MemoryStore
from src.memory_ex.memory_extractor import MemoryExtractor
from src.memory_ex.memory_compactor import MemoryCompactor
from src.memory_ex.memory_evolver import MemoryEvolver
from src.memory_ex.memory_retriever import MemoryRetriever
from src.memory_ex.memory_injector import MemoryInjector

logger = logging.getLogger(__name__)


def _load_memory_ex_config(config: Any) -> Any:
    """加载 memory_ex 专用配置。

    从 config/memory/memory_ex.yaml 读取，合并到全局配置对象。

    Args:
        config: 全局配置对象（SimpleNamespace）

    Returns:
        memory_ex 配置对象
    """
    from pathlib import Path
    from types import SimpleNamespace

    import yaml

    # 尝试从全局配置中获取 memory_ex 配置
    if hasattr(config, "memory_ex"):
        return config.memory_ex

    # 降级：从 YAML 文件加载
    config_path = Path("D:/AI/MyClaude/config/memory/memory_ex.yaml")
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
                if yaml_data and "memory_ex" in yaml_data:
                    return _dict_to_namespace(yaml_data["memory_ex"])
        except Exception as e:
            logger.warning(f"加载 memory_ex.yaml 失败，使用默认值: {e}")

    # 最终降级：使用默认配置
    return _get_default_config()


def _dict_to_namespace(d: dict) -> SimpleNamespace:
    """递归将 dict 转为 SimpleNamespace。"""
    if not isinstance(d, dict):
        return d
    return SimpleNamespace(
        **{k: _dict_to_namespace(v) for k, v in d.items()}
    )


def _get_default_config() -> SimpleNamespace:
    """获取默认配置（当 YAML 文件不存在时使用）。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        storage=SimpleNamespace(
            base_dir="D:/AI/MyClaude/memory_storage/memory_ex/",
            layer0_file="raw_memory.jsonl",
            layer0_md_file="raw_memory.md",
            layer1_file="MEMORY.md",
            metadata_file="metadata.json",
            archive_dir="archive/",
        ),
        watermarks=SimpleNamespace(
            warning=150,
            trigger=180,
            hard_limit=200,
            target_after=160,
        ),
        auto_compaction=SimpleNamespace(
            enabled=False,
            light_query_interval=10,
            mode="full",
        ),
        auto_evolution=SimpleNamespace(
            enabled=False,
            accumulation_threshold=10,
            trend_query_interval=200,
            batch_size=50,
        ),
        extractor=SimpleNamespace(
            model="default",
            temperature=0.2,
            max_tokens=2048,
            max_entries_per_query=3,
            timeout=60,
            raw_prompt_threshold=10,
        ),
        compactor=SimpleNamespace(
            model="default",
            temperature=0.3,
            max_tokens=2048,
        ),
        evolver=SimpleNamespace(
            model="default",
            temperature=0.3,
            max_tokens=1024,
        ),
        scoring=SimpleNamespace(
            recency_weight=0.20,
            relevance_weight=0.25,
            user_explicit_weight=0.20,
            cross_module_weight=0.10,
            access_frequency_weight=0.15,
            code_absorbed_penalty=0.40,
            recency_halflife_days=7,
            access_frequency_max=10,
        ),
        archive=SimpleNamespace(
            trigger_lines=1000,
            max_archive_files=5,
        ),
        retrieval=SimpleNamespace(
            default_top_k=5,
            max_top_k=20,
        ),
        injection=SimpleNamespace(
            max_tokens=2000,
        ),
    )


class MemoryEx(MemoryExInterface):
    """记忆系统主实现类。

    实现 MemoryExInterface 的全部方法（兼容 MemoryInterface + 扩展方法）。
    内部协调各子模块。不持有 ContextCompressor（由 query_loop.py 直接持有）。
    """

    def __init__(self, config: Any):
        """初始化所有子模块。

        Args:
            config: 全局配置对象（SimpleNamespace）
        """
        self._config = config
        self._mem_config = _load_memory_ex_config(config)

        # 初始化子模块
        self._store = MemoryStore(self._mem_config)
        self._extractor = MemoryExtractor(self._mem_config, self._store)
        self._compactor = MemoryCompactor(self._mem_config, self._store)
        self._evolver = MemoryEvolver(self._mem_config, self._store)
        self._retriever = MemoryRetriever(self._mem_config, self._store)
        self._injector = MemoryInjector(self._mem_config)

        # LLM 调用函数（延迟注入）
        self._llm_chat_fn = None

        logger.info("MemoryEx 初始化完成")

    def set_llm_chat_fn(self, fn):
        """注入 LLM 调用函数，并分发给所有需要 LLM 的子模块。

        Args:
            fn: LLM 调用函数，签名 fn(prompt: str, temperature: float, max_tokens: int) -> str
        """
        self._llm_chat_fn = fn
        self._extractor.set_llm_chat_fn(fn)
        self._compactor.set_llm_chat_fn(fn)
        self._evolver.set_llm_chat_fn(fn)
        self._retriever.set_llm_chat_fn(fn)

    def set_progress_callback(self, callback):
        """注入进化进度回调函数。"""
        self._evolver.set_progress_callback(callback)

    def set_extract_progress_callback(self, callback):
        """注入提取进度回调函数。

        Args:
            callback: 回调函数，签名 callback(completed: int, total: int, action: str)
        """
        self._extractor.set_progress_callback(callback)

    @property
    def raw_prompt_threshold(self) -> int:
        """raw 条目累积提示阈值，供 query_loop 读取。"""
        return int(getattr(self._mem_config.extractor, "raw_prompt_threshold", 10))

    # ===== 兼容 MemoryInterface 的方法 =====

    def add(self, role: str, content: str, metadata=None) -> str:
        """存储记忆。将原始内容直接写入 Layer 0（status=raw），不调用 LLM。

        提取器在 Query 结束后通过 extract() 方法批量处理。

        Args:
            role: 角色（通常为空字符串或 "user"）
            content: 原始对话内容
            metadata: 附加元数据（turn, has_tools, user_input 等）

        Returns:
            新条目的 ID
        """
        return self._store.add_raw(role, content, metadata)

    def get(self, memory_id: str) -> Optional[Dict]:
        """按 ID 从 Layer 0 获取单条记忆。"""
        return self._store.get_layer0_entry(memory_id)

    def search(self, query: str, top_k: int = None, **filters) -> List[Dict]:
        """搜索 Layer 0 + Layer 2。供 CLI /mem search 命令调用。"""
        return self._retriever.search(query, top_k, **filters)

    def get_working_memory(self) -> str:
        """返回空字符串（新架构中不再有"工作记忆"概念）。

        保留此方法仅为接口兼容，CLI 如需查看记忆状态请使用 stats()。
        """
        return ""

    def get_context_for_query(self, query: str, exclude_session_id: str = "") -> str:
        """返回格式化的记忆上下文，供注入 api_messages。

        流程：
        1. 检测用户输入中的文件路径，读取内容拼入查询（召回增强）
        2. 调用 retriever.retrieve_for_query() 做 LLM 预检索筛选
        3. 调用 injector.format_for_injection() 格式化注入文本

        如果 Layer 1 为空（冷启动），返回空字符串。

        Args:
            query: 当前用户查询文本
            exclude_session_id: 需要排除的 session_id（当前会话），
                                确保不召回本 session 产生的记忆

        Returns:
            格式化的记忆上下文文本，空字符串表示无内容可注入
        """
        layer1_content = self._store.read_layer1()
        if not layer1_content:
            return ""

        # 召回增强：展开文件引用
        enhanced_query = self._build_enhanced_query(query)

        # LLM 预检索筛选（排除当前 session 的记忆）
        entries = self._retriever.retrieve_for_query(
            enhanced_query, exclude_session_id=exclude_session_id
        )
        if not entries:
            return ""

        return self._injector.format_for_injection(entries, query)

    def _build_enhanced_query(self, user_input: str) -> str:
        """构建召回查询，自动展开文件引用。

        检测用户输入中的文件路径，读取内容（截断前 2000 字符）拼入查询，
        使得 LLM 预检索能基于文件内容而非无意义的指令词进行召回。

        Args:
            user_input: 用户原始输入

        Returns:
            增强后的查询字符串
        """
        query = user_input

        # 检测文件路径
        file_paths = self._extract_file_paths(user_input)

        for path in file_paths:
            content = self._safe_read_file(path)
            if content:
                query += "\n\n" + content[:2000]

        return query

    def _extract_file_paths(self, text: str) -> List[str]:
        """从用户输入中提取文件路径。

        支持两种格式：
        - Windows 绝对路径：D:\\xxx\\xxx.md 或 D:/AI/MyClaude/xxx.md
        - 相对文件名：spider_spec.md

        Args:
            text: 用户输入文本

        Returns:
            文件路径列表
        """
        paths = []
        seen = set()

        # 匹配 Windows 绝对路径
        for m in re.finditer(r'[A-Za-z]:[\\/][^\s，。、]+\.md', text):
            p = m.group()
            if p not in seen:
                paths.append(p)
                seen.add(p)

        # 匹配 spec/ 目录下的相对文件名
        for m in re.finditer(r'[\w/\\]+\.md', text):
            p = m.group()
            if p not in seen:
                paths.append(p)
                seen.add(p)

        return paths

    def _safe_read_file(self, path: str) -> str:
        """安全读取文件，失败返回空字符串。

        仅读取 .md 和 .txt 文件。

        Args:
            path: 文件路径

        Returns:
            文件内容字符串，失败返回空字符串
        """
        try:
            p = Path(path)
            if p.exists() and p.suffix in ('.md', '.txt'):
                return p.read_text(encoding='utf-8')
        except Exception as e:
            logger.debug(f"读取文件失败 {path}: {e}")

        return ""

    def update(self, memory_id: str, **fields) -> bool:
        """更新元数据层中指定记忆的字段。"""
        return self._store.update_metadata_entry(memory_id, **fields)

    def delete(self, memory_id: str) -> bool:
        """从 Layer 1 淘汰指定记忆（Layer 0 不删除）。"""
        return self._compactor.evict_by_id(memory_id)

    def clear_all(self) -> dict:
        """清空所有层，返回详细统计信息。"""
        return self._store.clear_all()

    def compact(self) -> int:
        """手动触发完整三段式整理（Merge → Demote → Evict）。

        返回处理的条目数（保持 MemoryInterface 原签名兼容）。
        """
        result = self._compactor.run_full_compaction()
        return result.get("total_processed", 0)

    def compact_detailed(self) -> dict:
        """手动触发完整三段式整理，返回 dict 统计信息。

        供 CLI /mem compaction 命令调用，展示详细整理报告。
        """
        return self._compactor.run_full_compaction()

    def stats(self) -> Dict:
        """返回记忆统计信息。"""
        return self._store.get_stats()

    def maintain(self) -> int:
        """执行轻量维护：检查水位、衰减评分。不做整理。"""
        return self._store.maintain()

    # ===== 扩展方法 =====

    def extract(self) -> dict:
        """从 Layer 0 中 status=raw 的条目中提取结构化记忆。

        Query 结束后由 query_loop.py 显式调用。
        """
        return self._extractor.extract_raw_entries()

    def evolve(self) -> dict:
        """手动触发全类型进化。返回进化统计信息。"""
        return self._evolver.run_full_evolution()

    def check_compaction_needed(self) -> bool:
        """检查是否需要整理。"""
        return self._compactor.check_needed()

    def check_evolution_needed(self) -> bool:
        """检查是否需要进化。"""
        return self._evolver.check_needed()

    def auto_compact(self) -> dict:
        """自动整理入口。检查配置开关和水位，满足条件则同步执行。"""
        if not self._mem_config.auto_compaction.enabled:
            return {"skipped": True, "reason": "auto_compaction disabled"}
        return self._compactor.run_auto_compaction()

    def auto_evolve(self) -> dict:
        """自动进化入口。检查配置开关和积累量，满足条件则同步执行。"""
        if not self._mem_config.auto_evolution.enabled:
            return {"skipped": True, "reason": "auto_evolution disabled"}
        return self._evolver.run_auto_evolution()
