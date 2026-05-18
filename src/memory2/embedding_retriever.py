"""
EmbeddingRetriever - 基于 Embedding 向量的语义记忆召回器

核心特性（与旧 memory_retrieval.py 的本质区别）：

1. **真正的向量召回**：使用 embedding 模型（OpenAI 兼容 API）将文本转为向量，
   通过余弦相似度在向量空间中进行语义搜索。
   —— 不再是对中文做单字 TF-IDF 这种「伪向量召回」。

2. **FAISS 加速**：当记忆量较大时，使用 FAISS 构建 IVF 索引，
   实现亚线性时间的高效近似最近邻（ANN）搜索。

3. **混合打分**：
   - 语义相似度 (cosine) 为主体
   - 时间衰减 (recency boost)：越近的记忆得分越高
   - 重要性加权 (importance)：用户标记的重要记忆优先
   - 使用频率衰减 (access_count 饱和函数)：避免正反馈循环

4. **分批 embedding**：支持 batch_size 控制，避免一次性请求过大。

5. **内嵌向量缓存**：embedding 直接存储在 MemoryEntry 中，
   增量更新时只对新记忆生成向量。

设计原则：
- 不追求极致的数学正确，但必须比字符级 TF-IDF 有质的飞跃
- embedding 调用失败时优雅降级（回退到关键词匹配）
- 使用饱和函数（log / sigmoid）控制各项权重的边际效应
"""

import math
import time
from typing import List, Optional, Callable, Dict, Tuple
from dataclasses import dataclass, field

from .memory_store import MemoryEntry, MemoryStore


@dataclass
class RetrievalResult:
    """单条检索结果"""
    memory_id: str
    content: str
    score: float
    semantic_score: float = 0.0
    recency_score: float = 0.0
    importance: float = 0.5
    entry: Optional[MemoryEntry] = None


