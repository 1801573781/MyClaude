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
            layer1_file="memory/MEMORY.md",
            metadata_file="memory/metadata.json",
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

        # 最近一次召回结果（供 query_loop 获取策略信息用于日志和 CLI 显示）
        self._last_retrieval_result = None

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

    def get_last_retrieval_result(self):
        """返回最近一次召回的 RetrievalResult 对象。

        供 query_loop 在 get_context_for_query 后获取策略信息和各阶段记录，
        用于日志记录和 CLI 显示。

        Returns:
            RetrievalResult 对象，初始为 None
        """
        return self._last_retrieval_result

    def set_progress_callback(self, callback):
        """注入进化进度回调函数。"""
        self._evolver.set_progress_callback(callback)

    def set_extract_progress_callback(self, callback):
        """注入提取进度回调函数。

        Args:
            callback: 回调函数，签名 callback(completed: int, total: int, action: str)
        """
        self._extractor.set_progress_callback(callback)

    def set_compaction_progress_callback(self, callback):
        """注入整理进度回调函数。

        Args:
            callback: 回调函数，签名 callback(step: str)，step 取值：
                      "rule_merge" / "llm_merge" / "evict"
        """
        self._compactor.set_progress_callback(callback)

    @property
    def raw_prompt_threshold(self) -> int:
        """raw 条目累积提示阈值（已废弃，保留仅为接口兼容）。"""
        return 999999  # 返回极大值，确保不会触发提示

    # ===== 兼容 MemoryInterface 的方法 =====

    def add(self, role: str, content: str, metadata=None) -> str:
        """存储记忆（已废弃，不再写入 Layer 0）。

        保留此方法仅为接口兼容，实际不做任何操作。
        记忆提取已改为从 MD 会话日志中直接提取，不再需要逐条写入。

        Args:
            role: 角色（通常为空字符串或 "user"）
            content: 原始对话内容
            metadata: 附加元数据（turn, has_tools, user_input 等）

        Returns:
            空字符串
        """
        return ""

    def get(self, memory_id: str) -> Optional[Dict]:
        """按 ID 获取单条记忆（已废弃，不再从 Layer 0 读取）。

        保留此方法仅为接口兼容。
        """
        return None

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
        1. 评估查询长度，超过300字时调用 LLM 压缩为意图摘要
        2. 调用 retriever.retrieve_for_query() 做 LLM 预检索筛选
        3. 调用 injector.format_for_injection() 格式化注入文本

        如果 Layer 1 为空（冷启动），返回空字符串。

        Args:
            query: 当前用户查询文本（可能含文件内容）
            exclude_session_id: 需要排除的 session_id（当前会话），
                                确保不召回本 session 产生的记忆

        Returns:
            格式化的记忆上下文文本，空字符串表示无内容可注入
        """
        layer1_content = self._store.read_layer1()
        if not layer1_content:
            return ""

        # 意图压缩：超过300字时调用 LLM 压缩，消除噪声
        recall_query = self._compress_intent(query)

        # LLM 预检索筛选（排除当前 session 的记忆）
        retrieval_result = self._retriever.retrieve_for_query_with_scores(
            recall_query, exclude_session_id=exclude_session_id
        )
        self._last_retrieval_result = retrieval_result

        entries = retrieval_result.final_items if retrieval_result else []
        if not entries:
            return ""

        return self._injector.format_for_injection(entries, query)

    def retrieve_detailed(self, query: str, exclude_session_id: str = "") -> "RetrievalResult":
        """执行带分数的记忆召回，供 CLI /mem rt 展示和记录日志。

        与 get_context_for_query() 使用相同的召回链路（意图压缩 + 策略召回），
        但返回包含各阶段记录的 RetrievalResult 对象，便于 CLI 打印完整召回过程
        并将召回内容完整记录到日志（md 和 html）。

        Args:
            query: 用户查询文本
            exclude_session_id: 需要排除的 session_id（当前会话）

        Returns:
            RetrievalResult 对象，包含策略信息、各阶段记录和最终结果。
        """
        layer1_content = self._store.read_layer1()
        if not layer1_content:
            from src.memory_ex.memory_retriever import RetrievalResult
            return RetrievalResult(
                strategy=self._retriever._strategy,
                strategy_desc="",
            )

        # 意图压缩：超过300字时调用 LLM 压缩，消除噪声
        recall_query = self._compress_intent(query)

        # 带完整阶段记录的策略召回（排除当前 session 的记忆）
        result = self._retriever.retrieve_for_query_with_scores(
            recall_query, exclude_session_id=exclude_session_id
        )
        return result

    def _compress_intent(self, query: str) -> str:
        """压缩用户意图到300字以内，消除CLI输出示例等噪声。

        仅用于记忆召回查询，不影响其他环节的上下文。

        Args:
            query: 用户查询文本（可能含文件内容）

        Returns:
            压缩后的意图文本；≤300字时直接返回原文；
            压缩失败时降级返回原文。
        """
        # ≤300字，不需要压缩
        if len(query) <= 300:
            return query

        # 无 LLM 调用函数，降级返回原文
        if not self._llm_chat_fn:
            logger.warning("LLM 调用函数未注入，跳过意图压缩")
            return query

        # 加载压缩 prompt 模板
        prompt_template = self._load_compress_prompt()
        if not prompt_template:
            logger.warning("意图压缩 prompt 模板未找到，跳过压缩")
            return query

        prompt = prompt_template.replace("{intent}", query)

        # 调用 LLM 压缩
        try:
            response = self._llm_chat_fn(
                prompt,
                temperature=0.1,
                max_tokens=1024,
                timeout=30.0,
            )
            if response and response.strip():
                compressed = response.strip()
                logger.info(f"意图压缩成功：{len(query)}字 → {len(compressed)}字")
                return compressed
            else:
                logger.warning("意图压缩返回空响应，降级为原文")
                return query
        except Exception as e:
            logger.warning(f"意图压缩失败: {e}，降级为原文")
            return query

    def _load_compress_prompt(self) -> str:
        """加载意图压缩 prompt 模板。"""
        try:
            prompt_path = Path(__file__).parent / "prompts" / "intent_compress_prompt.txt"
            return prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("intent_compress_prompt.txt 未找到")
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
        result = self._store.get_stats()

        # 用 evolver 的实际计数覆盖 store 的 unevolved，
        # 确保 CLI 显示的待进化数与 evolve() 实际消费数一致
        result["unevolved"] = self._evolver.get_unevolved_count()

        # MD 会话日志统计（新提取源）
        import os
        import json

        raw_memory_dir = Path(self._store._base_dir) / "raw_session_log"
        if raw_memory_dir.exists():
            md_files = list(raw_memory_dir.glob("MyClaude_*.md"))
            result["md_total"] = len(md_files)

            # 优先读取 JSON 格式记录，兼容旧 MD 格式
            json_path = Path(self._store._base_dir) / "memory" / "mem_ext_record.json"
            old_md_path = Path(self._store._base_dir) / "memory" / "mem_ext_record.md"

            extracted_count = 0
            if json_path.exists():
                try:
                    record = json.loads(json_path.read_text(encoding="utf-8"))
                    # 统计已提取且有增量内容待提取的文件数
                    extracted_count = len(record)
                except Exception:
                    extracted_count = 0
            elif old_md_path.exists():
                try:
                    extracted = set(
                        line.strip()
                        for line in old_md_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    )
                    extracted_count = len(extracted)
                except Exception:
                    extracted_count = 0

            result["md_extracted"] = extracted_count
            result["md_pending"] = len(md_files) - extracted_count
        else:
            result["md_total"] = 0
            result["md_extracted"] = 0
            result["md_pending"] = 0

        return result

    def maintain(self) -> int:
        """执行轻量维护：检查水位、衰减评分。不做整理。"""
        return self._store.maintain()

    # ===== 扩展方法 =====

    def extract(self) -> dict:
        """从 MD 会话日志中提取结构化记忆。

        扫描 raw_memory/ 目录下的 MyClaude_*.md 文件，
        调用 LLM 提取记忆，写入 MEMORY.md。
        已提取的文件记录在 mem_ext_record.md 中，不会重复提取。
        """
        return self._extractor.extract_from_md_logs()

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
