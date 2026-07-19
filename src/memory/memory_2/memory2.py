"""
memory_2 记忆后端（重构版）

重构要点：
- 合并 Adapter 逻辑：Backend 直接管理 Injector，不再需要 Adapter 包装层
- 单一工作记忆：只在 _working_memory 中维护一份，消除三处不同步问题
- 统一时间戳：全部使用 datetime.now(timezone.utc).isoformat()
- 修复 stats()：区分 compressed/uncompressed，不再返回 long_term=0
- 修复 compact()：压缩摘要标记 compressed=True，不会被重复压缩
- 实现遗忘曲线：maintain() 中调用 decay_importance()
- 删除死配置使用：scoring weights / forgetting strategy 不再读取
- _build_models 内联：Backend 自行构建 LLM 函数，不依赖外部注入
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from src.memory.memory_interface import MemoryInterface
from src.memory.memory_2.memory_store import MemoryStore
from src.memory.memory_2.memory_retriever import MemoryRetriever
from src.memory.memory_2.memory_compressor import MemoryCompressor
from src.memory.memory_2.memory_injector import MemoryInjector

logger = logging.getLogger(__name__)


class Memory2Backend(MemoryInterface):
    """memory_2 记忆后端（LLM 召回，无向量依赖）。

    红线：
    - 严禁存储 Embedding 向量
    - 严禁使用 FAISS / ChromaDB 等向量数据库
    """

    def __init__(self, config: Any = None):
        """
        Args:
            config: 全局配置对象（含 memory_2 节），或 memory_2 配置节本身
        """
        self._cfg = config or self._default_config()
        self._llm_chat_fn: Optional[Callable[..., str]] = None

        # --- 工作记忆（单一来源） ---
        self._working_memory: List[Dict[str, Any]] = []
        wm_cfg = getattr(self._cfg, "working_memory", None)
        self._max_working_turns = getattr(wm_cfg, "max_turns", 20) if wm_cfg else 20

        # --- 短期记忆 ---
        st_cfg = getattr(self._cfg, "short_term", None)
        self._short_term_max = getattr(st_cfg, "max_items", 200) if st_cfg else 200
        self._short_term_ttl = getattr(st_cfg, "ttl_seconds", 86400) if st_cfg else 86400

        # --- 压缩 ---
        lt_cfg = getattr(self._cfg, "long_term", None)
        self._compression_threshold = getattr(lt_cfg, "compression_threshold", 0.8) if lt_cfg else 0.8

        # --- 检索 ---
        retrieval_cfg = getattr(self._cfg, "retrieval", None)
        self._default_top_k = getattr(retrieval_cfg, "default_top_k", 5) if retrieval_cfg else 5
        self._max_top_k = getattr(retrieval_cfg, "max_top_k", 20) if retrieval_cfg else 20

        scoring_cfg = getattr(self._cfg, "scoring", None)
        self._min_relevance = getattr(scoring_cfg, "min_relevance", 0.50) if scoring_cfg else 0.50

        # --- 存储 ---
        self._store = self._init_store()

        # --- LLM 函数 ---
        self._llm_chat_fn = self._build_llm_fn(config)

        # --- 检索器 ---
        self._retriever = self._init_retriever()

        # --- 压缩器 ---
        self._compressor = self._init_compressor()

        # --- 注入器（纯格式化，无状态） ---
        injection_cfg = getattr(self._cfg, "injection", None)
        self._injector = MemoryInjector(
            max_tokens=getattr(injection_cfg, "max_tokens", 2048) if injection_cfg else 2048,
        )

        logger.info(f"Memory2Backend 初始化完成: store={self._store.count()}")

    # ================================================================== #
    #  MemoryInterface 实现
    # ================================================================== #

    def add(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加记忆到持久化存储 + 工作记忆。内容重复时返回已有 ID。"""
        if metadata is None:
            metadata = {}
        if "turn" not in metadata:
            metadata["turn"] = len(self._working_memory) + 1

        mem_id = self._store.add(role, content, metadata=metadata)

        self._working_memory.append({
            "id": mem_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn": metadata["turn"],
        })
        if len(self._working_memory) > self._max_working_turns:
            self._working_memory = self._working_memory[-self._max_working_turns:]

        if self._store.count_uncompressed() > self._short_term_max:
            logger.debug("Memory2: 短期记忆超量，触发压缩")
            self.compact()

        return mem_id

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get(memory_id)

    def search(self, query: str, top_k: int = None, **filters: Any) -> List[Dict[str, Any]]:
        """LLM 召回检索。压缩摘要默认纳入候选。"""
        if top_k is None:
            top_k = self._default_top_k
        top_k = min(top_k, self._max_top_k)

        context = self._build_context_text()

        role_filter = filters.pop("role", None)
        tag_filter = filters.pop("tag", None)
        time_window = filters.pop("time_window_days", None) or 30

        results = self._retriever.search(
            query=query,
            store=self._store,
            context=context,
            top_k=top_k,
            role_filter=role_filter,
            tag_filter=tag_filter if tag_filter else None,
            time_window_days=time_window,
        )

        # 二次过滤：按 min_relevance 阈值
        filtered = [r for r in results if r.get("llm_score", 0) >= self._min_relevance]
        if len(filtered) < len(results):
            logger.info(
                f"Memory2Backend.search: 过滤 {len(results) - len(filtered)} 条低相关性记忆 "
                f"(阈值={self._min_relevance})"
            )
        return filtered

    def get_working_memory(self) -> str:
        """获取工作记忆的格式化上下文（纯工作记忆，不触发检索）。"""
        if not self._working_memory or not self._injector:
            return ""
        return self._injector.format_working_memory(self._working_memory)

    def get_context_for_query(self, query: str) -> str:
        """根据用户查询返回格式化记忆上下文（工作记忆评分 + 长期检索）。"""
        if not self._injector:
            return ""

        retrieved = self.search(query, top_k=self._default_top_k)

        # 对工作记忆进行 LLM 评分
        scored_working = self._score_working_memory(query)

        return self._injector.format_context(
            working_memory_items=scored_working,
            retrieved_items=retrieved,
        )

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
        """压缩短期记忆为长期摘要。"""
        uncompressed_count = self._store.count_uncompressed()
        if uncompressed_count <= self._short_term_max:
            return 0

        if self._compressor and self._compressor.enabled:
            if not self._compressor.should_compress(
                uncompressed_count, self._short_term_max, self._compression_threshold
            ):
                return 0

            # 只取未压缩的记忆（压缩摘要不会被重复压缩）
            uncompressed = self._store.query(include_compressed=False, limit=self._short_term_max * 2)
            if not uncompressed:
                return 0

            uncompressed.sort(key=lambda x: x.get("timestamp", ""))
            excess = uncompressed_count - self._short_term_max
            to_compress = uncompressed[:excess]
            if not to_compress:
                return 0

            try:
                summary = self._compressor.compress(items=to_compress, target_count=self._short_term_max)
                if summary:
                    source_ids = [m["id"] for m in to_compress]
                    enriched = (
                        f"[压缩摘要 - {len(to_compress)} 条记忆]\n"
                        f"时间范围: {to_compress[0].get('timestamp', '')} ~ {to_compress[-1].get('timestamp', '')}\n\n"
                        f"{summary}"
                    )
                    # 压缩摘要以 compressed=True 写入，不会被重复压缩
                    self._store.add(
                        role="system",
                        content=enriched,
                        metadata={
                            "importance": 0.7,
                            "tags": ["compressed"],
                            "compressed": True,
                        },
                    )
                    marked = self._compressor.mark_compressed(self._store, source_ids)
                    logger.info(
                        f"Memory2Backend.compact: LLM 压缩成功，"
                        f"生成 1 条长期摘要，标记 {marked} 条原始记忆为已压缩"
                    )
                    return marked
            except Exception as e:
                logger.warning(f"Memory2Backend.compact: LLM 压缩失败 ({e})，fallback 到简单删除")

        # Fallback：简单删除最旧的超量条目（只删未压缩的）
        all_uncompressed = self._store.query(include_compressed=False, limit=uncompressed_count)
        all_uncompressed.sort(key=lambda x: x.get("timestamp", ""))
        to_delete = all_uncompressed[:uncompressed_count - self._short_term_max]
        ids_to_del = [item["id"] for item in to_delete]
        deleted = self._store.delete_by_ids(ids_to_del)
        logger.info(f"Memory2Backend.compact: (fallback) 清理 {deleted} 条超量短期记忆")
        return deleted

    def stats(self) -> Dict[str, Any]:
        """返回记忆统计信息（区分压缩/未压缩）。"""
        store_stats = self._store.get_stats()
        return {
            "working": len(self._working_memory),
            "short_term": self._store.count_uncompressed(),
            "long_term": self._store.count_compressed(),
            "total": self._store.count(),
            "storage_size_bytes": self._store.get_file_size(),
            "by_role": store_stats.get("by_role", {}),
            "by_tag": store_stats.get("by_tag", {}),
        }

    def maintain(self) -> int:
        """执行维护：遗忘过期记忆 + 重要性衰减。"""
        expired = self._forget_expired()
        # 指数衰减：每次维护时对已压缩记忆衰减一次
        self._store.decay_importance(factor=0.95)
        return expired

    # ================================================================== #
    #  内部方法
    # ================================================================== #

    def set_llm_chat_fn(self, chat_fn: Callable[..., str]) -> None:
        """注入/更新 LLM 对话函数（供 factory fallback 使用）。"""
        self._llm_chat_fn = chat_fn
        self._retriever.set_llm_chat_fn(chat_fn)
        self._compressor.set_llm_chat_fn(chat_fn)

    def _build_context_text(self) -> str:
        if not self._working_memory:
            return ""
        recent = self._working_memory[-6:]
        lines = []
        for mem in recent:
            role = mem.get("role", "unknown")
            content = mem.get("content", "")[:200]
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    def _score_working_memory(self, query: str) -> List[Dict[str, Any]]:
        """对工作记忆进行 LLM 评分。"""
        if not self._working_memory or not self._llm_chat_fn:
            return [
                {**w, "score": 0.5, "llm_score": 0.5}
                for w in self._working_memory
            ]

        scorable = [
            w for w in self._working_memory
            if w.get("role") in ("user", "assistant") and w.get("content")
        ]
        unscorable = [
            w for w in self._working_memory
            if w.get("role") not in ("user", "assistant") or not w.get("content")
        ]

        if not scorable:
            return [{**w, "score": 0.5, "llm_score": 0.5} for w in self._working_memory]

        context = self._build_context_text()
        scored = self._retriever.score_external_items(
            query=query, items=scorable, context=context
        )
        return scored + [
            {**w, "score": 0.5, "llm_score": 0.5} for w in unscorable
        ]

    def _forget_expired(self) -> int:
        """清理超过 TTL 的未压缩短期记忆。"""
        if not self._short_term_ttl:
            return 0

        all_uncompressed = self._store.query(include_compressed=False, limit=999999)
        now = datetime.now(timezone.utc)
        expired_ids = []
        for item in all_uncompressed:
            ts = MemoryStore._parse_timestamp(item.get("timestamp"))
            if ts and (now - ts).total_seconds() > self._short_term_ttl:
                expired_ids.append(item["id"])

        if expired_ids:
            deleted = self._store.delete_by_ids(expired_ids)
            logger.info(f"Memory2Backend._forget_expired: 遗忘 {deleted} 条过期记忆")
            return deleted
        return 0

    # ------------------------------------------------------------------ #
    #  初始化辅助
    # ------------------------------------------------------------------ #

    def _init_store(self) -> MemoryStore:
        storage_cfg = getattr(self._cfg, "storage", None)
        storage_path = getattr(storage_cfg, "path", None) if storage_cfg else None
        backup_count = getattr(storage_cfg, "backup_count", 3) if storage_cfg else 3

        if not storage_path:
            from src.utility.config_loader import global_cfg, get_project_root
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

        return MemoryStore(storage_path, backup_count)

    def _init_retriever(self) -> MemoryRetriever:
        llm_retrieval_cfg = getattr(self._cfg, "llm_retrieval", None)
        prefilter_cfg = getattr(self._cfg, "prefilter", None)

        system_prompt = None
        prompt_file = getattr(llm_retrieval_cfg, "system_prompt_file", None) if llm_retrieval_cfg else None
        if prompt_file:
            try:
                from src.utility.config_loader import get_project_root
                prompt_path = Path(prompt_file)
                if not prompt_path.is_absolute():
                    prompt_path = Path(get_project_root()) / prompt_file
                if prompt_path.exists():
                    system_prompt = prompt_path.read_text(encoding="utf-8")
                    logger.info(f"Memory2: 加载自定义检索提示词: {prompt_path}")
            except Exception as e:
                logger.warning(f"Memory2: 加载检索提示词失败: {e}")

        return MemoryRetriever(
            config=self._cfg,
            llm_chat_fn=self._llm_chat_fn,
            system_prompt=system_prompt,
            max_candidates_per_batch=getattr(llm_retrieval_cfg, "max_candidates_per_batch", 50) if llm_retrieval_cfg else 50,
            temperature=getattr(llm_retrieval_cfg, "temperature", 0.1) if llm_retrieval_cfg else 0.1,
            max_tokens=getattr(llm_retrieval_cfg, "max_tokens", 4096) if llm_retrieval_cfg else 4096,
            score_field=getattr(llm_retrieval_cfg, "score_field", "relevance") if llm_retrieval_cfg else "relevance",
            default_top_k=self._default_top_k,
            max_top_k=self._max_top_k,
            min_relevance=self._min_relevance,
            cache_ttl_seconds=getattr(getattr(self._cfg, "retrieval", None), "cache_ttl_seconds", 60),
        )

    def _init_compressor(self) -> MemoryCompressor:
        compressor_cfg = getattr(self._cfg, "compressor", None)
        return MemoryCompressor(
            enabled=getattr(compressor_cfg, "enabled", True) if compressor_cfg else True,
            model=getattr(compressor_cfg, "model", "default") if compressor_cfg else "default",
            max_tokens_per_batch=getattr(compressor_cfg, "max_tokens_per_batch", 4000) if compressor_cfg else 4000,
            llm_chat_fn=self._llm_chat_fn,
        )

    @staticmethod
    def _build_llm_fn(config: Any) -> Optional[Callable[..., str]]:
        """从 model_key.yaml 的 memory_2 节构建 llm_chat_fn。"""
        try:
            from openai import OpenAI
            import httpx
            from src.utility.config_loader import global_cfg

            mem_cfg = getattr(global_cfg, "memory_2", None)
            if mem_cfg is None:
                logger.warning("Memory2Backend: 未找到 memory_2 模型配置（model_key.yaml）")
                return None

            llm_cfg = getattr(mem_cfg, "llm", None)
            if llm_cfg is None:
                logger.warning("Memory2Backend: memory_2 缺少 llm 配置")
                return None

            llm_provider = getattr(llm_cfg, "provider", "")
            llm_model = getattr(llm_cfg, "model_name", "")
            provider_cfg = getattr(global_cfg, llm_provider, None)
            if provider_cfg is None:
                logger.warning(f"Memory2Backend: 未找到 provider '{llm_provider}' 的配置")
                return None

            api_key = getattr(provider_cfg, "api_key", "")
            base_url = getattr(provider_cfg, "base_url", "")
            if not api_key or not base_url:
                logger.warning(f"Memory2Backend: provider '{llm_provider}' 缺少 api_key 或 base_url")
                return None

            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                http_client=httpx.Client(verify=False),
            )

            def chat_fn(api_messages, max_tokens=4096, temperature=0.1):
                try:
                    response = client.chat.completions.create(
                        model=llm_model,
                        messages=api_messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    return response.choices[0].message.content or ""
                except Exception as e:
                    logger.error(f"Memory2Backend llm_chat_fn 调用失败: {e}")
                    return ""

            logger.info(f"Memory2Backend: LLM 模型 provider={llm_provider}, model={llm_model}")
            return chat_fn
        except Exception as e:
            logger.warning(f"Memory2Backend: LLM 函数构建失败: {e}")
            return None

    @staticmethod
    def _default_config() -> Any:
        return SimpleNamespace(
            working_memory=SimpleNamespace(max_turns=20),
            short_term=SimpleNamespace(max_items=200, ttl_seconds=86400),
            long_term=SimpleNamespace(compression_threshold=0.8),
            retrieval=SimpleNamespace(default_top_k=5, max_top_k=20, cache_ttl_seconds=60),
            storage=SimpleNamespace(path="data/memory/memory2.json", backup_count=3),
            llm_retrieval=SimpleNamespace(
                model="default",
                system_prompt_file="",
                max_candidates_per_batch=50,
                temperature=0.1,
                max_tokens=4096,
                score_field="relevance",
            ),
            prefilter=SimpleNamespace(max_candidates=200, time_window_days=30, filter_by_tags=[]),
            scoring=SimpleNamespace(min_relevance=0.50),
            compressor=SimpleNamespace(enabled=True, model="default", max_tokens_per_batch=4000),
            injection=SimpleNamespace(max_tokens=2048),
        )


def create_memory(config: Any) -> Memory2Backend:
    """工厂函数，供 factory.py 调用。"""
    memory_cfg = getattr(config, "memory_2", None) or getattr(config, "memory", None)
    return Memory2Backend(config=config)
