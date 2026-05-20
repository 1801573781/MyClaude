"""memory6 Embedding 检索器 —— FAISS 向量索引 + 时间衰减 + 重要性加权混合打分

从 src/memory2/embedding_retriever.py 重新实现，保持行为语义一致。
"""

import math
import logging
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.memory_ex.memory6.memory6 import _HAS_FAISS
try:
    import faiss
except ImportError:
    pass

logger = logging.getLogger(__name__)


class EmbeddingRetriever:
    """向量语义检索 + FAISS 索引 + 混合打分。"""

    def __init__(self,
                 dim: int,
                 embedding_weight: float = 0.6,
                 time_weight: float = 0.2,
                 importance_weight: float = 0.2,
                 time_decay_hours: float = 24.0):
        self._dim = dim
        self._embedding_weight = embedding_weight
        self._time_weight = time_weight
        self._importance_weight = importance_weight
        self._time_decay_seconds = time_decay_hours * 3600

        self._use_faiss = _HAS_FAISS
        self._index: Optional["faiss.IndexFlatIP"] = None
        if self._use_faiss:
            self._index = faiss.IndexFlatIP(dim)

        self._id_map: List[Optional[str]] = []  # FAISS 不支持直接删除，用 None 标记
        self._fallback_vecs: List[np.ndarray] = []
        self._fallback_ids: List[str] = []

    def add(self, doc_id: str, vec: np.ndarray):
        vec = vec.astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        if self._use_faiss and self._index is not None:
            self._index.add(vec.reshape(1, -1))
        else:
            self._fallback_vecs.append(vec)
            self._fallback_ids.append(doc_id)

        self._id_map.append(doc_id)

    def remove(self, doc_id: str):
        if doc_id in self._id_map:
            idx = self._id_map.index(doc_id)
            self._id_map[idx] = None

    def search(self,
               query_vec: np.ndarray,
               memories: Dict[str, Dict],
               limit: int = 10) -> List[Tuple[str, float]]:
        """搜索返回 (doc_id, score) 列表，分数已含混合加权。"""
        query_vec = query_vec.astype(np.float32).reshape(1, -1)
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        now = _time.time()
        candidates: List[Tuple[str, float]] = []

        # FAISS 搜索
        if self._use_faiss and self._index is not None and self._index.ntotal > 0:
            scores, indices = self._index.search(query_vec, min(limit * 3, self._index.ntotal))
            for score, idx in zip(scores[0], indices[0]):
                if 0 <= idx < len(self._id_map):
                    doc_id = self._id_map[idx]
                    if doc_id is not None:
                        candidates.append((doc_id, float(score)))
        else:
            # 暴力搜索
            for i, vec in enumerate(self._fallback_vecs):
                doc_id = self._fallback_ids[i]
                if doc_id not in memories:
                    continue
                sim = float(np.dot(query_vec.flatten(), vec))
                candidates.append((doc_id, sim))

        # 混合打分：向量相似度 + 时间衰减 + 重要性
        scored: List[Tuple[str, float]] = []
        for doc_id, raw_score in candidates:
            mem = memories.get(doc_id)
            if mem is None:
                continue

            age = now - self._parse_timestamp(mem.get("accessed_at", mem.get("created_at", 0)))
            time_factor = (
                math.exp(-math.log(2) * age / self._time_decay_seconds)
                if self._time_decay_seconds > 0
                else 1.0
            )
            imp = mem.get("importance", 1.0)
            final = (
                self._embedding_weight * raw_score +
                self._time_weight * time_factor +
                self._importance_weight * imp
            )
            scored.append((doc_id, final))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    @staticmethod
    def _parse_timestamp(ts) -> float:
        if isinstance(ts, (int, float)) and ts > 1e9:
            return ts
        return _time.time()

    def rebuild(self, documents: List[Tuple[str, np.ndarray]]):
        """根据 (id, vec) 列表重建索引。"""
        self.__init__(
            dim=self._dim,
            embedding_weight=self._embedding_weight,
            time_weight=self._time_weight,
            importance_weight=self._importance_weight,
            time_decay_hours=self._time_decay_seconds / 3600,
        )
        for doc_id, vec in documents:
            self.add(doc_id, vec)

    def __len__(self):
        return len([i for i in self._id_map if i is not None])