"""记忆召回模块。

召回策略：Layer 1 按需注入 + 倒排索引搜索。

职责：
- 读取 Layer 1（MEMORY.md）内容供注入
- 利用倒排索引搜索 Layer 1 数据
- 提供搜索接口供 CLI /mem search 命令调用
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Prompt 模板路径
_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """加载 Prompt 模板文件。"""
    try:
        return (_PROMPT_DIR / filename).read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(f"Prompt 模板未找到: {filename}")
        return ""


class MemoryRetriever:
    """记忆召回器。

    正常情况下召回方只读 Layer 1 + Layer 2。
    如果 LLM 发现索引层信息不够，可以主动搜索 Layer 0 原始数据。

    召回策略：
    - retrieve_for_query(): LLM 预检索，根据查询相关性筛选 Layer 1 条目
    - get_layer1_content(): 全量返回（向后兼容）
    - search(): 关键词搜索（CLI 手动调用）
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
        self._timeout = int(getattr(retrieval_config, "timeout", 30))

        injection_config = mem_config.injection
        self._max_injection_tokens = int(
            getattr(injection_config, "max_tokens", 2000)
        )

        # LLM 调用函数（延迟注入）
        self._llm_chat_fn = None

    def set_llm_chat_fn(self, fn):
        """注入 LLM 调用函数。"""
        self._llm_chat_fn = fn

    def retrieve_for_query(self, query: str, exclude_session_id: str = "") -> List[Dict[str, Any]]:
        """根据查询相关性筛选 Layer 1 记忆（LLM 预检索）。

        将 Layer 1 条目和用户查询发给 LLM，由 LLM 判断相关性并返回编号列表。
        原则：宁可不召回也不瞎召回。LLM 不可用、超时、返回 NONE 时一律返回空列表。

        Args:
            query: 增强后的用户查询（可能含文件内容）
            exclude_session_id: 需要排除的 session_id（当前会话），

        Returns:
            筛选后的记忆条目列表，每个元素含 id, session_id, tags, content, raw_line。
            空列表表示无相关记忆或召回失败。
        """
        layer1_content = self._store.read_layer1()
        if not layer1_content or not layer1_content.strip():
            return []

        all_entries = self._parse_layer1_entries(layer1_content)
        if not all_entries:
            return []

        # 显式过滤：排除当前 session 的记忆，确保不会召回本 session 产生的记忆
        if exclude_session_id:
            entries = [
                e for e in all_entries
                if e.get("session_id", "") != exclude_session_id
            ]
            excluded_count = len(all_entries) - len(entries)
            if excluded_count > 0:
                logger.info(f"已过滤当前 session 记忆 {excluded_count} 条")
        else:
            entries = all_entries

        if not entries:
            return []

        # 无 LLM 函数时不召回
        if not self._llm_chat_fn:
            logger.info("LLM 调用函数未注入，跳过召回")
            return []

        # 构建预检索 Prompt
        prompt = self._build_retrieval_prompt(query, entries)
        if not prompt:
            return []

        # 调用 LLM 筛选
        try:
            response = self._call_llm_with_timeout(prompt, timeout=self._timeout)
            if response is None:
                logger.warning("LLM 预检索超时，跳过召回")
                return []

            selected_indices = self._parse_retrieval_response(response, len(entries))

            if not selected_indices:
                # LLM 返回 NONE 或解析失败，不召回
                logger.info("LLM 预检索无匹配，不召回")
                return []

            selected = [entries[i] for i in selected_indices if i < len(entries)]
            logger.info(f"LLM 预检索命中 {len(selected)}/{len(entries)} 条记忆")
            return selected

        except Exception as e:
            logger.error(f"LLM 预检索失败: {e}，跳过召回")
            return []

    def _parse_layer1_entries(self, layer1_content: str) -> List[Dict[str, Any]]:
        """解析 Layer 1 内容为结构化条目列表。

        Args:
            layer1_content: MEMORY.md 的原始内容

        Returns:
            条目列表，每个元素含 id, tags, content, raw_line
        """
        entries = []
        for line in layer1_content.split("\n"):
            line = line.strip()
            if not line.startswith("- "):
                continue

            # 解析格式: - [tag1][tag2] content (id=xxx) (session=yyy)
            raw_line = line

            # 提取 ID
            id_match = re.search(r"\(id=([^)]+)\)", line)
            entry_id = id_match.group(1) if id_match else ""

            # 提取 session_id
            session_match = re.search(r"\(session=([^)]+)\)", line)
            session_id = session_match.group(1) if session_match else ""

            # 提取标签
            tags = re.findall(r"\[([^\]]+)\]", line)
            # 过滤掉 id 标签
            tags = [t for t in tags if not t.startswith("id=")]

            # 提取内容（去掉前导 "- " 和所有 [tag] 和 (id=...) 和 (session=...)）
            content = re.sub(r"^\-\s+", "", line)
            content = re.sub(r"\[[^\]]+\]", "", content).strip()
            content = re.sub(r"\(id=[^)]+\)", "", content).strip()
            content = re.sub(r"\(session=[^)]+\)", "", content).strip()

            entries.append({
                "id": entry_id,
                "session_id": session_id,
                "tags": tags,
                "content": content,
                "raw_line": raw_line,
            })

        return entries

    def _build_retrieval_prompt(self, query: str, entries: List[Dict]) -> str:
        """构建预检索 Prompt。

        Args:
            query: 增强后的用户查询
            entries: Layer 1 条目列表

        Returns:
            完整的预检索 Prompt 字符串
        """
        prompt_template = _load_prompt("retrieval_prompt.txt")
        if not prompt_template:
            logger.warning("retrieval_prompt.txt 未找到，跳过 LLM 预检索")
            return ""

        # 构建记忆列表（带编号）
        memory_lines = []
        for i, entry in enumerate(entries, 1):
            tags_str = "".join(f"[{t}]" for t in entry.get("tags", []))
            memory_lines.append(f"{i}. {tags_str} {entry.get('content', '')}")

        memories_text = "\n".join(memory_lines)

        prompt = prompt_template.replace("{query}", query)
        prompt = prompt.replace("{memories}", memories_text)

        return prompt

    def _parse_retrieval_response(self, response: str, total: int) -> List[int]:
        """解析 LLM 预检索响应，支持 SCORED 和旧版 RELATED 格式。

        Args:
            response: LLM 响应文本
            total: 总条目数（用于边界检查）

        Returns:
            选中的条目编号列表（0-based 索引），按分数降序排列
        """
        if not response:
            return []

        response = response.strip()

        # 检查 NONE
        if response.upper().startswith("NONE"):
            return []

        # 匹配 SCORED: 1:9, 3:8, 5:7（新格式）
        scored_match = re.match(r"SCORED:\s*([\d:,\s]+)", response, re.IGNORECASE)
        if scored_match:
            scored_str = scored_match.group(1)
            scored_entries = []
            for part in scored_str.split(","):
                part = part.strip()
                if ":" in part:
                    num_str, score_str = part.split(":", 1)
                    try:
                        num = int(num_str.strip())
                        score = int(score_str.strip())
                        if 1 <= num <= total and score >= 7:
                            scored_entries.append((num - 1, score))
                    except ValueError:
                        continue

            # 按分数降序排序，最多取 5 条
            scored_entries.sort(key=lambda x: x[1], reverse=True)
            return [idx for idx, _ in scored_entries[:5]]

        # 兼容旧格式 RELATED: 1,3,5
        related_match = re.match(r"RELATED:\s*([\d,\s]+)", response, re.IGNORECASE)
        if related_match:
            numbers_str = related_match.group(1)
            numbers = [int(n.strip()) for n in numbers_str.split(",") if n.strip().isdigit()]
            indices = [n - 1 for n in numbers if 1 <= n <= total]
            return indices[:5]

        logger.warning(f"无法解析预检索响应: {response[:100]}")
        return []

    def _call_llm_with_timeout(self, prompt: str, timeout: int = 30) -> Optional[str]:
        """调用 LLM，带超时保护。

        直接将 timeout 传递给 simple_chat，由 httpx 在 HTTP 层处理超时，
        确保超时后连接被正确关闭，避免线程泄漏和僵尸连接。

        Args:
            prompt: 完整 Prompt
            timeout: 超时秒数

        Returns:
            LLM 响应文本，None 表示超时
        """
        import time

        try:
            start = time.time()
            response = self._llm_chat_fn(
                prompt,
                temperature=0.1,
                max_tokens=10240,
                timeout=float(timeout),
            )
            elapsed = time.time() - start

            if not response:
                if elapsed >= timeout * 0.9:
                    logger.warning(f"LLM 预检索疑似超时（耗时 {elapsed:.1f}s，阈值 {timeout}s）")
                else:
                    logger.warning(f"LLM 预检索返回空响应（耗时 {elapsed:.1f}s）")
                return None

            logger.info(f"LLM 预检索成功（耗时 {elapsed:.1f}s）")
            return response
        except Exception as e:
            logger.error(f"LLM 预检索调用异常: {e}")
            return None

    def get_layer1_content(self) -> str:
        """读取 Layer 1（MEMORY.md）内容（全量，向后兼容）。

        新的召回主路径是 retrieve_for_query()，此方法保留供回退和调试使用。

        Returns:
            Layer 1 的 Markdown 内容，空字符串表示无内容
        """
        return self._store.read_layer1()

    def get_layer1_stats(self) -> Dict[str, int]:
        """获取 Layer 1 的行数和 token 估算。"""
        return self._store.get_layer1_stats()

    def search(self, query: str, top_k: int = None, **filters) -> List[Dict]:
        """搜索 Layer 1。

        供 CLI /mem search 命令调用。仅搜索 Layer 1（MEMORY.md）。

        利用倒排索引进行快速定位，再回退到内容匹配。

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

        # 2. 从 Layer 1 读取并匹配
        layer1_content = self._store.read_layer1()
        if not layer1_content:
            return []

        all_entries = self._parse_layer1_entries(layer1_content)
        id_set = set(matched_ids)

        results = []
        for entry in all_entries:
            entry_id = entry.get("id", "")
            content = entry.get("content", "")
            tags = entry.get("tags", [])

            # 倒排索引命中或内容匹配
            if entry_id in id_set or any(kw.lower() in content.lower() for kw in keywords):
                if self._matches_filters(entry, filters):
                    results.append(entry)

            if len(results) >= top_k:
                break

        return results[:top_k]

    def search_layer0_by_keywords(self, keywords: List[str]) -> List[Dict]:
        """通过关键词搜索（已废弃，保留仅为接口兼容）。

        Args:
            keywords: 关键词列表

        Returns:
            空列表
        """
        return []

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


