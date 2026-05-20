"""memory5 独立检索器 —— 基于 TF-IDF + 余弦相似度 + Jaccard

从 src/memory/memory_retrieval.py 重新实现，保持行为语义一致。
支持停用词过滤、词表优化、按需重建索引。
"""

import re
import math
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ---------- 停用词 ----------
_EN_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "in", "on",
    "to", "for", "and", "or", "it", "this", "that", "with", "as",
    "at", "by", "from"
}

_ZH_STOPWORDS = {
    "的", "了", "和", "是", "在", "不", "我", "有", "也", "就",
    "都", "要", "会", "可以", "这个"
}

ALL_STOPWORDS = _EN_STOPWORDS | _ZH_STOPWORDS


class Memory5Retrieval:
    """基于 TF-IDF + 余弦相似度的轻量检索器（纯 Python）。"""

    def __init__(self, similarity_threshold: float = 0.15):
        self._similarity_threshold = similarity_threshold
        self._vocab: Dict[str, int] = {}
        self._idf: Dict[str, float] = {}
        self._memory_vectors: Dict[str, List[float]] = {}
        self._needs_rebuild = True

    # ========== 公开接口 ==========

    def search(self,
               query: str,
               long_memories: List[Dict],
               short_memories: List[Dict],
               working_memories: List[Dict],
               limit: int = 5) -> List[Dict]:
        all_persistent = long_memories + short_memories
        if not all_persistent and not working_memories:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            logger.warning("查询分词后为空")
            return []

        results = []
        if all_persistent:
            results = self._search_persistent(query_tokens, all_persistent, limit)

        if working_memories:
            working_results = self._search_working(query_tokens, working_memories, limit)
            results = working_results + results

        results = results[:limit]

        for r in results:
            if "id" in r:
                r["access_count"] = r.get("access_count", 0) + 1
                r["last_access"] = self._now_ts()

        return results

    def rebuild_index(self, memories: Optional[List[Dict]] = None) -> None:
        self._needs_rebuild = True
        if memories is not None:
            self._memory_vectors = {}
            logger.info(f"Memory5Retrieval 索引已标记重建，共 {len(memories)} 条")

    # ========== 持久化记忆检索 ==========

    def _search_persistent(self,
                           query_tokens: List[str],
                           memories: List[Dict],
                           limit: int) -> List[Dict]:
        if self._needs_rebuild or not self._vocab:
            self._build_index(memories)

        query_vec = self._vectorize_tokens(query_tokens)
        if all(v == 0.0 for v in query_vec):
            return []

        scored = []
        for mem in memories:
            mem_id = mem["id"]
            mem_vec = self._memory_vectors.get(mem_id)
            if mem_vec is None:
                mem_vec = self._vectorize_tokens(self._tokenize(mem.get("content", "")))
                self._memory_vectors[mem_id] = mem_vec

            raw_score = self._cosine_similarity(query_vec, mem_vec)

            importance = mem.get("importance", 0.5)
            access_count = mem.get("access_count", 0)
            final_score = (
                raw_score
                * (0.7 + 0.3 * importance)
                * (1 + math.log(access_count + 1) / 10)
            )

            if final_score >= self._similarity_threshold:
                scored.append((final_score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, mem in scored[:limit]:
            mem_copy = dict(mem)
            mem_copy["_score"] = round(score, 4)
            results.append(mem_copy)
        return results

    # ========== 工作记忆检索 ==========

    def _search_working(self,
                        query_tokens: List[str],
                        working_memories: List[Dict],
                        limit: int) -> List[Dict]:
        query_set = set(query_tokens)
        scored = []
        for mem in working_memories:
            mem_tokens = set(self._tokenize(mem.get("content", "")))
            if not mem_tokens:
                continue
            intersection = len(query_set & mem_tokens)
            union = len(query_set | mem_tokens)
            jaccard = intersection / union if union > 0 else 0.0
            if jaccard >= self._similarity_threshold:
                scored.append((jaccard, mem))
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, mem in scored[:limit]:
            mem_copy = dict(mem)
            mem_copy["_score"] = round(score, 4)
            results.append(mem_copy)
        return results

    # ========== TF-IDF 构建 ==========

    def _build_index(self, memories: List[Dict]) -> None:
        self._vocab = {}
        self._idf = {}
        self._memory_vectors = {}

        if not memories:
            self._needs_rebuild = False
            return

        all_docs_tokens = []
        doc_word_sets = []
        for mem in memories:
            tokens = self._tokenize(mem.get("content", ""))
            all_docs_tokens.append(tokens)
            doc_word_sets.append(set(tokens))

        df = {}
        N = len(memories)
        for word_set in doc_word_sets:
            for w in word_set:
                df[w] = df.get(w, 0) + 1

        if N > 500:
            sorted_words = sorted(df.items(), key=lambda x: x[1], reverse=True)
            top_words = sorted_words[:2000]
            vocab_words = [w for w, _ in top_words]
        else:
            vocab_words = sorted(df.keys())

        self._vocab = {w: i for i, w in enumerate(vocab_words)}

        for w, idx in self._vocab.items():
            self._idf[w] = math.log((N + 1) / (df.get(w, 0) + 1)) + 1

        for i, mem in enumerate(memories):
            tokens = all_docs_tokens[i]
            vec = self._compute_tfidf_vector(tokens)
            self._memory_vectors[mem["id"]] = vec

        self._needs_rebuild = False
        logger.info(
            f"Memory5Retrieval 索引构建完成: 词表={len(self._vocab)}, 记忆={N}"
        )

    def _vectorize_tokens(self, tokens: List[str]) -> List[float]:
        return self._compute_tfidf_vector(tokens)

    def _compute_tfidf_vector(self, tokens: List[str]) -> List[float]:
        vec = [0.0] * len(self._vocab)
        if not tokens:
            return vec
        total = len(tokens)
        for w in tokens:
            if w in self._vocab:
                idx = self._vocab[w]
                vec[idx] += 1.0 / total
        for w, idx in self._vocab.items():
            if vec[idx] > 0:
                vec[idx] *= self._idf.get(w, 1.0)
        return vec

    # ========== 文本预处理 ==========

    def _tokenize(self, text: str) -> List[str]:
        text = text.lower()
        segments = re.split(r'[^a-z0-9\u4e00-\u9fff]+', text)
        segments = [s for s in segments if s]
        tokens = []
        for seg in segments:
            if re.search(r'[\u4e00-\u9fff]', seg):
                for ch in seg:
                    if ch not in ALL_STOPWORDS:
                        tokens.append(ch)
            else:
                if seg not in ALL_STOPWORDS:
                    tokens.append(seg)
        return tokens

    # ========== 数学工具 ==========

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        if len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b + 1e-8)

    @staticmethod
    def _now_ts() -> str:
        import time
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())