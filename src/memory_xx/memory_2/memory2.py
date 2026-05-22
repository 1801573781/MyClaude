"""
memory_2 记忆实现入口

继承 MemoryInterface，组装 MemoryStore + MemoryRetriever + Compressor + Injector，
实现完整的三层记忆系统（工作记忆 / 短期记忆 / 长期记忆）。

检索方案：LLM 直接召回（无 Embedding，无向量索引）
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.memory_xx.memory_2.memory_store import MemoryStore
from src.memory_xx.memory_2.memory_retriever import MemoryRetriever
from src.memory_xx.memory_interface import MemoryInterface

logger = logging.getLogger(__name__)


class Memory2Backend(MemoryInterface):
    """memory_2 记忆后端（LLM 召回）。

    不依赖 Embedding 和 FAISS，所有记忆以纯文本 JSON 存储，
    检索时交由 LLM 直接判断相关性并打分。

    红线：
    - 严禁存储 Embedding 向量
    - 严禁使用 FAISS / ChromaDB 等向量数据库
    """

    def __init__(self, config: Any = None, llm_chat_fn: Optional[Callable[..., str]] = None):
        """
        Args:
            config: 记忆配置命名空间（memory_2 节）
            llm_chat_fn: LLM 对话函数（用于检索评分 + 压缩）
        """
        self._cfg = config or self._default_config()
        self._llm_chat_fn = llm_chat_fn

        # 工作记忆
        self._working_memory: List[Dict[str, Any]] = []
        wm_cfg = getattr(self._cfg, "working_memory", None)
        self._max_working_turns = getattr(wm_cfg, "max_turns", 20) if wm_cfg else 20

        # 短期记忆
        st_cfg = getattr(self._cfg, "short_term", None)
        self._short_term_max = getattr(st_cfg, "max_items", 200) if st_cfg else 200
        self._short_term_ttl = getattr(st_cfg, "ttl_seconds", 86400) if st_cfg else 86400

        # 长期记忆
        lt_cfg = getattr(self._cfg, "long_term", None)
        self._long_term_max = getattr(lt_cfg, "max_items", 2000) if lt_cfg else 2000
        self._compression_threshold = getattr(
            lt_cfg, "compression_threshold", 0.8
        ) if lt_cfg else 0.8

        # 检索配置
        retrieval_cfg = getattr(self._cfg, "retrieval", None)
        self._default_top_k = getattr(retrieval_cfg, "default_top_k", 5) if retrieval_cfg else 5
        self._max_top_k = getattr(retrieval_cfg, "max_top_k", 20) if retrieval_cfg else 20

        # 存储层：优先从 memory2.yaml 的 storage.path 读取，fallback 到全局 memory.storage
        storage_cfg = getattr(self._cfg, "storage", None)
        storage_path = getattr(storage_cfg, "path", None) if storage_cfg else None
        backup_count = getattr(storage_cfg, "backup_count", 3) if storage_cfg else 3
        if not storage_path:
            # fallback 到 config.yaml 的 memory.storage
            from src.utility.config_loader import global_cfg
            global_storage = getattr(getattr(global_cfg, "memory", None), "storage", None)
            if global_storage:
                storage_path = getattr(global_storage, "path", None)
                if storage_path:
                    storage_path = str(Path(storage_path) / "memory2.json")
                backup_count = getattr(global_storage, "backup_count", backup_count)
        if not storage_path:
            storage_path = "data/memory/memory2.json"
        from src.utility.config_loader import get_project_root
        if not Path(storage_path).is_absolute():
            storage_path = str(Path(get_project_root()) / storage_path)
        self._store = MemoryStore(storage_path, backup_count)

        # 检索器
        llm_retrieval_cfg = getattr(self._cfg, "llm_retrieval", None)
        prefilter_cfg = getattr(self._cfg, "prefilter", None)
        scoring_cfg = getattr(self._cfg, "scoring", None)
        forgetting_cfg = getattr(self._cfg, "forgetting", None)

        # 加载自定义系统提示词（如果指定了文件）
        system_prompt = None
        prompt_file = getattr(llm_retrieval_cfg, "system_prompt_file", None) if llm_retrieval_cfg else None
        if prompt_file:
            try:
                prompt_path = Path(prompt_file)
                if not prompt_path.is_absolute():
                    prompt_path = Path(get_project_root()) / prompt_file
                if prompt_path.exists():
                    system_prompt = prompt_path.read_text(encoding="utf-8")
                    logger.info(f"Memory2: 加载自定义检索提示词: {prompt_path}")
            except Exception as e:
                logger.warning(f"Memory2: 加载检索提示词失败: {e}")

        self._retriever = MemoryRetriever(
            config=self._cfg,
            llm_chat_fn=llm_chat_fn,
            system_prompt=system_prompt,
            max_candidates_per_batch=getattr(llm_retrieval_cfg, "max_candidates_per_batch", 50) if llm_retrieval_cfg else 50,
            temperature=getattr(llm_retrieval_cfg, "temperature", 0.1) if llm_retrieval_cfg else 0.1,
            max_tokens=getattr(llm_retrieval_cfg, "max_tokens", 4096) if llm_retrieval_cfg else 4096,
            score_field=getattr(llm_retrieval_cfg, "score_field", "relevance") if llm_retrieval_cfg else "relevance",
            llm_score_weight=getattr(scoring_cfg, "llm_score_weight", 0.7) if scoring_cfg else 0.7,
            recency_weight=getattr(scoring_cfg, "recency_weight", 0.2) if scoring_cfg else 0.2,
            importance_weight=getattr(scoring_cfg, "importance_weight", 0.1) if scoring_cfg else 0.1,
            default_top_k=self._default_top_k,
            max_top_k=self._max_top_k,
            forgetting_strategy=getattr(forgetting_cfg, "strategy", "exponential") if forgetting_cfg else "exponential",
            half_life_hours=getattr(forgetting_cfg, "half_life_hours", 72.0) if forgetting_cfg else 72.0,
        )

        # 预过滤配置
        self._prefilter_time_window = getattr(prefilter_cfg, "time_window_days", 30) if prefilter_cfg else 30
        self._prefilter_tags = getattr(prefilter_cfg, "filter_by_tags", []) if prefilter_cfg else []
        self._prefilter_max_candidates = getattr(prefilter_cfg, "max_candidates", 200) if prefilter_cfg else 200

        # 压缩器与注入器（延迟初始化）
        self._compressor: Any = None
        self._injector: Any = None

        logger.info(
            f"Memory2Backend 初始化完成: store={self._store.count()}"
        )

    # ------------------------------------------------------------------ #
    #  MemoryInterface 实现
    # ------------------------------------------------------------------ #

    def add(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加记忆到短期记忆并持久化。严禁存储 Embedding。"""
        mem_id = self._store.add(role, content, metadata=metadata)

        self._working_memory.append({
            "id": mem_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # 裁剪工作记忆
        if len(self._working_memory) > self._max_working_turns:
            self._working_memory = self._working_memory[-self._max_working_turns:]

        # 短期记忆超量时触发压缩
        if self._store.count() > self._short_term_max:
            logger.debug("Memory2: 短期记忆超量，触发压缩")
            self.compact()

        return mem_id

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get(memory_id)

    def search(self, query: str, top_k: int = None, **filters: Any) -> List[Dict[str, Any]]:
        """LLM 召回检索。"""
        if top_k is None:
            top_k = self._default_top_k
        top_k = min(top_k, self._max_top_k)

        # 构造对话上下文
        context = self._build_context_text()

        # 预过滤参数
        role_filter = filters.pop("role", None)
        tag_filter = filters.pop("tag", None) or self._prefilter_tags
        time_window = filters.pop("time_window_days", None) or self._prefilter_time_window

        results = self._retriever.search(
            query=query,
            store=self._store,
            context=context,
            top_k=top_k,
            role_filter=role_filter,
            tag_filter=tag_filter if tag_filter else None,
            time_window_days=time_window,
        )

        # Backend 层二次过滤：按 LLM 纯评分过滤，阻止不相关记忆注入
        # 阈值与 MemoryRetriever 保持一致
        min_relevance = getattr(self._retriever, "_min_relevance", 0.50)
        filtered = [r for r in results if r.get("llm_score", 0) >= min_relevance]
        if len(filtered) < len(results):
            logger.info(
                f"Memory2Backend.search: 过滤 {len(results) - len(filtered)} 条低相关性记忆 "
                f"(阈值={min_relevance})"
            )
        return filtered

    def get_working_memory(self) -> str:
        """获取格式化的记忆上下文。"""
        if not self._working_memory:
            return ""

        lines = ["[系统提醒] 以下是与当前任务相关的历史记忆，仅供参考："]
        for mem in self._working_memory[-self._max_working_turns:]:
            role_tag = {"user": "👤 用户", "assistant": "🤖 助手", "system": "⚙️ 系统"}.get(
                mem["role"], mem["role"]
            )
            lines.append(f"- [{role_tag}] {mem['content'][:200]}")
        return "\n".join(lines)

    def update(self, memory_id: str, **fields: Any) -> bool:
        return self._store.update(memory_id, **fields)

    def delete(self, memory_id: str) -> bool:
        return self._store.delete(memory_id)

    def clear_all(self) -> int:
        count = self._store.clear_all()
        self._working_memory.clear()
        logger.info(f"Memory2Backend.clear_all: 已清空 {count} 条记忆")
        return count

    def compact(self) -> int:
        """手动触发压缩。"""
        total = self._store.count()
        if total <= self._short_term_max:
            return 0

        # 简化实现：按时间排序，删除最旧的超量条目
        all_items = self._store.get_all()
        all_items.sort(key=lambda x: x.get("timestamp", ""))
        to_delete = all_items[: total - self._short_term_max]
        ids_to_del = [item["id"] for item in to_delete]
        deleted = self._store.delete_by_ids(ids_to_del)
        logger.info(f"Memory2Backend.compact: 清理 {deleted} 条超量短期记忆")
        return deleted

    def stats(self) -> Dict[str, Any]:
        return {
            "working": len(self._working_memory),
            "short_term": self._store.count(),
            "long_term": 0,  # 待 compressor 模块填充
            "total": self._store.count(),
            "storage_size_bytes": self._store.get_file_size(),
        }

    def maintain(self) -> int:
        """遗忘过期记忆。"""
        return self._forget_expired()

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def set_llm_chat_fn(self, chat_fn: Callable[..., str]) -> None:
        """注入/更新 LLM 对话函数。"""
        self._llm_chat_fn = chat_fn
        self._retriever.set_llm_chat_fn(chat_fn)

    def _build_context_text(self) -> str:
        """构造对话上下文文本（最近几轮）。"""
        if not self._working_memory:
            return ""
        recent = self._working_memory[-6:]  # 最近 6 轮
        lines = []
        for mem in recent:
            role = mem.get("role", "unknown")
            content = mem.get("content", "")[:200]
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    def _forget_expired(self) -> int:
        """清理超过 TTL 的短期记忆。"""
        if not self._short_term_ttl:
            return 0

        all_items = self._store.get_all()
        now = datetime.now(timezone.utc)
        expired_ids = []
        for item in all_items:
            ts_str = item.get("timestamp")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if (now - ts).total_seconds() > self._short_term_ttl:
                        expired_ids.append(item["id"])
                except (ValueError, TypeError):
                    pass

        if expired_ids:
            deleted = self._store.delete_by_ids(expired_ids)
            logger.info(f"Memory2Backend._forget_expired: 遗忘 {deleted} 条过期记忆")
            return deleted
        return 0

    @staticmethod
    def _default_config() -> Any:
        """默认配置。"""
        from types import SimpleNamespace
        return SimpleNamespace(
            working_memory=SimpleNamespace(max_turns=20),
            short_term=SimpleNamespace(max_items=200, ttl_seconds=86400),
            long_term=SimpleNamespace(max_items=2000, compression_threshold=0.8),
            retrieval=SimpleNamespace(default_top_k=5, max_top_k=20),
            storage=SimpleNamespace(path="data/memory/memory2.json", backup_count=3),
            llm_retrieval=SimpleNamespace(
                model="default",
                system_prompt_file="",
                max_candidates_per_batch=50,
                temperature=0.1,
                max_tokens=4096,
                score_field="score",
            ),
            prefilter=SimpleNamespace(max_candidates=200, time_window_days=30, filter_by_tags=[]),
            scoring=SimpleNamespace(llm_score_weight=0.7, recency_weight=0.2, importance_weight=0.1),
            forgetting=SimpleNamespace(strategy="exponential", half_life_hours=72.0),
            compressor=SimpleNamespace(enabled=True, model="default", max_tokens_per_batch=4000),
        )


def create_memory(config: Any) -> Memory2Backend:
    """工厂函数，供 factory.py 调用。"""
    memory_cfg = getattr(config, "memory_2", None) or getattr(config, "memory", None)
    return Memory2Backend(config=memory_cfg)