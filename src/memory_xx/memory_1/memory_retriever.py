"""
memory_1 语义检索器：Embedding + FAISS

流程：
1. 调用 Embedding API 将查询文本转为向量
2. 在 FAISS 索引中搜索 top_k 最近邻
3. 混合打分：语义相似度 + 时间衰减 + 重要性权重
4. 返回排序结果
"""

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

try:
    import faiss
except ImportError:
    faiss = None
    logging.getLogger(__name__).warning("FAISS 未安装，向量检索功能不可用。请执行 pip install faiss-cpu")

logger = logging.getLogger(__name__)


class MemoryRetriever:
    """Embedding + FAISS 语义检索器。

    职责：
    - 管理 FAISS 索引的构建与更新
    - 执行向量检索
    - 多因子混合打分（语义 + 时间 + 重要性）
    """

    def __init__(
        self,
        dim: int = 1536,
        index_type: str = "IVFFlat",
        nlist: int = 100,
        semantic_weight: float = 0.6,
        recency_weight: float = 0.25,
        importance_weight: float = 0.15,
        forgetting_strategy: str = "exponential",
        half_life_hours: float = 72.0,
    ):
        """
        Args:
            dim: Embedding 向量维度
            index_type: FAISS 索引类型（IVFFlat / Flat / HNSW）
            nlist: IVF 聚类中心数
            semantic_weight: 语义相似度权重
            recency_weight: 时间新近度权重
            importance_weight: 重要性权重
            forgetting_strategy: 遗忘曲线策略（exponential / linear / static）
            half_life_hours: 半衰期（小时），仅 exponential 模式
        """
        if faiss is None:
            raise RuntimeError("FAISS 未安装，无法创建 MemoryRetriever。请执行 pip install faiss-cpu")

        self._dim = dim
        self._index_type = index_type
        self._nlist = nlist
        self._semantic_weight = semantic_weight
        self._recency_weight = recency_weight
        self._importance_weight = importance_weight
        self._forgetting_strategy = forgetting_strategy
        self._half_life_hours = half_life_hours
        self._half_life_seconds = half_life_hours * 3600

        # FAISS 索引
        self._index: Optional[faiss.Index] = None
        self._id_to_idx: Dict[str, int] = {}   # memory_id -> FAISS 索引位置
        self._idx_to_id: Dict[int, str] = {}   # FAISS 索引位置 -> memory_id
        self._embeddings_matrix: Optional[np.ndarray] = None  # (N, dim) 向量矩阵
        self._total_items: int = 0

        # Embedding 函数（外部注入）
        self._embed_fn: Optional[Callable[[str], List[float]]] = None

    def set_embedding_function(self, embed_fn: Callable[[str], List[float]]) -> None:
        """注入 Embedding 函数。

        Args:
            embed_fn: 接收文本字符串，返回浮点列表（向量）
        """
        self._embed_fn = embed_fn

    # ------------------------------------------------------------------ #
    #  索引管理
    # ------------------------------------------------------------------ #

    def build_index(self, items: List[Dict[str, Any]]) -> None:
        """从记忆条目列表构建 FAISS 索引。

        要求每个条目的 embedding 字段非空。
        无 embedding 的条目将被跳过（记录 warning）。

        Args:
            items: 记忆条目列表，每项含 id 和 embedding 字段
        """
        vectors = []
        ids = []
        for item in items:
            emb = item.get("embedding")
            if emb and isinstance(emb, list) and len(emb) == self._dim:
                vectors.append(emb)
                ids.append(item["id"])
            else:
                logger.debug(f"跳过无 embedding 的条目: {item.get('id')}")

        if not vectors:
            logger.warning("MemoryRetriever.build_index: 无有效向量，创建空索引")
            self._create_empty_index()
            return

        matrix = np.array(vectors, dtype=np.float32)
        self._embeddings_matrix = matrix
        self._total_items = len(ids)
        n = matrix.shape[0]

        # 构建 FAISS 索引
        if self._index_type == "Flat" or n < self._nlist:
            index = faiss.IndexFlatIP(self._dim)  # 内积（余弦相似度需先归一化）
        elif self._index_type == "IVFFlat":
            quantizer = faiss.IndexFlatIP(self._dim)
            index = faiss.IndexIVFFlat(quantizer, self._dim, min(self._nlist, n))
            index.train(matrix)
        elif self._index_type == "HNSW":
            index = faiss.IndexHNSWFlat(self._dim, 32)
        else:
            logger.warning(f"未知索引类型 {self._index_type}，回退到 Flat")
            index = faiss.IndexFlatIP(self._dim)

        # L2 归一化（使内积等价于余弦相似度）
        faiss.normalize_L2(matrix)
        index.add(matrix)
        self._index = index

        # 映射表
        self._id_to_idx = {mid: i for i, mid in enumerate(ids)}
        self._idx_to_id = {i: mid for i, mid in enumerate(ids)}

        logger.info(
            f"MemoryRetriever.build_index: 索引构建完成，{n} 条向量，类型={self._index_type}"
        )

    def add_to_index(self, memory_id: str, embedding: List[float]) -> None:
        """向现有索引添加一条向量。"""
        if self._index is None:
            self._create_empty_index()

        vec = np.array(embedding, dtype=np.float32).reshape(1, -1)
        faiss.normalize_L2(vec)
        self._index.add(vec)

        idx = self._total_items
        self._id_to_idx[memory_id] = idx
        self._idx_to_id[idx] = memory_id
        self._total_items += 1

        # 更新矩阵
        if self._embeddings_matrix is not None:
            self._embeddings_matrix = np.vstack([self._embeddings_matrix, vec])
        else:
            self._embeddings_matrix = vec

    def remove_from_index(self, memory_id: str) -> None:
        """移除一条向量（标记删除，实际重建索引时清理）。"""
        self._id_to_idx.pop(memory_id, None)
        # FAISS 不支持直接删除，需调用方在足够多的删除后重建索引

    def rebuild_if_needed(self, force: bool = False) -> bool:
        """当删除条目过多时重建索引。"""
        if self._index is None:
            return False
        actual = len(self._id_to_idx)
        if force or (self._total_items > 0 and actual < self._total_items * 0.7):
            logger.info("MemoryRetriever: 触发索引重建")
            return True
        return False

    # ------------------------------------------------------------------ #
    #  检索
    # ------------------------------------------------------------------ #

    def search(
        self,
        query: str,
        items: List[Dict[str, Any]],
        top_k: int = 10,
        role_filter: Optional[str] = None,
        tag_filter: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """执行语义检索并返回混合打分后的 top_k 结果。

        当 FAISS 索引为空或 Embedding 不可用时，自动降级为关键词匹配。

        Args:
            query: 查询文本
            items: 完整的记忆条目列表（含 id、content、timestamp、importance 等）
            top_k: 返回条数
            role_filter: 按 role 过滤（"user" / "assistant" / "system"）
            tag_filter: 按标签过滤

        Returns:
            list[dict]，每项含 id、content、score、timestamp 及原始字段
        """
        embed_unavailable = self._embed_fn is None
        index_empty = self._index is None or self._index.ntotal == 0

        if index_empty or embed_unavailable:
            if embed_unavailable:
                logger.warning("MemoryRetriever.search: Embedding 函数未注入，降级为关键词匹配")
            elif index_empty:
                logger.info("MemoryRetriever.search: 索引为空，降级为关键词匹配")
            return self._keyword_search(query, items, top_k, role_filter, tag_filter)

        # 1. 查询向量化
        try:
            query_vec = self._embed_fn(query)
        except Exception as e:
            logger.error(f"Embedding 失败: {e}")
            return []

        query_np = np.array(query_vec, dtype=np.float32).reshape(1, -1)
        faiss.normalize_L2(query_np)

        # 2. FAISS 搜索
        k = min(top_k * 3, self._index.ntotal)  # 多召回一些用于后续过滤和混合打分
        distances, indices = self._index.search(query_np, k)

        # 3. 收集候选
        candidates = self._collect_candidates(indices[0], distances[0], items)

        # 4. 过滤
        candidates = self._apply_filters(candidates, role_filter, tag_filter)

        # 5. 综合打分
        now = datetime.now(timezone.utc)
        for cand in candidates:
            cand["score"] = self._compute_final_score(
                semantic_score=cand["_semantic_score"],
                timestamp=cand.get("timestamp"),
                importance=cand.get("importance", 0.5),
                now=now,
            )

        # 6. 排序
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # 7. 截断
        result = candidates[:top_k]

        # 清理内部字段
        for r in result:
            r.pop("_semantic_score", None)

        logger.info(f"MemoryRetriever.search: query='{query[:50]}...', 返回 {len(result)} 条")
        return result

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def _create_empty_index(self) -> None:
        self._index = faiss.IndexFlatIP(self._dim)
        self._id_to_idx.clear()
        self._idx_to_id.clear()
        self._embeddings_matrix = None
        self._total_items = 0

    def _collect_candidates(
        self,
        indices: np.ndarray,
        distances: np.ndarray,
        items: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """将 FAISS 索引结果映射回记忆条目。"""
        items_by_id = {item["id"]: item for item in items}
        candidates = []
        for i, dist in zip(indices, distances):
            if i < 0 or i not in self._idx_to_id:
                continue
            mid = self._idx_to_id[i]
            item = items_by_id.get(mid)
            if item:
                cand = dict(item)
                cand["_semantic_score"] = float(dist)  # FAISS 内积 = 余弦相似度
                candidates.append(cand)
        return candidates

    @staticmethod
    def _apply_filters(
        candidates: List[Dict[str, Any]],
        role_filter: Optional[str],
        tag_filter: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        """按 role 和 tag 过滤。"""
        if role_filter:
            candidates = [c for c in candidates if c.get("role") == role_filter]
        if tag_filter:
            filter_set = set(tag_filter)
            candidates = [
                c for c in candidates
                if filter_set.intersection(set(c.get("tags", [])))
            ]
        return candidates

    def _compute_final_score(
        self,
        semantic_score: float,
        timestamp: Optional[str],
        importance: float,
        now: datetime,
    ) -> float:
        """综合打分：语义相似度 + 时间衰减 + 重要性。

        Args:
            semantic_score: FAISS 余弦相似度（0~1）
            timestamp: ISO 时间戳
            importance: 重要性（0~1）
            now: 当前时间

        Returns:
            综合得分（0~1）
        """
        # 语义得分（已归一化在 0~1）
        s_sem = max(0.0, min(1.0, semantic_score))

        # 时间衰减得分
        s_rec = self._compute_recency(timestamp, now)

        # 重要性得分
        s_imp = max(0.0, min(1.0, importance))

        total = (
            self._semantic_weight * s_sem
            + self._recency_weight * s_rec
            + self._importance_weight * s_imp
        )
        return round(total, 4)

    @staticmethod
    def _parse_timestamp(ts_str: str) -> Optional[datetime]:
        """解析 timestamp 字符串，兼容 ISO 8601（带 T）和空格分隔格式。"""
        if not ts_str:
            return None
        # 尝试 ISO 8601 格式（Python 3.7+ fromisoformat 支持）
        try:
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            pass
        # 尝试空格分隔格式
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(ts_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
        return None

    def _compute_recency(self, timestamp: Optional[str], now: datetime) -> float:
        """计算时间新近度得分（0~1）。

        越新越接近 1，越旧越接近 0。
        """
        ts = self._parse_timestamp(timestamp)
        if ts is None:
            return 0.0
        age_seconds = (now - ts).total_seconds()
        if age_seconds < 0:
            return 1.0

        if self._forgetting_strategy == "exponential":
            # 指数衰减：score = 2^(-age/half_life)
            return math.pow(2, -age_seconds / self._half_life_seconds)
        elif self._forgetting_strategy == "linear":
            # 线性衰减：7 天内线性降到 0
            decay_days = 7 * 86400
            if age_seconds >= decay_days:
                return 0.0
            return 1.0 - age_seconds / decay_days
        else:
            # static：不衰减
            return 1.0

    def _keyword_search(
        self,
        query: str,
        items: List[Dict[str, Any]],
        top_k: int = 10,
        role_filter: Optional[str] = None,
        tag_filter: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """基于关键词的降级检索（Embedding 不可用时）."""
        import re

        # 提取查询关键词（>1 个字符的非标点词）
        query_lower = query.lower()
        keywords = [w for w in re.findall(r'[\w\u4e00-\u9fff]{2,}', query_lower)]

        if not keywords:
            keywords = [query_lower.strip()]

        # 过滤
        candidates = list(items)
        if role_filter:
            candidates = [c for c in candidates if c.get("role") == role_filter]

        # 计算每条记忆的关键词命中得分
        for cand in candidates:
            content = (cand.get("content") or "").lower()
            hits = sum(1 for kw in keywords if kw in content)
            # 基础分：命中数/关键词数 + 时间衰减
            keyword_score = hits / max(len(keywords), 1)
            recency = self._compute_recency(
                cand.get("timestamp"), datetime.now(timezone.utc)
            )
            importance = cand.get("importance", 0.5)
            cand["score"] = round(
                keyword_score * self._semantic_weight
                + recency * self._recency_weight
                + importance * self._importance_weight,
                4,
            )

        # 按得分排序
        candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
        return candidates[:top_k]

    @property
    def index_size(self) -> int:
        """返回索引中的向量数量。"""
        if self._index:
            return self._index.ntotal
        return 0