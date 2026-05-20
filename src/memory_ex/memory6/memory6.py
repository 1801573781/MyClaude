"""memory6 核心实现 —— 基于 Embedding + FAISS + LLM 双路召回

继承 MemoryBackend，实现三层记忆（Working / Short-term / Long-term）。
参考 src/memory2 的实现风格重新编写，保持行为语义一致。
"""

import json
import math
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.memory_ex.memory_interface import MemoryBackend

# 尝试导入可选依赖
try:
    import faiss
    _HAS_FAISS = True
except ImportError:
    _HAS_FAISS = False

try:
    from openai import OpenAI
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


# ---------------------------------------------------------------------------
# 中文 / 英文 简单分词（供 Jaccard 和日志使用）
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> List[str]:
    return re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z]+|\d+", text.lower())


def _jaccard_similarity(a: str, b: str) -> float:
    set_a = set(_tokenize(a))
    set_b = set(_tokenize(b))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


# ---------------------------------------------------------------------------
# Embedding 计算
# ---------------------------------------------------------------------------
class EmbeddingEngine:
    """向量化引擎，封装 OpenAI 兼容的 Embedding API"""

    def __init__(self, config: Dict[str, Any]):
        self._cfg = config
        self._model  = config.get("embedding_model",  "text-embedding-3-small")
        self._dim    = int(config.get("embedding_dim", 1536))
        self._client: Optional["OpenAI"] = None

        if _HAS_OPENAI:
            api_key = config.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
            base_url = config.get("base_url")
            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = OpenAI(**kwargs)

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: List[str]) -> np.ndarray:
        """将文本列表转为 Embedding 向量矩阵 (N, dim)"""
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)

        if self._client is None:
            # 降级：使用零向量
            return np.zeros((len(texts), self._dim), dtype=np.float32)

        resp = self._client.embeddings.create(model=self._model, input=texts)
        vectors = [d.embedding for d in resp.data]
        return np.array(vectors, dtype=np.float32)

    def embed_single(self, text: str) -> np.ndarray:
        """单文本 Embedding，返回 (dim,)"""
        vecs = self.embed([text])
        return vecs[0] if len(vecs) > 0 else np.zeros(self._dim, dtype=np.float32)


# ---------------------------------------------------------------------------
# FAISS 索引封装
# ---------------------------------------------------------------------------
class FaissIndex:
    """FAISS 向量索引封装，替代时降级为暴力搜索"""

    def __init__(self, dim: int, use_faiss: bool = True):
        self._dim = dim
        self._use_faiss = use_faiss and _HAS_FAISS
        self._index: Any = None
        self._id_map: List[str] = []
        self._vectors: List[np.ndarray] = []

        if self._use_faiss:
            self._index = faiss.IndexFlatIP(dim)  # 内积（需 L2 归一化后等价余弦）

    def add(self, doc_id: str, vec: np.ndarray):
        """添加向量到索引"""
        vec = vec.astype(np.float32)
        # L2 归一化（使内积等于余弦相似度）
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        if self._use_faiss and vec.shape[0] == self._dim:
            self._index.add(vec.reshape(1, -1))
        else:
            self._vectors.append(vec)

        self._id_map.append(doc_id)

    def remove(self, doc_id: str):
        """FAISS 不支持直接删除，标记为 None 并在搜索时过滤"""
        if doc_id in self._id_map:
            idx = self._id_map.index(doc_id)
            self._id_map[idx] = None

    def search(self, query_vec: np.ndarray, limit: int = 5) -> List[Tuple[str, float]]:
        """搜索返回 (doc_id, score) 列表"""
        query_vec = query_vec.astype(np.float32).reshape(1, -1)
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        if self._use_faiss and self._index is not None and self._index.ntotal > 0:
            scores, indices = self._index.search(query_vec, min(limit * 2, self._index.ntotal))
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if 0 <= idx < len(self._id_map):
                    doc_id = self._id_map[idx]
                    if doc_id is not None:
                        results.append((doc_id, float(score)))
            return results[:limit]
        else:
            # 暴力搜索
            results = []
            for i, vec in enumerate(self._vectors):
                doc_id = self._id_map[i]
                if doc_id is None:
                    continue
                sim = float(np.dot(query_vec.flatten(), vec))
                results.append((doc_id, sim))
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:limit]

    def rebuild(self, documents: List[Tuple[str, np.ndarray]]):
        """根据 (id, vec) 列表重建索引"""
        self.__init__(self._dim, self._use_faiss)
        for doc_id, vec in documents:
            self.add(doc_id, vec)

    def __len__(self):
        return len([i for i in self._id_map if i is not None])


