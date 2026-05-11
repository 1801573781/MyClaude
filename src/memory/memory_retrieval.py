import re
import math
import logging
import time
from typing import List, Dict, Optional


logger = logging.getLogger(__name__)


# ========== 停用词 ==========

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


class MemoryRetrieval:
    """
    基于 TF‑IDF + 余弦相似度的轻量检索器。
    纯 Python 实现，不依赖 sklearn。
    """

    def __init__(self, similarity_threshold: float = 0.15):
        """
        参数:
            similarity_threshold: 相似度最低阈值（默认 0.15）。
        """
        self._similarity_threshold = similarity_threshold
        # 检索索引（按需重建）
        self._vocab: Dict[str, int] = {}          # word → index
        self._idf: Dict[str, float] = {}          # word → idf
        self._memory_vectors: Dict[str, List[float]] = {}  # id → tfidf_vec
        self._needs_rebuild = True

    # ========== 公开接口 ==========

    def search(self,
               query: str,
               long_memories: List[Dict],
               short_memories: List[Dict],
               working_memories: List[Dict],
               limit: int = 5) -> List[Dict]:
        """
        根据查询文本检索最相关的记忆条目。

        参数:
            query:            查询文本。
            long_memories:    长期记忆列表。
            short_memories:   短期记忆列表。
            working_memories: 工作记忆列表。
            limit:            最大返回条数。

        返回:
            按最终得分降序排列的记忆字典列表（已附加 "_score" 字段）。
        """
        all_persistent = long_memories + short_memories
        if not all_persistent and not working_memories:
            logger.debug("记忆为空，返回 []")
            return []

        # 预处理查询
        query_tokens = self._tokenize(query)
        if not query_tokens:
            logger.warning("查询分词后为空（可能全是停用词）")
            return []

        # 搜索持久化记忆
        results = []
        if all_persistent:
            persistent_results = self._search_persistent(
                query_tokens, all_persistent, limit
            )
            results.extend(persistent_results)

        # 搜索工作记忆（Jaccard）
        if working_memories:
            working_results = self._search_working(
                query_tokens, working_memories, limit
            )
            # 工作记忆排在长期记忆之前
            results = working_results + results

        # 截断到 limit
        results = results[:limit]

        # 命中后更新 access_count / last_access（仅持久化条目才有 id）
        for r in results:
            if "id" in r:
                r["access_count"] = r.get("access_count", 0) + 1
                r["last_access"] = int(time.time())

        return results

    def rebuild_index(self, memories: Optional[List[Dict]] = None) -> None:
        """
        重建 TF‑IDF 索引。

        参数:
            memories: 要建索引的记忆列表，None 表示保留现有向量。
        """
        self._needs_rebuild = True
        if memories is not None:
            self._memory_vectors = {}
            logger.info(f"索引已标记重建，共 {len(memories)} 条记忆")

    # ========== 持久化记忆检索 ==========

    def _search_persistent(self,
                           query_tokens: List[str],
                           memories: List[Dict],
                           limit: int) -> List[Dict]:
        """对持久化记忆执行 TF‑IDF 检索。"""
        if self._needs_rebuild or not self._vocab:
            self._build_index(memories)

        query_vec = self._vectorize_tokens(query_tokens)

        if all(v == 0.0 for v in query_vec):
            logger.warning("查询向量全为零")
            return []

        scored = []
        for mem in memories:
            mem_id = mem["id"]
            mem_vec = self._memory_vectors.get(mem_id)
            if mem_vec is None:
                # 新记忆未在索引中，重新向量化
                mem_vec = self._vectorize_tokens(
                    self._tokenize(mem.get("content", ""))
                )
                self._memory_vectors[mem_id] = mem_vec

            raw_score = self._cosine_similarity(query_vec, mem_vec)

            # 最终得分加权
            importance = mem.get("importance", 0.5)
            access_count = mem.get("access_count", 0)
            final_score = (
                raw_score
                * (0.7 + 0.3 * importance)
                * (1 + math.log(access_count + 1) / 10)
            )

            if final_score >= self._similarity_threshold:
                scored.append((final_score, mem))

        # 按 final_score 降序
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
        """对工作记忆执行 Jaccard 相似度检索。"""
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

    # ========== TF‑IDF 构建 ==========

    def _build_index(self, memories: List[Dict]) -> None:
        """
        从记忆列表构建词表、IDF 和向量。
        """
        self._vocab = {}
        self._idf = {}
        self._memory_vectors = {}

        if not memories:
            self._needs_rebuild = False
            return

        # 1. 收集词频统计
        all_docs_tokens = []
        doc_word_sets = []  # 每条记忆出现的词集合（用于 IDF）

        for mem in memories:
            tokens = self._tokenize(mem.get("content", ""))
            all_docs_tokens.append(tokens)
            doc_word_sets.append(set(tokens))

        # 2. 文档频率
        df = {}
        N = len(memories)
        for word_set in doc_word_sets:
            for w in word_set:
                df[w] = df.get(w, 0) + 1

        # 3. 优化：记忆数 > 500 时限词表
        if N > 500:
            sorted_words = sorted(df.items(), key=lambda x: x[1], reverse=True)
            top_words = sorted_words[:2000]
            vocab_words = [w for w, _ in top_words]
        else:
            vocab_words = sorted(df.keys())

        # 4. 构建词表映射
        self._vocab = {w: i for i, w in enumerate(vocab_words)}

        # 5. 计算 IDF
        for w, idx in self._vocab.items():
            self._idf[w] = math.log((N + 1) / (df.get(w, 0) + 1)) + 1

        # 6. 为每条记忆计算 TF‑IDF 向量
        for i, mem in enumerate(memories):
            tokens = all_docs_tokens[i]
            vec = self._compute_tfidf_vector(tokens)
            self._memory_vectors[mem["id"]] = vec

        self._needs_rebuild = False
        logger.info(
            f"索引构建完成: 词表大小={len(self._vocab)}, 记忆={N}"
        )

    def _vectorize_tokens(self, tokens: List[str]) -> List[float]:
        """计算 token 列表的 TF‑IDF 向量（用于查询）。"""
        return self._compute_tfidf_vector(tokens)

    def _compute_tfidf_vector(self, tokens: List[str]) -> List[float]:
        """
        计算 TF‑IDF 向量。

        TF(w) = count(w) / len(tokens)
        TF‑IDF(w) = TF(w) * IDF(w)
        """
        vec = [0.0] * len(self._vocab)
        if not tokens:
            return vec

        total = len(tokens)

        for w in tokens:
            if w in self._vocab:
                idx = self._vocab[w]
                vec[idx] += 1.0 / total

        # 乘 IDF
        for w, idx in self._vocab.items():
            if vec[idx] > 0:
                vec[idx] *= self._idf.get(w, 1.0)

        return vec

    # ========== 文本预处理 ==========

    def _tokenize(self, text: str) -> List[str]:
        """
        预处理 + 分词。

        步骤:
            1. 小写化。
            2. 按非字母数字/非中文字符分割。
            3. 去停用词。
            4. 中文按单字切分。
        """
        text = text.lower()

        # 分割：保留字母数字和中文字符
        segments = re.split(r'[^a-z0-9\u4e00-\u9fff]+', text)
        segments = [s for s in segments if s]

        tokens = []
        for seg in segments:
            if re.search(r'[\u4e00-\u9fff]', seg):
                # 中文段：按单字切分
                for ch in seg:
                    if ch not in ALL_STOPWORDS:
                        tokens.append(ch)
            else:
                # 英文/数字段：整体保留
                if seg not in ALL_STOPWORDS:
                    tokens.append(seg)

        return tokens

    # ========== 数学工具 ==========

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """
        计算余弦相似度。

        sim = (a·b) / (||a|| * ||b|| + 1e-8)
        """
        if len(vec_a) != len(vec_b):
            return 0.0

        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return dot / (norm_a * norm_b + 1e-8)