"""
memory_1 记忆实现入口

继承 MemoryInterface，组装 MemoryStore + MemoryRetriever + Compressor + Injector，
实现完整的三层记忆系统（工作记忆 / 短期记忆 / 长期记忆）。

检索方案：Embedding + FAISS 向量检索 + 混合打分
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.memory_xx.memory_1.memory_store import MemoryStore
from src.memory_xx.memory_1.memory_retriever import MemoryRetriever
from src.memory_xx.memory_interface import MemoryInterface

logger = logging.getLogger(__name__)


class Memory1Backend(MemoryInterface):
    """memory_1 记忆后端（Embedding + FAISS）。

    组装各子模块，对外提供统一 MemoryInterface 接口。
    """

    def __init__(
        self,
        config: Any = None,
        embed_fn: Callable[[str], List[float]] = None,
    ):
        """
        Args:
            config: 记忆配置命名空间（memory_1 节）
            embed_fn: Embedding 函数，接收文本返回浮点列表
        """
        self._cfg = config or self._default_config()
        self._embed_fn = embed_fn

        # 工作记忆：本次会话的对话轮次缓冲区
        self._working_memory: List[Dict[str, Any]] = []
        self._max_working_turns = getattr(
            getattr(self._cfg, "working_memory", None), "max_turns", 20
        )

        # 短期记忆配置
        self._short_term_max = getattr(
            getattr(self._cfg, "short_term", None), "max_items", 200
        )
        self._short_term_ttl = getattr(
            getattr(self._cfg, "short_term", None), "ttl_seconds", 86400
        )

        # 长期记忆配置
        self._long_term_max = getattr(
            getattr(self._cfg, "long_term", None), "max_items", 2000
        )
        self._compression_threshold = getattr(
            getattr(self._cfg, "long_term", None), "compression_threshold", 0.8
        )

        # 检索配置
        retrieval_cfg = getattr(self._cfg, "retrieval", None)
        self._default_top_k = getattr(retrieval_cfg, "default_top_k", 5)
        self._max_top_k = getattr(retrieval_cfg, "max_top_k", 20)

        # 存储层：优先从 memory1.yaml 的 storage.path 读取，fallback 到全局 memory.storage
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
                    storage_path = str(Path(storage_path) / "memory1.json")
                backup_count = getattr(global_storage, "backup_count", backup_count)
        if not storage_path:
            storage_path = "data/memory/memory1.json"
        # 解析为绝对路径
        from src.utility.config_loader import get_project_root
        if not Path(storage_path).is_absolute():
            storage_path = str(Path(get_project_root()) / storage_path)
        self._store = MemoryStore(storage_path, backup_count)

        # 检索器
        embedding_cfg = getattr(self._cfg, "embedding", None)
        dim = getattr(embedding_cfg, "dim", 1536)
        faiss_cfg = getattr(self._cfg, "faiss", None)
        scoring_cfg = getattr(self._cfg, "scoring", None)
        forgetting_cfg = getattr(self._cfg, "forgetting", None)

        self._retriever = MemoryRetriever(
            dim=dim,
            index_type=getattr(faiss_cfg, "index_type", "IVFFlat"),
            nlist=getattr(faiss_cfg, "nlist", 100),
            semantic_weight=getattr(scoring_cfg, "semantic_weight", 0.6),
            recency_weight=getattr(scoring_cfg, "recency_weight", 0.25),
            importance_weight=getattr(scoring_cfg, "importance_weight", 0.15),
            forgetting_strategy=getattr(forgetting_cfg, "strategy", "exponential"),
            half_life_hours=getattr(forgetting_cfg, "half_life_hours", 72.0),
        )
        if embed_fn:
            self._retriever.set_embedding_function(embed_fn)

        # 压缩器与注入器（延迟初始化）
        self._compressor: Any = None
        self._injector: Any = None

        # 首次加载：从持久化存储构建索引
        self._rebuild_index_from_store()

        logger.info(
            f"Memory1Backend 初始化完成: "
            f"store={self._store.count()}, index={self._retriever.index_size}"
        )

    # ------------------------------------------------------------------ #
    #  MemoryInterface 实现
    # ------------------------------------------------------------------ #

    def add(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加记忆到短期记忆并持久化。"""
        # 计算 Embedding
        embedding = self._compute_embedding(content) if self._embed_fn else None

        # 写入存储
        mem_id = self._store.add(role, content, embedding=embedding, metadata=metadata)

        # 添加到 FAISS 索引
        if embedding:
            self._retriever.add_to_index(mem_id, embedding)

        # 更新工作记忆
        self._working_memory.append({
            "id": mem_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f"),
        })
        # 裁剪工作记忆
        if len(self._working_memory) > self._max_working_turns:
            self._working_memory = self._working_memory[-self._max_working_turns:]

        # 短期记忆超量时触发压缩
        if self._store.count() > self._short_term_max:
            logger.debug("短期记忆超量，触发压缩")
            self.compact()

        return mem_id

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get(memory_id)

    def search(self, query: str, top_k: int = None, **filters: Any) -> List[Dict[str, Any]]:
        """语义检索。"""
        if top_k is None:
            top_k = self._default_top_k
        top_k = min(top_k, self._max_top_k)

        all_items = self._store.get_all()
        if not all_items:
            return []

        role_filter = filters.pop("role", None)
        tag_filter = filters.pop("tag", None)

        results = self._retriever.search(
            query=query,
            items=all_items,
            top_k=top_k,
            role_filter=role_filter,
            tag_filter=tag_filter,
        )
        return results

    def get_working_memory(self) -> str:
        """获取格式化的记忆上下文，可直接注入 api_messages。

        返回格式参见 spec 第 5 章注入时机要求。
        """
        if not self._working_memory:
            return ""

        lines = ["[系统提醒] 以下是与当前任务相关的历史记忆，仅供参考："]
        for mem in self._working_memory[-self._max_working_turns:]:
            role_tag = {"user": "👤 用户", "assistant": "🤖 助手", "system": "⚙️ 系统"}.get(
                mem["role"], mem["role"]
            )
            lines.append(f"- [{role_tag}] {mem['content'][:200]}")
        return "\n".join(lines)


    def get_context_for_query(self, query: str) -> str:
        """根据用户查询获取记忆上下文（工作记忆 + 检索结果）。

        如果 injector 已注入（通过 adapter），使用 injector 格式化；
        否则使用内置简化格式化。

        Args:
            query: 用户输入的查询文本

        Returns:
            格式化的 Markdown 文本，可直接注入 api_messages
        """
        working = self._working_memory

        # 检索相关记忆
        retrieved = self.search(query, top_k=self._default_top_k)

        if self._injector is not None:
            return self._injector.format_context(working, retrieved)

        # 内置简化格式化（fallback）
        return self._build_context_fallback(working, retrieved)


    def _build_context_fallback(
        self,
        working: List[Dict[str, Any]],
        retrieved: List[Dict[str, Any]],
    ) -> str:
        """内置简化格式化（当 injector 未注入时使用）。"""
        if not working and not retrieved:
            return ""

        lines = ["[系统提醒] 以下是与当前任务相关的历史记忆，请参考："]

        # 工作记忆
        if working:
            lines.append("")
            lines.append("[当前任务上下文]")
            for i, mem in enumerate(working[-10:], 1):
                role_icon = {"user": "👤", "assistant": "🤖", "system": "⚙️"}.get(
                    mem.get("role", ""), "❓"
                )
                content = mem.get("content", "")[:300]
                lines.append(f"- [Turn {i}] {role_icon} {content}")

        # 检索记忆
        if retrieved:
            lines.append("")
            lines.append("[相关历史记忆]")
            for item in retrieved:
                mem_id = item.get("id", "unknown")[:8]
                content = item.get("content", "")[:500]
                score = item.get("score")
                turn = item.get("turn")
                parts = []
                if turn is not None:
                    parts.append(f"[Turn {turn}]")
                parts.append(content)
                if score is not None:
                    parts.append(f"(相关性: {score:.2f})")
                lines.append(f"- [id={mem_id}...] " + " ".join(parts))

        return "\n".join(lines)


    def update(self, memory_id: str, **fields: Any) -> bool:
        return self._store.update(memory_id, **fields)

    def delete(self, memory_id: str) -> bool:
        success = self._store.delete(memory_id)
        if success:
            self._retriever.remove_from_index(memory_id)
        return success

    def clear_all(self) -> int:
        count = self._store.clear_all()
        self._working_memory.clear()

        # 重建空索引
        self._retriever = MemoryRetriever(
            dim=self._retriever._dim,
            index_type=self._retriever._index_type,
            nlist=self._retriever._nlist,
        )
        if self._embed_fn:
            self._retriever.set_embedding_function(self._embed_fn)

        logger.info(f"Memory1Backend.clear_all: 已清空 {count} 条记忆")
        return count

    def compact(self) -> int:
        """手动触发压缩（短期 → 长期）。

        当前简化实现：直接裁剪超量条目，后续由 compressor 模块接管。
        """
        total = self._store.count()
        if total <= self._short_term_max:
            return 0

        # 临时实现：按时间排序，删除最旧的超量条目
        all_items = self._store.get_all()
        all_items.sort(key=lambda x: x.get("timestamp", ""))
        to_delete = all_items[: total - self._short_term_max]
        ids_to_del = [item["id"] for item in to_delete]
        deleted = self._store.delete_by_ids(ids_to_del)
        for mid in ids_to_del:
            self._retriever.remove_from_index(mid)

        # 重建索引
        if self._retriever.rebuild_if_needed(force=False):
            self._rebuild_index_from_store()

        logger.info(f"Memory1Backend.compact: 清理 {deleted} 条超量短期记忆")
        return deleted

    def stats(self) -> Dict[str, Any]:
        return {
            "working": len(self._working_memory),
            "short_term": self._store.count(),
            "long_term": 0,  # 待 compressor 模块填充
            "total": self._store.count(),
            "storage_size_bytes": self._store.get_file_size(),
            "index_size": self._retriever.index_size,
        }

    def maintain(self) -> int:
        """执行维护：遗忘过期记忆。"""
        cleaned = self._forget_expired()
        if self._retriever.rebuild_if_needed(force=False):
            self._rebuild_index_from_store()
        return cleaned

    # ------------------------------------------------------------------ #
    #  内部工具方法
    # ------------------------------------------------------------------ #

    def _compute_embedding(self, text: str) -> Optional[List[float]]:
        """调用注入的 Embedding 函数。"""
        if not self._embed_fn:
            return None
        try:
            return self._embed_fn(text)
        except Exception as e:
            logger.error(f"Embedding 计算失败: {e}")
            return None

    def _rebuild_index_from_store(self) -> None:
        """从持久化存储重建 FAISS 索引。"""
        items = self._store.get_all()
        if items:
            self._retriever.build_index(items)

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
                ts = self._retriever._parse_timestamp(ts_str)
                if ts and (now - ts).total_seconds() > self._short_term_ttl:
                    expired_ids.append(item["id"])

        if expired_ids:
            deleted = self._store.delete_by_ids(expired_ids)
            for mid in expired_ids:
                self._retriever.remove_from_index(mid)
            logger.info(f"Memory1Backend._forget_expired: 遗忘 {deleted} 条过期记忆")
            return deleted
        return 0

    @staticmethod
    def _default_config() -> Any:
        """当未传入配置时，提供最小可用默认配置。"""
        from types import SimpleNamespace
        return SimpleNamespace(
            working_memory=SimpleNamespace(max_turns=20),
            short_term=SimpleNamespace(max_items=200, ttl_seconds=86400),
            long_term=SimpleNamespace(max_items=2000, compression_threshold=0.8),
            retrieval=SimpleNamespace(default_top_k=5, max_top_k=20),
            storage=SimpleNamespace(path="data/memory/memory1.json", backup_count=3),
            embedding=SimpleNamespace(provider="openai", model="text-embedding-3-small", dim=1536),
            faiss=SimpleNamespace(index_type="IVFFlat", nlist=100),
            scoring=SimpleNamespace(semantic_weight=0.6, recency_weight=0.25, importance_weight=0.15),
            forgetting=SimpleNamespace(strategy="exponential", half_life_hours=72.0),
            compressor=SimpleNamespace(enabled=True, model="default", max_tokens_per_batch=4000),
        )


