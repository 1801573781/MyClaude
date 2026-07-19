"""记忆召回模块。

对应设计文档第二章。

召回策略：索引层按需注入 + 主题文件懒加载。

职责：
- 读取 Layer 1（MEMORY.md）内容供注入
- 解析 Layer 1 中的主题文件指针
- 利用倒排索引搜索 Layer 0 原始数据（回退机制）
- 提供搜索接口供 CLI /mem search 命令调用
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryRetriever:
    """记忆召回器。

    正常情况下召回方只读 Layer 1 + Layer 2。
    如果 LLM 发现索引层信息不够，可以主动搜索 Layer 0 原始数据。
    """

    def __init__(self, mem_config: Any, store: Any):
        """初始化召回器。

        Args:
            mem_config: memory_ex.yaml 配置对象
            store: MemoryStore 实例
        """
        self._store = store

        retrieval_config = mem_config.retrieval
        self._default_top_k = int(getattr(retrieval_config, "default_top_k", 5))
        self._max_top_k = int(getattr(retrieval_config, "max_top_k", 20))

        injection_config = mem_config.injection
        self._max_injection_tokens = int(
            getattr(injection_config, "max_tokens", 2000)
        )

    def get_layer1_content(self) -> str:
        """读取 Layer 1（MEMORY.md）内容。

        这是召回的主路径——Layer 1 始终注入到 api_messages 的固定区末尾。

        Returns:
            Layer 1 的 Markdown 内容，空字符串表示无内容
        """
        return self._store.read_layer1()

    def get_layer1_stats(self) -> Dict[str, int]:
        """获取 Layer 1 的行数和 token 估算。"""
        return self._store.get_layer1_stats()

    def parse_topic_pointers(self, layer1_content: str) -> List[Dict]:
        """解析 Layer 1 中的主题文件指针。

        指针格式：- [主题标签] 一句话概括 → 详见 topics/{topic_slug}.md

        Args:
            layer1_content: Layer 1 的 Markdown 内容

        Returns:
            指针信息列表，每个元素含 tag, summary, filename
        """
        pointers = []
        if not layer1_content:
            return pointers

        # 匹配指针行
        # 格式: - [标签] 概括 → 详见 topics/xxx.md
        pattern = re.compile(
            r"^-\s*\[([^\]]+)\]\s*(.+?)\s*→\s*详见\s*topics/(\S+\.md)",
            re.MULTILINE,
        )

        for match in pattern.finditer(layer1_content):
            tag = match.group(1).strip()
            summary = match.group(2).strip()
            filename = match.group(3).strip()
            pointers.append({
                "tag": tag,
                "summary": summary,
                "filename": filename,
            })

        return pointers

    def get_topic_file_content(self, filename: str) -> str:
        """读取 Layer 2 主题文件内容（懒加载）。

        LLM 判断需要某主题细节时，通过 file_view 或此方法读取。

        Args:
            filename: 主题文件名（如 "database.md"）

        Returns:
            主题文件内容，空字符串表示文件不存在
        """
        return self._store.read_topic_file(filename)

    def search(self, query: str, top_k: int = None, **filters) -> List[Dict]:
        """搜索 Layer 0 + Layer 2。

        供 CLI /mem search 命令调用。

        利用倒排索引进行快速定位，避免全量扫描 JSONL。

        Args:
            query: 搜索关键词
            top_k: 返回的最大条目数
            **filters: 过滤条件（如 tags=["数据库"]）

        Returns:
            匹配的记忆条目列表
        """
        if top_k is None:
            top_k = self._default_top_k
        top_k = min(top_k, self._max_top_k)

        # 提取搜索关键词
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        # 1. 通过倒排索引查找匹配的条目 ID
        matched_ids = self._store.search_inverted_index(keywords)

        # 2. 精准读取匹配的 Layer 0 条目
        results = []
        all_entries = self._store.iter_layer0()
        id_set = set(matched_ids)

        for entry in all_entries:
            if entry.get("id") in id_set:
                # 应用过滤条件
                if self._matches_filters(entry, filters):
                    results.append(entry)

            # 也做内容匹配（补充倒排索引的遗漏）
            if len(results) < top_k and entry.get("id") not in id_set:
                content = entry.get("content", "")
                if any(kw.lower() in content.lower() for kw in keywords):
                    if self._matches_filters(entry, filters):
                        if entry not in results:
                            results.append(entry)

            if len(results) >= top_k:
                break

        # 3. 也搜索 Layer 2 主题文件
        topic_results = self._search_topic_files(keywords, top_k - len(results))
        results.extend(topic_results)

        return results[:top_k]

    def search_layer0_by_keywords(self, keywords: List[str]) -> List[Dict]:
        """通过关键词搜索 Layer 0 原始数据（回退机制）。

        当 LLM 发现索引层信息不够时，可主动搜索 Layer 0。

        Args:
            keywords: 关键词列表

        Returns:
            匹配的 Layer 0 条目列表
        """
        if not keywords:
            return []

        # 优先使用倒排索引
        matched_ids = self._store.search_inverted_index(keywords)
        if matched_ids:
            all_entries = self._store.iter_layer0()
            id_set = set(matched_ids)
            return [e for e in all_entries if e.get("id") in id_set]

        # 降级：全量扫描
        results = []
        for entry in self._store.iter_layer0():
            content = entry.get("content", "")
            tags = entry.get("tags", [])
            searchable_text = content + " " + " ".join(tags)

            if any(kw.lower() in searchable_text.lower() for kw in keywords):
                results.append(entry)

        return results

    # ===== 辅助方法 =====

    def _extract_keywords(self, query: str) -> List[str]:
        """从查询文本中提取关键词。

        简化实现：按空格分词，过滤停用词和过短词。
        """
        if not query:
            return []

        # 中文按字符分割，英文按空格分词
        # 简化：直接按空格和标点分词
        raw_words = re.split(r"[\s,，。、；;：:！!？?()（）\[\]]+", query)
        keywords = [w.strip() for w in raw_words if w.strip() and len(w.strip()) >= 2]

        # 停用词过滤
        stop_words = {"的", "了", "是", "在", "我", "你", "他", "她", "它", "这", "那"}
        keywords = [w for w in keywords if w.lower() not in stop_words]

        return keywords

    def _matches_filters(self, entry: Dict, filters: Dict) -> bool:
        """检查条目是否匹配过滤条件。"""
        for key, value in filters.items():
            if key == "tags":
                entry_tags = set(entry.get("tags", []))
                if isinstance(value, list):
                    if not set(value).intersection(entry_tags):
                        return False
                elif value not in entry_tags:
                    return False
            elif key == "status":
                if entry.get("status") != value:
                    return False
            elif key == "session_id":
                if entry.get("session_id") != value:
                    return False
            elif key == "query_id":
                if entry.get("query_id") != value:
                    return False

        return True

    def _search_topic_files(
        self, keywords: List[str], max_results: int
    ) -> List[Dict]:
        """搜索 Layer 2 主题文件。

        Args:
            keywords: 关键词列表
            max_results: 最大返回数

        Returns:
            匹配的主题文件内容条目
        """
        if max_results <= 0:
            return []

        results = []
        topic_files = self._store.list_topic_files()

        for filename in topic_files:
            content = self._store.read_topic_file(filename)
            if not content:
                continue

            # 检查是否包含关键词
            if any(kw.lower() in content.lower() for kw in keywords):
                # 解析主题文件中的条目
                for line in content.split("\n"):
                    line = line.strip()
                    if line.startswith("- ") and any(
                        kw.lower() in line.lower() for kw in keywords
                    ):
                        results.append({
                            "id": "",
                            "source": "topic_file",
                            "tags": [filename.replace(".md", "")],
                            "content": line,
                            "filename": filename,
                        })
                        if len(results) >= max_results:
                            return results

        return results