# ---------------------------------------------------------------------------
# LLM 评分器
# ---------------------------------------------------------------------------
class LLMRater:
    """调用 LLM 对检索结果进行精确相关性评分"""

    def __init__(self, config: Dict[str, Any]):
        self._cfg = config
        self._client: Optional["OpenAI"] = None

        if _HAS_OPENAI:
            self._client = OpenAI(
                api_key=config.get("api_key") or os.environ.get("OPENAI_API_KEY", ""),
                base_url=config.get("base_url"),
            )
        self._model = config.get("rater_model", "gpt-3.5-turbo")
        self._enabled = config.get("enable_llm_rater", True)

    def rate(self, query: str, candidates: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
        """对候选记忆评分，返回 (id, score)"""
        if not candidates or not self._client or not self._enabled:
            # 降级：返回原始向量分数
            return [(c["id"], c.get("score", 0)) for c in candidates]

        # 构建评分 prompt
        items_text = "\n".join(
            f"[{i}] {c['content'][:200]}"
            for i, c in enumerate(candidates)
        )
        prompt = (
            f"查询：{query}\n\n"
            f"候选记忆：\n{items_text}\n\n"
            f"请对每条候选记忆与查询的相关性打分（0-10 的整数），"
            f"格式：序号:分数，每行一条。只输出评分，不要解释。"
        )

        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
            )
            answer = resp.choices[0].message.content or ""
        except Exception:
            return [(c["id"], c.get("score", 0)) for c in candidates]

        # 解析评分
        scores: Dict[int, float] = {}
        for line in answer.strip().split("\n"):
            match = re.match(r"(\d+)\s*[:：]\s*(\d+)", line.strip())
            if match:
                idx = int(match.group(1))
                s = float(match.group(2)) / 10.0
                scores[idx] = s

        rated = []
        for i, c in enumerate(candidates):
            rated.append((c["id"], scores.get(i, c.get("score", 0))))
        return rated


