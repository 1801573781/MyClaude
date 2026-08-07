<![CDATA[
"""Rerank 模型客户端。

封装 rerank 模型 API 调用，输入查询 + 候选文档列表，返回带分数的排序结果。
通过 provider 引用 model_key.yaml 顶层配置，复用其 api_key、base_url、model_name。
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# model_key.yaml 路径
_MODEL_KEY_PATH = Path(__file__).parent.parent.parent.parent / "config" / "model_key.yaml"


def _load_provider_config(provider: str) -> Dict[str, Any]:
    """从 model_key.yaml 加载指定 provider 的配置。

    Args:
        provider: provider 名称（如 "GLM"、"DeepSeek"）

    Returns:
        包含 api_key、base_url、model_name 的字典
    """
    try:
        with open(_MODEL_KEY_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        provider_config = config.get(provider, {})
        if not provider_config:
            logger.error(f"model_key.yaml 中未找到 provider: {provider}")
        return provider_config
    except Exception as e:
        logger.error(f"加载 model_key.yaml 失败: {e}")
        return {}


class RerankClient:
    """Rerank 模型客户端。

    通过 provider 引用 model_key.yaml 顶层配置，复用其 api_key、base_url、model_name。

    Usage:
        client = RerankClient(provider="GLM")
        results = client.rerank("查询文本", ["doc1", "doc2", "doc3"], top_n=3)
    """

    def __init__(self, provider: str):
        """初始化 Rerank 客户端。

        Args:
            provider: provider 名称，引用 model_key.yaml 顶层配置，
                      复用其 api_key、base_url、model_name
        """
        self._provider = provider
        provider_config = _load_provider_config(provider)

        self._api_key: str = provider_config.get("api_key", "")
        self._base_url: str = provider_config.get("base_url", "")
        self._model_name: str = provider_config.get("model_name", "")

        if not self._api_key or not self._base_url:
            logger.error(
                f"RerankClient 初始化失败：provider '{provider}' 缺少 api_key 或 base_url"
            )

    def rerank(
        self,
        query: str,
        documents: List[str],
        top_n: int = 3,
        timeout: int = 30,
    ) -> List[Dict[str, Any]]:
        """调用 rerank API 对文档列表按相关性重排序。

        Args:
            query: 查询文本
            documents: 候选文档文本列表
            top_n: 返回前 N 条
            timeout: API 超时秒数

        Returns:
            [{"index": int, "score": float, "document": str}, ...]
            按 score 降序排列。空列表表示无结果或调用失败。
        """
        if not documents:
            return []

        if not self._api_key or not self._base_url:
            logger.error("RerankClient 未正确配置，跳过 rerank 调用")
            return []

        try:
            results = self._call_rerank_api(query, documents, top_n, timeout)
            return results
        except Exception as e:
            logger.error(f"Rerank API 调用失败: {e}")
            return []

    def _call_rerank_api(
        self,
        query: str,
        documents: List[str],
        top_n: int,
        timeout: int,
    ) -> List[Dict[str, Any]]:
        """实际调用 rerank API。

        以 GLM 为例，调用 POST {base_url}/rerank 接口。

        Args:
            query: 查询文本
            documents: 候选文档文本列表
            top_n: 返回前 N 条
            timeout: 超时秒数

        Returns:
            [{"index": int, "score": float, "document": str}, ...]
        """
        import httpx

        url = f"{self._base_url.rstrip('/')}/rerank"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        payload = {
            "model": self._model_name,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }

        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        # 解析响应：results 数组，每项含 index 和 relevance_score
        raw_results = data.get("results", [])
        parsed = []
        for item in raw_results:
            idx = item.get("index", 0)
            score = item.get("relevance_score", 0.0)
            doc = documents[idx] if 0 <= idx < len(documents) else ""
            parsed.append({
                "index": idx,
                "score": float(score),
                "document": doc,
            })

        # 按 score 降序排列
        parsed.sort(key=lambda x: x["score"], reverse=True)
        return parsed
]]>