def create_memory(config: Any) -> Memory1Backend:
    """工厂函数，供 factory.py 调用。

    尝试从 model_key.yaml 自动构建 embed_fn，使得即使
    adapter 加载失败，fallback 路径也具备向量检索能力。
    """
    memory_cfg = getattr(config, "memory_1", None) or getattr(config, "memory", None)
    embed_fn = _try_build_embed_fn(config)
    return Memory1Backend(config=memory_cfg, embed_fn=embed_fn)


def _try_build_embed_fn(config: Any):
    """尝试从 model_key.yaml 构建 embedding 函数。

    如果构建失败（缺少配置 / 缺少依赖 / API 不可达），
    返回 None，Memory1Backend 将降级为关键词匹配检索。

    Returns:
        Callable[[str], List[float]] 或 None
    """
    try:
        from openai import OpenAI
        import httpx
        from src.utility.config_loader import global_cfg

        mem_cfg = getattr(global_cfg, "memory_1", None)
        if mem_cfg is None:
            logger.debug("_try_build_embed_fn: global_cfg.memory_1 不存在，跳过")
            return None

        emb_cfg = getattr(mem_cfg, "embedding", None)
        if emb_cfg is None:
            logger.debug("_try_build_embed_fn: memory_1.embedding 配置不存在，跳过")
            return None

        emb_provider = getattr(emb_cfg, "provider", "")
        emb_model = getattr(emb_cfg, "model_name", "")
        provider_cfg = getattr(global_cfg, emb_provider, None)

        if provider_cfg is None:
            logger.warning(f"_try_build_embed_fn: provider '{emb_provider}' 在 model_key.yaml 中不存在")
            return None

        api_key = getattr(provider_cfg, "api_key", "")
        base_url = getattr(provider_cfg, "base_url", "")
        if not api_key or not base_url:
            logger.warning(f"_try_build_embed_fn: provider '{emb_provider}' 缺少 api_key 或 base_url")
            return None

        def _embed_fn(text: str) -> list:
            try:
                resp = httpx.post(
                    f"{base_url.rstrip('/')}/embeddings",
                    json={
                        "model": emb_model,
                        "texts": [text],
                    },
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    verify=False,
                    timeout=30,
                )
                if resp.status_code != 200:
                    logger.error(
                        f"embedding 调用失败: HTTP {resp.status_code}, "
                        f"body={resp.text[:500]}"
                    )
                    return None
                body = resp.json()
                if "vectors" in body and body["vectors"]:
                    return body["vectors"][0]
                if "data" in body and body["data"]:
                    item = body["data"][0]
                    if "embedding" in item:
                        return item["embedding"]
                    if "vector" in item:
                        return item["vector"]
                logger.error(
                    f"embedding 调用失败: 无法从响应中提取向量, "
                    f"keys={list(body.keys())}, body={str(body)[:300]}"
                )
                return None
            except Exception as e:
                logger.error(f"embedding 调用失败: {e}")
                return None

        logger.info(
            f"Memory1Backend._try_build_embed_fn: embedding provider={emb_provider}, "
            f"model={emb_model}"
        )
        return _embed_fn

    except ImportError as e:
        logger.warning(f"_try_build_embed_fn: 缺少依赖 ({e})，检索将降级为关键词匹配")
        return None
    except Exception as e:
        logger.warning(f"_try_build_embed_fn: 构建 embed_fn 失败 ({e})，检索将降级为关键词匹配")
        return None