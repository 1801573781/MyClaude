"""
memory_1 适配层

将 Memory1Backend 适配为 query_loop 所需的接口格式，
提供便捷的初始化和嵌入函数注入方法。

用法（在 query_loop.py 中）：
    from src.memory_xx.memory_1.adapter import Memory1Adapter
    self._memory = Memory1Adapter(config)
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from src.memory_xx.memory_1.memory1 import Memory1Backend
from src.memory_xx.memory_1.memory_compressor import MemoryCompressor
from src.memory_xx.memory_1.memory_injector import MemoryInjector
from src.memory_xx.memory_interface import MemoryInterface

logger = logging.getLogger(__name__)


class Memory1Adapter(MemoryInterface):
    """memory_1 适配器。

    对 Memory1Backend 进行薄封装，注入 Compressor、Injector 和 LLM 回调，
    使 query_loop 可以通过统一的 MemoryInterface 使用 memory_1。

    适配器不改变任何业务语义，仅做组装和注入。
    """

    def __init__(self, config: Any = None):
        """
        Args:
            config: 全局配置对象（含 memory_1 节）

        适配器内部根据 model_key.yaml 的 memory_1 配置节，
        自行构建 embed_fn（向量化）和 llm_chat_fn（压缩），
        实现高内聚低耦合，factory 无需感知底层模型细节。
        """
        # 提取 memory_1 配置节
        memory1_cfg = self._extract_config(config)

        # 从 model_key.yaml memory_1 节构建 embed_fn 和 llm_chat_fn
        embed_fn, llm_chat_fn = self._build_models(config)

        # 核心后端
        self._backend = Memory1Backend(config=memory1_cfg, embed_fn=embed_fn)
        self._backend._embed_fn = embed_fn

        # 压缩器
        compressor_cfg = getattr(memory1_cfg, "compressor", None)
        self._compressor = MemoryCompressor(
            enabled=getattr(compressor_cfg, "enabled", True) if compressor_cfg else True,
            model=getattr(compressor_cfg, "model", "default") if compressor_cfg else "default",
            max_tokens_per_batch=getattr(compressor_cfg, "max_tokens_per_batch", 4000) if compressor_cfg else 4000,
            llm_chat_fn=llm_chat_fn,
        )
        self._backend._compressor = self._compressor

        # 注入器
        injector_cfg = getattr(memory1_cfg, "injector", None)
        max_tokens = getattr(injector_cfg, "max_tokens", 2000) if injector_cfg else 2000
        self._injector = MemoryInjector(max_tokens=max_tokens)
        self._backend._injector = self._injector

        logger.info("Memory1Adapter 初始化完成")

    # ------------------------------------------------------------------ #
    #  MemoryInterface 委托
    # ------------------------------------------------------------------ #

    def add(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        return self._backend.add(role, content, metadata)

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        return self._backend.get(memory_id)

    def search(self, query: str, top_k: int = None, **filters: Any) -> List[Dict[str, Any]]:
        return self._backend.search(query, top_k, **filters)

    def get_working_memory(self) -> str:
        """返回完整的记忆注入上下文（工作记忆 + 检索结果）。

        内部调用 get_context_for_injection() 以触发检索召回，
        使得注入上下文中同时包含工作记忆与从长期/短期记忆检索到的条目。
        session_log 据此将记忆内容归类为 memory_context section。
        """
        return self.get_context_for_injection()

    def get_context_for_injection(self) -> str:
        """获取完整的记忆注入上下文（工作记忆 + 检索）。"""
        working = self._backend._working_memory
        # 用最近一条用户消息作为检索 query
        recent_user_msg = ""
        for item in reversed(working):
            if item.get("role") == "user":
                recent_user_msg = item.get("content", "")
                break

        retrieved = []
        if recent_user_msg:
            retrieved = self._backend.search(recent_user_msg, top_k=self._backend._default_top_k)

        return self._injector.format_context(working, retrieved)

    def get_context_for_query(self, query: str) -> str:
        """根据查询关键词获取记忆上下文。"""
        working = self._backend._working_memory
        retrieved = self._backend.search(query, top_k=self._backend._default_top_k)
        return self._injector.format_context(working, retrieved)

    def update(self, memory_id: str, **fields: Any) -> bool:
        return self._backend.update(memory_id, **fields)

    def delete(self, memory_id: str) -> bool:
        return self._backend.delete(memory_id)

    def clear_all(self) -> int:
        return self._backend.clear_all()

    def compact(self) -> int:
        return self._backend.compact()

    def stats(self) -> Dict[str, Any]:
        return self._backend.stats()

    def maintain(self) -> int:
        return self._backend.maintain()

    # ------------------------------------------------------------------ #
    #  适配器特有方法
    # ------------------------------------------------------------------ #

    def set_llm_chat_fn(self, chat_fn: Callable[..., str]) -> None:
        """注入/更新 LLM 对话函数。"""
        self._compressor.set_llm_chat_fn(chat_fn)

    def set_embed_fn(self, embed_fn: Callable[[str], List[float]]) -> None:
        """注入/更新 Embedding 函数。"""
        self._backend._embed_fn = embed_fn
        self._backend._retriever.set_embedding_function(embed_fn)

    def get_backend(self) -> Memory1Backend:
        """获取内部后端实例（测试用）。"""
        return self._backend

    @staticmethod
    def _extract_config(config: Any) -> Any:
        """从全局配置提取 memory_1 配置节。"""
        if config is None:
            return None
        if hasattr(config, "memory_1"):
            return config.memory_1
        if hasattr(config, "memory") and hasattr(config.memory, "memory_1"):
            return config.memory.memory_1
        # 如果全局配置本身就是 memory_1 配置（从工厂传入时可能直接给 memory_1 节）
        if hasattr(config, "working_memory") or hasattr(config, "short_term"):
            return config
        return config

    @staticmethod
    def _build_models(config: Any):
        """从 model_key.yaml 的 memory_1 节构建 embed_fn 和 llm_chat_fn。

        memory_1 有两个模型交互需求：
        - embedding：向量化模型（如 MiniMax-Embedding-01）
        - llm：压缩用 LLM（如 deepseek-chat）

        Returns:
            tuple[embed_fn, llm_chat_fn]
        """
        from openai import OpenAI
        import httpx
        from src.utility.config_loader import global_cfg

        mem_cfg = getattr(global_cfg, "memory_1", None)

        # ---- embedding 模型 ----
        embed_fn = None
        if mem_cfg is not None:
            emb_cfg = getattr(mem_cfg, "embedding", None)
            if emb_cfg is not None:
                emb_provider = getattr(emb_cfg, "provider", "")
                emb_model = getattr(emb_cfg, "model_name", "")
                emb_provider_cfg = getattr(global_cfg, emb_provider, None)
                if emb_provider_cfg:
                    emb_api_key = getattr(emb_provider_cfg, "api_key", "")
                    emb_base_url = getattr(emb_provider_cfg, "base_url", "")
                    if emb_api_key and emb_base_url:
                        emb_client = OpenAI(
                            api_key=emb_api_key,
                            base_url=emb_base_url,
                            http_client=httpx.Client(verify=False),
                        )

                        def _embed_fn(text: str) -> list:
                            try:
                                resp = httpx.post(
                                    f"{emb_base_url.rstrip('/')}/embeddings",
                                    json={
                                        "model": emb_model,  # "embo-01"
                                        "type": "db",  # 必填，根据官方文档使用 "db"
                                        "texts": [text],
                                    },
                                    headers={
                                        "Authorization": f"Bearer {emb_api_key}",
                                        "Content-Type": "application/json",
                                    },
                                    verify=False,
                                    timeout=30,
                                )
                                if resp.status_code != 200:
                                    logger.error(
                                        f"embedding 调用失败: HTTP {resp.status_code}, "
                                        f"body={resp.text[:500]}"
                                    )
                                    return None
                                body = resp.json()
                                # 优先检查 MiniMax base_resp 错误响应
                                # status_code=0 表示成功，非 0 表示业务错误
                                if "base_resp" in body:
                                    base_resp = body["base_resp"]
                                    err_code = base_resp.get("status_code", -1)
                                    err_msg = base_resp.get("status_msg", "unknown error")
                                    if err_code != 0:
                                        logger.error(
                                            f"embedding API 返回错误: code={err_code}, "
                                            f"msg={err_msg}（请检查 model_key.yaml 中 "
                                            f"MiniMax 的 api_key 是否支持 embedding 服务）"
                                        )
                                        return None
                                # MiniMax 成功响应格式: {"vectors": [[...]]}
                                # OpenAI 兼容格式: {"data": [{"embedding": [...]}]}
                                if "vectors" in body and body["vectors"]:
                                    return body["vectors"][0]
                                if "data" in body and body["data"]:
                                    item = body["data"][0]
                                    if "embedding" in item:
                                        return item["embedding"]
                                    if "vector" in item:
                                        return item["vector"]
                                logger.error(
                                    f"embedding 调用失败: 无法从响应中提取向量, "
                                    f"keys={list(body.keys())}, body={str(body)[:300]}"
                                )
                                return None
                            except Exception as e:
                                logger.error(f"embedding 调用失败: {e}")
                                return None

                        embed_fn = _embed_fn
                        logger.info(
                            f"Memory1Adapter: embedding 模型 provider={emb_provider}, "
                            f"model={emb_model}"
                        )
                    else:
                        logger.warning(
                            f"Memory1Adapter: embedding provider '{emb_provider}' "
                            f"缺少 api_key 或 base_url"
                        )

        # ---- LLM 模型（压缩用） ----
        llm_chat_fn = None
        if mem_cfg is not None:
            llm_cfg = getattr(mem_cfg, "llm", None)
            if llm_cfg is not None:
                llm_provider = getattr(llm_cfg, "provider", "")
                llm_model = getattr(llm_cfg, "model_name", "")
                llm_provider_cfg = getattr(global_cfg, llm_provider, None)
                if llm_provider_cfg:
                    llm_api_key = getattr(llm_provider_cfg, "api_key", "")
                    llm_base_url = getattr(llm_provider_cfg, "base_url", "")
                    if llm_api_key and llm_base_url:
                        llm_client = OpenAI(
                            api_key=llm_api_key,
                            base_url=llm_base_url,
                            http_client=httpx.Client(verify=False),
                        )

                        def _chat_fn(api_messages, max_tokens=4096, temperature=0.1):
                            try:
                                response = llm_client.chat.completions.create(
                                    model=llm_model,
                                    messages=api_messages,
                                    max_tokens=max_tokens,
                                    temperature=temperature,
                                )
                                return response.choices[0].message.content or ""
                            except Exception as e:
                                logger.error(f"llm_chat_fn 调用失败: {e}")
                                return ""

                        llm_chat_fn = _chat_fn
                        logger.info(
                            f"Memory1Adapter: LLM 模型 provider={llm_provider}, "
                            f"model={llm_model}"
                        )
                    else:
                        logger.warning(
                            f"Memory1Adapter: LLM provider '{llm_provider}' "
                            f"缺少 api_key 或 base_url"
                        )

        return embed_fn, llm_chat_fn