# ---------------------------------------------------------------------------
# Memory6 实现
# ---------------------------------------------------------------------------
class Memory6(MemoryBackend):
    """基于 Embedding + FAISS + LLM 双路召回的三层记忆实现"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._cfg = config or {}

        # ---------- Embedding ----------
        self._embedder = EmbeddingEngine(self._cfg)

        # ---------- LLM Rater ----------
        self._rater = LLMRater(self._cfg)

        # ---------- 路径 ----------
        storage_dir = Path(self._cfg.get("storage_dir", "data/memory6"))
        storage_dir.mkdir(parents=True, exist_ok=True)
        self._data_file = storage_dir / "memory.json"
        self._backup_dir = storage_dir / "backups"
        self._backup_dir.mkdir(parents=True, exist_ok=True)

        # ---------- 容量 ----------
        self._working_max = int(self._cfg.get("working_max", 20))
        self._short_max   = int(self._cfg.get("short_max",   200))
        self._long_max    = int(self._cfg.get("long_max",    1000))

        # ---------- FAISS 索引 ----------
        dim = self._embedder.dim
        self._short_index = FaissIndex(dim)
        self._long_index  = FaissIndex(dim)

        # ---------- 数据 ----------
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    # =======================================================================
    # 持久化
    # =======================================================================
    def _load(self):
        if self._data_file.exists():
            try:
                with open(self._data_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._restore_from_backup()
        self._rebuild_indices()

    def _save(self):
        tmp = self._data_file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._data_file)
        except IOError:
            if tmp.exists():
                tmp.unlink()

        # 滚动备份
        backups = sorted(self._backup_dir.glob("memory_*.json"))
        if len(backups) >= 5:
            for old in backups[:-4]:
                old.unlink()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        try:
            shutil.copy2(self._data_file, self._backup_dir / f"memory_{stamp}.json")
        except IOError:
            pass

    def _restore_from_backup(self):
        backups = sorted(self._backup_dir.glob("memory_*.json"), reverse=True)
        for b in backups:
            try:
                with open(b, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                return
            except (json.JSONDecodeError, IOError):
                continue
        self._data = {}

    def _rebuild_indices(self):
        short_docs = []
        long_docs = []
        for mid, mem in self._data.items():
            layer = mem.get("layer", "short")
            vec_data = mem.get("vector")
            if vec_data is None:
                continue
            vec = np.array(vec_data, dtype=np.float32)
            if layer == "short":
                short_docs.append((mid, vec))
            elif layer == "long":
                long_docs.append((mid, vec))

        self._short_index.rebuild(short_docs)
        self._long_index.rebuild(long_docs)

    # =======================================================================
    # 记忆操作
    # =======================================================================
    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:12]

    def add_memory(self, content: str, **kwargs) -> str:
        mid = self._generate_id()
        now = time.time()
        layer = kwargs.get("layer", "working")
        importance = float(kwargs.get("importance", 1.0))
        tags = kwargs.get("tags", [])

        # 计算 Embedding
        vec = self._embedder.embed_single(content)

        mem = {
            "id":           mid,
            "content":      content,
            "layer":        layer,
            "created_at":   now,
            "accessed_at":  now,
            "access_count": 0,
            "importance":   importance,
            "tags":         list(tags),
            "metadata":     kwargs.get("metadata", {}),
            "vector":       vec.tolist(),   # JSON 可序列化
        }
        self._data[mid] = mem

        if layer == "short":
            self._short_index.add(mid, vec)
        elif layer == "long":
            self._long_index.add(mid, vec)

        self._save()

        if layer == "working":
            self._promote_working()

        return mid

    def _promote_working(self):
        working = [m for m in self._data.values() if m["layer"] == "working"]
        if len(working) <= self._working_max:
            return

        working.sort(key=lambda m: m["accessed_at"])
        overflow = working[: len(working) - self._working_max]
        for mem in overflow:
            mem["layer"] = "short"
            vec = np.array(mem["vector"], dtype=np.float32)
            self._short_index.add(mem["id"], vec)

        self._save()

    def search_memory(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        query_vec = self._embedder.embed_single(query)
        now = time.time()

        # 时间衰减参数
        decay_half = float(self._cfg.get("time_decay_hours", 24)) * 3600
        embedding_weight = float(self._cfg.get("embedding_weight", 0.6))
        time_weight      = float(self._cfg.get("time_weight",      0.2))
        importance_weight = float(self._cfg.get("importance_weight", 0.2))

        def _decay_score(raw_score: float, mem: Dict[str, Any]) -> float:
            """混合打分：向量相似度 + 时间衰减 + 重要性"""
            age = now - mem.get("accessed_at", mem["created_at"])
            time_factor = math.exp(-math.log(2) * age / decay_half) if decay_half > 0 else 1.0
            imp = mem.get("importance", 1.0)
            return (
                embedding_weight * raw_score +
                time_weight      * time_factor +
                importance_weight * imp
            )

        # 1. 工作记忆：Jaccard
        results: List[Dict[str, Any]] = []
        working = [m for m in self._data.values() if m["layer"] == "working"]
        for mem in working:
            sim = _jaccard_similarity(query, mem["content"])
            if sim > 0:
                results.append({
                    "id":      mem["id"],
                    "content": mem["content"],
                    "score":   sim,
                    "layer":   "working",
                })

        # 2. 短期记忆：FAISS
        for mid, score in self._short_index.search(query_vec, limit):
            mem = self._data.get(mid)
            if mem:
                results.append({
                    "id":      mem["id"],
                    "content": mem["content"],
                    "score":   score,
                    "layer":   "short",
                })

        # 3. 长期记忆：FAISS
        for mid, score in self._long_index.search(query_vec, limit):
            mem = self._data.get(mid)
            if mem:
                results.append({
                    "id":      mem["id"],
                    "content": mem["content"],
                    "score":   score * 0.8,
                    "layer":   "long",
                })

        # 去重
        seen = set()
        unique = []
        for r in sorted(results, key=lambda x: x["score"], reverse=True):
            if r["id"] not in seen:
                seen.add(r["id"])
                unique.append(r)

        # LLM 精排（对前 20 条候选）
        if self._rater._enabled and len(unique) > limit:
            llm_scores = self._rater.rate(query, unique[:20])
            llm_map = dict(llm_scores)
            for r in unique:
                r["score"] = llm_map.get(r["id"], r["score"])
            unique.sort(key=lambda x: x["score"], reverse=True)
        else:
            # 混合打分
            for r in unique:
                mem = self._data.get(r["id"])
                if mem:
                    r["score"] = _decay_score(r["score"], mem)
            unique.sort(key=lambda x: x["score"], reverse=True)

        # 更新访问记录
        for r in unique[:limit]:
            mem = self._data.get(r["id"])
            if mem:
                mem["accessed_at"] = now
                mem["access_count"] = mem.get("access_count", 0) + 1

        return unique[:limit]

    def get_all_memories(self) -> List[Dict[str, Any]]:
        return list(self._data.values())

    def delete_memory(self, memory_id: str) -> bool:
        mem = self._data.pop(memory_id, None)
        if mem is None:
            return False

        if mem["layer"] == "short":
            self._short_index.remove(memory_id)
        elif mem["layer"] == "long":
            self._long_index.remove(memory_id)

        self._save()
        return True

    def clear_all(self) -> None:
        self._data.clear()
        self._short_index.rebuild([])
        self._long_index.rebuild([])
        self._save()
        # 删除所有备份文件
        for bak in self._backup_dir.glob("memory_*.json"):
            try:
                bak.unlink()
            except OSError:
                pass

    def get_working_context(self) -> str:
        working = [m for m in self._data.values() if m["layer"] == "working"]
        if not working:
            return ""

        working.sort(key=lambda m: m["accessed_at"], reverse=True)
        lines = ["## 工作记忆 (Working Memory)"]
        for i, mem in enumerate(working, 1):
            tags_str = f" [{', '.join(mem.get('tags', []))}]" if mem.get("tags") else ""
            lines.append(f"{i}.{tags_str} {mem['content']}")
        return "\n".join(lines)

    def compress(self) -> int:
        short = [m for m in self._data.values() if m["layer"] == "short"]
        overflow = len(short) - self._short_max
        if overflow <= 0:
            return 0

        short.sort(key=lambda m: m["accessed_at"])
        compressed = 0
        for mem in short[:overflow]:
            mem["layer"] = "long"
            vec = np.array(mem["vector"], dtype=np.float32)
            self._short_index.remove(mem["id"])
            self._long_index.add(mem["id"], vec)
            compressed += 1

        self._save()
        return compressed

    def forget_outdated(self) -> int:
        """基于遗忘曲线清理过期记忆

        使用 Ebbinghaus 遗忘曲线近似：
            retention = exp(-age / half_life)
        当 retention 低于阈值时删除。
        """
        now = time.time()
        working_half  = float(self._cfg.get("working_half_hours",  1))  * 3600
        short_half    = float(self._cfg.get("short_half_hours",    24)) * 3600
        long_half     = float(self._cfg.get("long_half_hours",     168)) * 3600  # 7 天
        retention_threshold = float(self._cfg.get("retention_threshold", 0.05))

        layer_half = {
            "working": working_half,
            "short":   short_half,
            "long":    long_half,
        }

        to_delete = []
        for mid, mem in self._data.items():
            layer = mem.get("layer", "short")
            half = layer_half.get(layer, short_half)
            age = now - mem.get("accessed_at", mem["created_at"])
            retention = math.exp(-math.log(2) * age / half) if half > 0 else 1.0

            # 结合重要性调整：高重要性记忆更难遗忘
            imp = mem.get("importance", 1.0)
            adjusted_retention = retention * math.log(1 + imp)

            if adjusted_retention < retention_threshold:
                to_delete.append(mid)

        for mid in to_delete:
            self.delete_memory(mid)

        return len(to_delete)