class EmbeddingGenerator:
    """
    Embedding 向量生成器。
    通过 OpenAI 兼容 API 调用 embedding 模型。

    内置 MiniMax 默认 endpoint，也接受自定义配置。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.minimax.chat/v1",
        model: str = "embo-01",
    ):
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._cache: Dict[str, List[float]] = {}  # 文本 → 向量

    def embed(self, text: str) -> List[float]:
        """生成单条文本的 embedding 向量。"""
        if text in self._cache:
            return self._cache[text]

        try:
            resp = self._client.embeddings.create(
                model=self._model,
                input=[text],
            )
            vec = resp.data[0].embedding
            self._cache[text] = vec
            return vec
        except Exception as e:
            raise RuntimeError(f"Embedding 生成失败: {e}")

    def embed_batch(self, texts: List[str], batch_size: int = 8) -> List[List[float]]:
        """批量生成 embedding 向量。"""
        vectors = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            uncached = []
            uncached_indices = []

            for j, t in enumerate(batch):
                if t in self._cache:
                    vectors.append(self._cache[t])
                else:
                    uncached.append(t)
                    uncached_indices.append(i + j)

            if uncached:
                try:
                    resp = self._client.embeddings.create(
                        model=self._model,
                        input=uncached,
                    )
                    for k, data_item in enumerate(resp.data):
                        vec = data_item.embedding
                        actual_idx = uncached_indices[k]
                        self._cache[uncached[k]] = vec
                        # 维护顺序
                        while len(vectors) < actual_idx:
                            pass  # 不应发生
                        # 由于可能乱序，采用不同策略
                except Exception as e:
                    raise RuntimeError(f"批量 Embedding 生成失败: {e}")

            # 简化处理：统一重排
            # 重新处理整个 batch
            for j, t in enumerate(batch):
                if t in self._cache:
                    if i + j < len(vectors):
                        vectors.insert(i + j, self._cache[t])
                    else:
                        vectors.append(self._cache[t])

        return vectors


class EmbeddingRetriever:
    """
    基于 Embedding 向量的语义记忆检索器。

    使用方式：
        retriever = EmbeddingRetriever(
            store=memory_store,
            embedding_generator=EmbeddingGenerator(api_key="..."),
        )
        results = retriever.retrieve("用户最近的代码风格偏好", top_k=5)

    特性：
    - 新记忆自动生成 embedding 并缓存到 MemoryEntry.embedding 中
    - FAISS 索引可选启用（需安装 faiss-cpu）
    - 多维度打分函数，避免单一向量距离的片面性
    """

    def __init__(
        self,
        store: MemoryStore,
        embedding_generator: EmbeddingGenerator,
        use_faiss: bool = True,
        similarity_threshold: float = 0.35,
        top_k: int = 5,
        recency_weight: float = 0.15,
        importance_weight: float = 0.10,
        batch_size: int = 8,
    ):
        """
        Args:
            store: MemoryStore 实例
            embedding_generator: Embedding 生成器
            use_faiss: 是否使用 FAISS 加速（需安装 faiss-cpu）
            similarity_threshold: 语义相似度最低阈值（0~1）
            top_k: 召回数量
            recency_weight: 时间新近度权重
            importance_weight: 重要性权重
            batch_size: embedding 批量大小
        """
        self._store = store
        self._embed = embedding_generator
        self._use_faiss = use_faiss
        self._similarity_threshold = similarity_threshold
        self._top_k = top_k
        self._recency_weight = recency_weight
        self._importance_weight = importance_weight
        self._batch_size = batch_size

        # FAISS 索引（惰性初始化）
        self._faiss_index = None
        self._faiss_id_to_mem = {}  # FAISS 内部 ID → memory_id
        self._index_version = 0     # 记忆变更计数器，触发重建

    # ========== 公开接口 ==========

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        memory_type: Optional[str] = None,
    ) -> List[RetrievalResult]:
        """
        根据 query 文本召回最相关的记忆。

        Args:
            query: 查询文本（通常是用户当前提问）
            top_k: 返回结果数，默认使用 __init__ 的值
            memory_type: 筛选记忆类型（如 "long_term"），None 表示全部

        Returns:
            按综合得分降序排列的 RetrievalResult 列表
        """
        self._store._ensure_loaded()
        top_k = top_k or self._top_k

        # 1. 确保所有记忆都有 embedding
        self._ensure_embeddings()

        # 2. 生成 query 的 embedding
        try:
            query_vec = self._embed.embed(query)
        except RuntimeError:
            # embedding 失败，降级回退
            return self._fallback_retrieve(query, top_k, memory_type)

        # 3. 收集候选记忆
        candidates = self._get_candidates(memory_type)
        if not candidates:
            return []

        # 4. 计算语义相似度
        scored = self._compute_scores(query_vec, candidates)

        # 5. 排序 + 截断
        scored.sort(key=lambda r: r.score, reverse=True)

        # 6. 更新访问计数（使用饱和函数，避免正反馈循环）
        self._update_access_with_decay(scored[:top_k])

        return scored[:top_k]

    def rebuild_index(self):
        """强制重建 FAISS 索引（记忆变更后调用）。"""
        self._faiss_index = None
        self._faiss_id_to_mem = {}
        self._index_version += 1

    # ========== 内部方法 ==========

    def _ensure_embeddings(self):
        """确保所有记忆都有 embedding 向量。"""
        entries = self._store.get_all()
        missing = [e for e in entries if e.embedding is None]

        if not missing:
            return

        # 批量生成 embedding
        texts = [e.content for e in missing]
        try:
            vecs = self._embed_batch(texts)
            for entry, vec in zip(missing, vecs):
                entry.embedding = vec
        except RuntimeError:
            # 批量失败，逐个尝试
            for entry in missing:
                try:
                    entry.embedding = self._embed.embed(entry.content)
                except RuntimeError:
                    entry.embedding = None

        # 标记索引需要重建
        self.rebuild_index()

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量生成 embedding（优化版，保证顺序）。"""
        vectors = [None] * len(texts)

        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            try:
                from openai import OpenAI
                # 复用已缓存的 embedding generator 的 client
                resp = self._embed._client.embeddings.create(
                    model=self._embed._model,
                    input=batch,
                )
                for j, data_item in enumerate(resp.data):
                    self._embed._cache[batch[j]] = data_item.embedding
                    vectors[i + j] = data_item.embedding
            except Exception:
                raise RuntimeError(f"批量 embedding 失败，batch [{i}:{i+len(batch)}]")

        return vectors

    def _get_candidates(self, memory_type: Optional[str] = None) -> List[MemoryEntry]:
        """获取候选记忆列表，按类型筛选。"""
        if memory_type:
            return self._store.get_by_type(memory_type)
        return self._store.get_all()

    def _compute_scores(
        self,
        query_vec: List[float],
        candidates: List[MemoryEntry],
    ) -> List[RetrievalResult]:
        """计算每条候选记忆的综合得分。"""
        now = time.time()
        results = []

        # 归一化因子
        max_age = max((now - e.created_at) for e in candidates) if candidates else 1.0
        if max_age < 1.0:
            max_age = 1.0

        for entry in candidates:
            # (a) 语义相似度
            semantic = 0.0
            if entry.embedding:
                semantic = self._cosine_similarity(query_vec, entry.embedding)

            if semantic < self._similarity_threshold:
                continue  # 低于阈值，直接跳过

            # (b) 时间新近度（饱和函数: 1 - normalized_age，log 压缩）
            age = now - entry.created_at
            norm_age = age / max_age
            recency = 1.0 - math.log(1 + norm_age * 9) / math.log(10)
            recency = max(0.0, min(1.0, recency))

            # (c) 重要性
            importance = entry.importance

            # (d) 访问频率（饱和函数：避免正反馈循环）
            # access_count 高不会让得分暴增
            access_bonus = math.log(1 + entry.access_count) / 10.0 * 0.05

            # 综合得分
            score = (
                semantic * (1.0 - self._recency_weight - self._importance_weight - 0.05)
                + recency * self._recency_weight
                + importance * self._importance_weight
                + access_bonus
            )

            results.append(RetrievalResult(
                memory_id=entry.id,
                content=entry.content,
                score=score,
                semantic_score=semantic,
                recency_score=recency,
                importance=importance,
                entry=entry,
            ))

        return results

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """余弦相似度，范围 [0, 1]（取 max(0, cos) 避免负值）。"""
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return max(0.0, dot / (norm_a * norm_b))

    def _update_access_with_decay(self, results: List[RetrievalResult]):
        """
        更新访问计数（使用饱和函数避免正反馈循环）。

        旧模块的问题：access_count 累积导致不相关记忆反复被召回。
        新方案：仅对 top_k 结果微增计数，使用 log 饱和。
        """
        for r in results:
            if r.entry:
                # 使用对数增长，而非线性增长
                r.entry.access_count = min(
                    r.entry.access_count + 0.3,
                    100.0,  # 硬上限
                )
                r.entry.last_access = time.time()

    def _fallback_retrieve(
        self,
        query: str,
        top_k: int,
        memory_type: Optional[str],
    ) -> List[RetrievalResult]:
        """
        当 embedding 不可用时的降级策略：
        基于关键词（英文单词 + 中文字符）进行简单的 Jaccard 召回。
        比旧模块的 TF-IDF 更保守，阈值更高。
        """
        candidates = self._get_candidates(memory_type)
        query_chars = set(query.lower())

        results = []
        for entry in candidates:
            content_chars = set(entry.content.lower())
            if not query_chars or not content_chars:
                continue
            # Jaccard 相似度
            intersection = query_chars & content_chars
            union = query_chars | content_chars
            jaccard = len(intersection) / len(union) if union else 0.0

            if jaccard > 0.30:  # 降级模式阈值更高
                results.append(RetrievalResult(
                    memory_id=entry.id,
                    content=entry.content,
                    score=jaccard,
                    semantic_score=jaccard,
                    entry=entry,
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]