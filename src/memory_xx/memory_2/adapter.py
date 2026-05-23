"""
memory_2 适配层

将 Memory2Backend 适配为 query_loop 所需的 MemoryInterface 接口，
组装 Injector / Compressor，确保 SessionLog 兼容的记忆上下文格式。

与 memory_1/adapter.py 保持相同的适配模式。
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from src.memory_xx.memory_2.memory2 import Memory2Backend
from src.memory_xx.memory_2.memory_injector import MemoryInjector
from src.memory_xx.memory_2.memory_compressor import MemoryCompressor
from src.memory_xx.memory_interface import MemoryInterface

logger = logging.getLogger(__name__)


class Memory2Adapter(MemoryInterface):
    """memory_2 适配器。

    对 Memory2Backend 进行薄封装，注入 Compressor、Injector 和 LLM 回调，
    使 query_loop 可以通过统一的 MemoryInterface 使用 memory_2。

    所有 get_working_memory() 输出均带有
    ``[系统提醒] 以下是与当前任务相关的历史记忆`` 前缀，
    确保 session_log._classify_user() 正确识别为 memory_context section。
    """

    def __init__(self, config: Any = None):
        """
        Args:
            config: 全局配置对象（含 memory_2 节）

        适配器内部根据 model_key.yaml 的 memory_2 配置节，
        自行构建 llm_chat_fn（用于检索评分 + 压缩），
        实现高内聚低耦合，factory 无需感知底层模型细节。
        """
        # 提取 memory_2 配置节
        memory2_cfg = self._extract_config(config)

        # 从 model_key.yaml memory_2 节构建 llm_chat_fn
        llm_chat_fn = self._build_models(config)

        # 核心后端
        self._backend = Memory2Backend(config=memory2_cfg, llm_chat_fn=llm_chat_fn)

        # 将配置转为 dict 供 Injector / Compressor 使用
        cfg_dict = self._cfg_to_dict(memory2_cfg)

        # 注入器（使用后端的 MemoryStore 实例）
        self._injector = MemoryInjector(
            store=self._backend._store,
            config=cfg_dict,
        )

        # 压缩器
        self._compressor = MemoryCompressor(
            memory_store=self._backend._store,
            config=cfg_dict,
            llm_chat=llm_chat_fn if llm_chat_fn else (lambda msg, mt, t: ""),
        )
        # 同步 enabled 状态
        compressor_cfg = cfg_dict.get("compressor", {})
        self._compressor._enabled = compressor_cfg.get("enabled", True)

        # 将 injector / compressor 注入到 backend，供内部调用
        self._backend._injector = self._injector
        self._backend._compressor = self._compressor

        logger.info("Memory2Adapter 初始化完成（SessionLog 兼容模式）")

    # ------------------------------------------------------------------ #
    #  MemoryInterface 实现
    # ------------------------------------------------------------------ #

    def add(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """添加记忆到短期存储，同时写入工作记忆。"""
        # 持久化存储（先获取 ID）
        mem_id = self._backend.add(role, content, metadata=metadata)
        # 写入工作记忆（通过 injector 管理，传入记忆 ID）
        self._injector.add(role, content, memory_id=mem_id)
        return mem_id

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        return self._backend.get(memory_id)

    def search(
        self,
        query: str,
        top_k: int = None,
        **filters: Any,
    ) -> List[Dict[str, Any]]:
        return self._backend.search(query, top_k, **filters)

    def get_working_memory(self) -> str:
        """获取格式化的记忆上下文（工作记忆 + 检索结果）。

        返回带有 ``[系统提醒]`` 前缀的 Markdown 文本，
        session_log 据此将其归类为 memory_context section。

        内部调用 get_context_for_injection() 以触发 LLM 检索召回，
        从而在注入上下文中同时包含工作记忆与从长期/短期记忆检索到的条目。
        """
        return self.get_context_for_injection()

    def get_context_for_injection(self) -> str:
        """获取完整的记忆注入上下文（工作记忆 + 检索）。

        此方法可供 query_loop 在构造 api_messages 时直接调用，
        返回的文本包含：
        - ``[系统提醒]`` 前缀（触发 session_log 分类）
        - ``[相关历史记忆]`` 区块（从长期/短期记忆召回 + 相关的工作记忆）
        - 每条记忆末尾标注 ``(相关性: X.XX)``

        关键规则：仅当 LLM 检索到相关长期/短期记忆时，
        才会注入上下文（format_search_results 内部只合并相关的工作记忆）。
        若检索结果为空，说明当前查询与历史上下文无关，直接返回空字符串。
        """
        working = self._injector.get_all()
        # 用最近一条用户消息作为检索 query
        recent_user_msg = ""
        for item in reversed(working):
            if item.get("role") == "user":
                recent_user_msg = item.get("content", "")
                break

        retrieved: List[Dict[str, Any]] = []
        if recent_user_msg:
            retrieved = self._backend.search(
                recent_user_msg,
                top_k=self._backend._default_top_k,
            )

        if not retrieved and not working:
            return ""

        # format_search_results 内部仅在检索结果非空时合并工作记忆，
        # 若 merged 为空则返回 "没有召唤到相关记忆"
        return self._injector.format_search_results(
            query=recent_user_msg,
            results=retrieved,
        )

    def get_context_for_query(self, query: str) -> str:
        """根据查询关键词获取记忆上下文。

        Args:
            query: 用户查询关键词

        Returns:
            格式化后的 Markdown 文本，包含工作记忆 + 检索结果
        """
        working = self._injector.get_all()
        retrieved = self._backend.search(
            query,
            top_k=self._backend._default_top_k,
        )

        if not retrieved and not working:
            return ""

        return self._injector.format_search_results(
            query=query,
            results=retrieved,
        )

    def update(self, memory_id: str, **fields: Any) -> bool:
        return self._backend.update(memory_id, **fields)

    def delete(self, memory_id: str) -> bool:
        return self._backend.delete(memory_id)

    def clear_all(self) -> int:
        count = self._backend.clear_all()
        self._injector.clear()
        return count

    def compact(self) -> int:
        if self._compressor and self._compressor._enabled:
            # 获取待压缩的短期记忆
            short_mem = self._backend._store.get_all()
            if len(short_mem) > self._backend._short_term_max:
                return self._compressor.compress(short_mem)
        return self._backend.compact()

    def stats(self) -> Dict[str, Any]:
        return self._backend.stats()

    def maintain(self) -> int:
        # 维护工作记忆
        wm_cleaned = self._injector.maintain(self._backend._max_working_turns)
        # 遗忘过期短期记忆
        st_cleaned = self._backend.maintain()
        return wm_cleaned + st_cleaned

    # ------------------------------------------------------------------ #
    #  适配器特有方法
    # ------------------------------------------------------------------ #

    def set_llm_chat_fn(self, chat_fn: Callable[..., str]) -> None:
        """注入/更新 LLM 对话函数。"""
        self._backend.set_llm_chat_fn(chat_fn)
        if self._compressor:
            self._compressor.set_llm_chat_fn(chat_fn)

    def get_backend(self) -> Memory2Backend:
        """获取内部后端实例（测试用）。"""
        return self._backend

    @staticmethod
    def _extract_config(config: Any) -> Any:
        """从全局配置提取 memory_2 配置节。"""
        if config is None:
            return None
        if hasattr(config, "memory_2"):
            return config.memory_2
        if hasattr(config, "memory") and hasattr(config.memory, "memory_2"):
            return config.memory.memory_2
        # 如果全局配置本身就是 memory_2 配置节（从工厂传入）
        if hasattr(config, "working_memory") or hasattr(config, "short_term"):
            return config
        return config

    @staticmethod
    def _build_models(config: Any) -> Optional[Callable[..., str]]:
        """从 model_key.yaml 的 memory_2 节构建 llm_chat_fn。

        memory_2 使用 LLM 直接进行检索评分 + 记忆压缩，只需一个 LLM。

        Returns:
            llm_chat_fn 或 None
        """
        from openai import OpenAI
        import httpx
        from src.utility.config_loader import global_cfg

        mem_cfg = getattr(global_cfg, "memory_2", None)
        if mem_cfg is None:
            logger.warning("Memory2Adapter: 未找到 memory_2 模型配置（model_key.yaml）")
            return None

        llm_cfg = getattr(mem_cfg, "llm", None)
        if llm_cfg is None:
            logger.warning("Memory2Adapter: memory_2 缺少 llm 配置")
            return None

        llm_provider = getattr(llm_cfg, "provider", "")
        llm_model = getattr(llm_cfg, "model_name", "")
        provider_cfg = getattr(global_cfg, llm_provider, None)
        if provider_cfg is None:
            logger.warning(f"Memory2Adapter: 未找到 provider '{llm_provider}' 的配置")
            return None

        api_key = getattr(provider_cfg, "api_key", "")
        base_url = getattr(provider_cfg, "base_url", "")
        if not api_key or not base_url:
            logger.warning(
                f"Memory2Adapter: provider '{llm_provider}' 缺少 api_key 或 base_url"
            )
            return None

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.Client(verify=False),
        )

        def chat_fn(api_messages, max_tokens=4096, temperature=0.1):
            try:
                response = client.chat.completions.create(
                    model=llm_model,
                    messages=api_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                logger.error(f"Memory2Adapter llm_chat_fn 调用失败: {e}")
                return ""

        logger.info(
            f"Memory2Adapter: LLM 模型 provider={llm_provider}, model={llm_model}"
        )
        return chat_fn

    @staticmethod
    def _cfg_to_dict(cfg: Any) -> Dict[str, Any]:
        """将 SimpleNamespace 配置转为 dict，供 Injector / Compressor 使用。"""
        if cfg is None:
            return {}
        if isinstance(cfg, dict):
            return cfg
        result: Dict[str, Any] = {}
        for attr in dir(cfg):
            if attr.startswith("_"):
                continue
            value = getattr(cfg, attr, None)
            if callable(value):
                continue
            result[attr] = value
        return result