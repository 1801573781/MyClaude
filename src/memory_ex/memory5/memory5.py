"""memory5 核心实现 —— 基于 TF-IDF + 余弦相似度检索

继承 MemoryBackend，实现三层记忆（Working / Short-term / Long-term）。
参考 src/memory 的实现风格重新编写，保持行为语义一致。
"""

import json
import math
import os
import re
import shutil
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.memory_ex.memory_interface import MemoryBackend


# ---------------------------------------------------------------------------
# 中文 / 英文 简单分词
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> List[str]:
    """将文本切分为 token 列表（中英文混合简单分词）"""
    # 匹配中文字符、英文单词、数字
    tokens = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z]+|\d+", text.lower())
    return tokens


# ---------------------------------------------------------------------------
# TF-IDF 计算
# ---------------------------------------------------------------------------
class TfidfIndex:
    """轻量级 TF-IDF 索引，用于短期/长期记忆检索"""

    def __init__(self):
        self._docs: List[str] = []        # 文档原文
        self._doc_ids: List[str] = []     # 对应记忆 ID
        self._df: Counter = Counter()     # 文档频率
        self._tfidf_vecs: List[Dict[str, float]] = []

    def add_document(self, doc_id: str, content: str):
        """添加一篇文档到索引"""
        tokens = _tokenize(content)
        tf = Counter(tokens)
        if len(tokens) == 0:
            return

        # 更新 DF
        for word in set(tf.keys()):
            self._df[word] += 1

        self._docs.append(content)
        self._doc_ids.append(doc_id)
        self._tfidf_vecs.append(tf)

    def remove_document(self, doc_id: str):
        """从索引中移除一篇文档"""
        if doc_id not in self._doc_ids:
            return
        idx = self._doc_ids.index(doc_id)
        tokens = _tokenize(self._docs[idx])

        # 回退 DF
        for word in set(tokens):
            self._df[word] = max(0, self._df.get(word, 1) - 1)
            if self._df[word] == 0:
                del self._df[word]

        del self._docs[idx]
        del self._doc_ids[idx]
        del self._tfidf_vecs[idx]

    def rebuild(self, documents: List[Tuple[str, str]]):
        """根据 (id, content) 列表重建整个索引"""
        self.__init__()
        for doc_id, content in documents:
            self.add_document(doc_id, content)

    def search(self, query: str, limit: int = 5) -> List[Tuple[str, float]]:
        """搜索并返回 (doc_id, score) 列表，按分数降序"""
        query_tokens = _tokenize(query)
        if not query_tokens or not self._docs:
            return []

        N = len(self._docs)
        query_tf = Counter(query_tokens)

        # 计算 query TF-IDF
        query_tfidf: Dict[str, float] = {}
        for word, count in query_tf.items():
            df = self._df.get(word, 0)
            idf = math.log((N + 1) / (df + 1)) + 1.0
            query_tfidf[word] = count * idf

        norm_q = math.sqrt(sum(v ** 2 for v in query_tfidf.values()))
        if norm_q == 0:
            return []

        scores: List[Tuple[str, float]] = []
        for i, doc_vec in enumerate(self._tfidf_vecs):
            dot = 0.0
            norm_d = 0.0
            for word, count in doc_vec.items():
                idf = math.log((N + 1) / (self._df.get(word, 0) + 1)) + 1.0
                tfidf = count * idf
                norm_d += tfidf ** 2
                dot += query_tfidf.get(word, 0) * tfidf

            norm_d = math.sqrt(norm_d)
            if norm_d == 0:
                continue
            sim = dot / (norm_q * norm_d)
            if sim > 0:
                scores.append((self._doc_ids[i], sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:limit]


# ---------------------------------------------------------------------------
# Jaccard 相似度
# ---------------------------------------------------------------------------
def _jaccard_similarity(a: str, b: str) -> float:
    """计算两段文本的 Jaccard 相似度（基于 token 集合）"""
    set_a = set(_tokenize(a))
    set_b = set(_tokenize(b))
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Memory5 实现
# ---------------------------------------------------------------------------
class Memory5(MemoryBackend):
    """基于 TF-IDF + 余弦相似度的三层记忆实现

    记忆分层：
        - working:  工作记忆（最近高频交互，Jaccard 相似度检索）
        - short:    短期记忆（TF-IDF 检索，容量超限触发压缩）
        - long:     长期记忆（TF-IDF 检索，压缩/遗忘管理）
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._cfg = config or {}

        # ---------- 路径 ----------
        storage_dir = Path(self._cfg.get("storage_dir", "data/memory5"))
        storage_dir.mkdir(parents=True, exist_ok=True)
        self._data_file = storage_dir / "memory.json"
        self._backup_dir = storage_dir / "backups"
        self._backup_dir.mkdir(parents=True, exist_ok=True)

        # ---------- 容量参数 ----------
        self._working_max = int(self._cfg.get("working_max", 20))
        self._short_max   = int(self._cfg.get("short_max",   200))
        self._long_max    = int(self._cfg.get("long_max",    1000))

        # ---------- 索引 ----------
        self._short_index = TfidfIndex()
        self._long_index  = TfidfIndex()

        # ---------- 记忆存储 ----------
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    # =======================================================================
    # 持久化
    # =======================================================================
    def _load(self):
        """从 JSON 文件加载记忆，失败则从备份恢复"""
        if self._data_file.exists():
            try:
                with open(self._data_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._restore_from_backup()
        self._rebuild_indices()

    def _save(self):
        """原子写入：先写临时文件再替换，失败时回滚"""
        tmp = self._data_file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._data_file)
        except IOError:
            if tmp.exists():
                tmp.unlink()

        # 滚动备份（保留最近 5 份）
        backups = sorted(self._backup_dir.glob("memory_*.json"))
        if len(backups) >= 5:
            for old in backups[:-4]:
                old.unlink()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = self._backup_dir / f"memory_{stamp}.json"
        try:
            shutil.copy2(self._data_file, backup)
        except IOError:
            pass

    def _restore_from_backup(self):
        """从最近备份恢复数据"""
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
        """根据当前数据重建 TF-IDF 索引"""
        short_docs = []
        long_docs = []
        for _mid, mem in self._data.items():
            layer = mem.get("layer", "short")
            if layer == "short":
                short_docs.append((_mid, mem.get("content", "")))
            elif layer == "long":
                long_docs.append((_mid, mem.get("content", "")))

        self._short_index.rebuild(short_docs)
        self._long_index.rebuild(long_docs)

    # =======================================================================
    # 记忆生命周期
    # =======================================================================
    @staticmethod
    def _now_iso() -> str:
        """返回当前时间的 ISO 格式字符串（人类可读）。"""
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    @staticmethod
    def _iso_to_epoch(iso_str) -> float:
        """将 ISO 时间字符串转换为 epoch 时间（兼容旧版数值时间戳）。"""
        if isinstance(iso_str, (int, float)):
            return float(iso_str)
        try:
            return time.mktime(time.strptime(str(iso_str), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, TypeError):
            return time.time()

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:12]

    def add_memory(self, content: str, **kwargs) -> str:
        mid = self._generate_id()
        now_iso = self._now_iso()
        importance = float(kwargs.get("importance", 1.0))
        layer = kwargs.get("layer", "working")
        tags = kwargs.get("tags", [])

        mem = {
            "id":          mid,
            "content":     content,
            "layer":       layer,
            "created_at":  now_iso,
            "accessed_at": now_iso,
            "access_count": 0,
            "importance":  importance,
            "tags":        list(tags),
            "metadata":    kwargs.get("metadata", {}),
        }
        self._data[mid] = mem

        # 如果直接写入 short/long，更新对应索引
        if layer == "short":
            self._short_index.add_document(mid, content)
        elif layer == "long":
            self._long_index.add_document(mid, content)

        self._save()

        # 检查工作记忆是否需要提升
        if layer == "working":
            self._promote_working()

        return mid

    def _promote_working(self):
        """将超过容量的工作记忆晋升为短期记忆"""
        working = [m for m in self._data.values() if m["layer"] == "working"]
        if len(working) <= self._working_max:
            return

        # 按访问时间升序，最早访问的优先晋升
        working.sort(key=lambda m: self._iso_to_epoch(m["accessed_at"]))
        overflow = working[: len(working) - self._working_max]
        for mem in overflow:
            mem["layer"] = "short"
            self._short_index.add_document(mem["id"], mem["content"])

        self._save()

    def promote_all_working(self):
        """将会话结束时所有工作记忆晋升为短期记忆，并清理工作记忆。"""
        working = [m for m in self._data.values() if m["layer"] == "working"]
        for mem in working:
            mem["layer"] = "short"
            mem["accessed_at"] = self._now_iso()
            self._short_index.add_document(mem["id"], mem["content"])

        if working:
            self._save()

        return len(working)

    def search_memory(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        # 1. 工作记忆：Jaccard 相似度
        working = [m for m in self._data.values() if m["layer"] == "working"]
        for mem in working:
            sim = _jaccard_similarity(query, mem["content"])
            if sim > 0:
                results.append({
                    "id":         mem["id"],
                    "content":    mem["content"],
                    "score":      sim,
                    "layer":      "working",
                    "created_at": mem.get("created_at", ""),
                    "accessed_at": mem.get("accessed_at", ""),
                })

        # 2. 短期记忆：TF-IDF
        for mid, score in self._short_index.search(query, limit):
            mem = self._data.get(mid)
            if mem:
                results.append({
                    "id":         mem["id"],
                    "content":    mem["content"],
                    "score":      score,
                    "layer":      "short",
                    "created_at": mem.get("created_at", ""),
                    "accessed_at": mem.get("accessed_at", ""),
                })

        # 3. 长期记忆：TF-IDF
        for mid, score in self._long_index.search(query, limit):
            mem = self._data.get(mid)
            if mem:
                results.append({
                    "id":      mem["id"],
                    "content": mem["content"],
                    "score":   score * 0.8,  # 长期记忆权重略低
                    "layer":   "long",
                })

        # 去重 & 排序
        seen = set()
        unique: List[Dict[str, Any]] = []
        for r in sorted(results, key=lambda x: x["score"], reverse=True):
            if r["id"] not in seen:
                seen.add(r["id"])
                unique.append(r)

        # 更新访问记录
        for r in unique[:limit]:
            mem = self._data.get(r["id"])
            if mem:
                mem["accessed_at"] = self._now_iso()
                mem["access_count"] = mem.get("access_count", 0) + 1

        return unique[:limit]

    def get_all_memories(self) -> List[Dict[str, Any]]:
        return list(self._data.values())

    def delete_memory(self, memory_id: str) -> bool:
        mem = self._data.pop(memory_id, None)
        if mem is None:
            return False

        if mem["layer"] == "short":
            self._short_index.remove_document(memory_id)
        elif mem["layer"] == "long":
            self._long_index.remove_document(memory_id)

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
        """将工作记忆格式化为 LLM 可注入文本"""
        working = [m for m in self._data.values() if m["layer"] == "working"]
        if not working:
            return ""

        # 按访问时间倒序，最近优先
        working.sort(key=lambda m: m["accessed_at"], reverse=True)

        lines = ["## 工作记忆 (Working Memory)"]
        for i, mem in enumerate(working, 1):
            tags_str = f" [{', '.join(mem.get('tags', []))}]" if mem.get("tags") else ""
            lines.append(f"{i}.{tags_str} {mem['content']}")
        return "\n".join(lines)

    def compress(self) -> int:
        """将超量短期记忆批量压缩为长期记忆"""
        short = [m for m in self._data.values() if m["layer"] == "short"]
        overflow = len(short) - self._short_max
        if overflow <= 0:
            return 0

        # 按访问时间升序，最早访问的优先压缩
        short.sort(key=lambda m: self._iso_to_epoch(m["accessed_at"]))
        compressed = 0
        for mem in short[:overflow]:
            mem["layer"] = "long"
            self._short_index.remove_document(mem["id"])
            self._long_index.add_document(mem["id"], mem["content"])
            compressed += 1

        self._save()
        return compressed

    def forget_outdated(self) -> int:
        """基于时间衰减清理过期记忆

        遗忘策略：
            - 工作记忆超过 1 小时未访问 → 删除
            - 短期记忆超过 24 小时未访问 → 删除
            - 长期记忆重要性低于阈值 + 超过 7 天未访问 → 删除
        """
        now = time.time()
        working_ttl = float(self._cfg.get("working_ttl_seconds", 3600))     # 1 小时
        short_ttl   = float(self._cfg.get("short_ttl_seconds",   86400))   # 24 小时
        long_ttl    = float(self._cfg.get("long_ttl_seconds",    604800))  # 7 天
        importance_threshold = float(self._cfg.get("importance_threshold", 0.1))

        to_delete: List[str] = []
        for mid, mem in self._data.items():
            accessed_epoch = self._iso_to_epoch(
                mem.get("accessed_at", mem.get("created_at", 0))
            )
            age = now - accessed_epoch
            layer = mem.get("layer", "short")

            if layer == "working" and age > working_ttl:
                to_delete.append(mid)
            elif layer == "short" and age > short_ttl:
                to_delete.append(mid)
            elif layer == "long" and age > long_ttl:
                imp = mem.get("importance", 1.0)
                if imp < importance_threshold:
                    to_delete.append(mid)

        for mid in to_delete:
            self.delete_memory(mid)

        return len(to_delete)