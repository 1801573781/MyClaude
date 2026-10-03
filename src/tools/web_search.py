"""互联网搜索工具（web_search）。

提供 Provider 抽象（tavily / bocha / zhipu / duckduckgo 可插拔切换）、
本地 JSON 缓存（TTL 控制）、最小间隔限流、超时重试与优雅降级。

工具协议（LLM 侧，自闭合标签）：
    <web_search query="搜索关键词" max_results="5"/>

执行链路：
    tool_executor 解析标签 → execute_web_search(params) → 格式化文本回注上下文

设计约束（遵守项目架构契约）：
    - 全同步 requests 调用，禁止 async/await
    - api_key 绝不进入 LLM 上下文（仅出现在 HTTP 请求头中）
    - 结果注入受 token_budget 控制，防止撑爆上下文
    - 网络失败/功能关闭时返回明确提示，让 LLM 基于自身知识作答并声明局限
"""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from src.utility.config_loader import global_cfg

logger = logging.getLogger(__name__)


# ======================== 数据结构 ========================

@dataclass
class SearchResult:
    """单条搜索结果"""
    title: str
    url: str
    snippet: str


@dataclass
class WebSearchConfig:
    """web_search 全量配置（从 config.yaml + model_key.yaml 合并读取）"""
    provider: str = "tavily"
    api_key: str = ""
    base_url: str = ""
    enabled: bool = False
    trigger_mode: str = "auto"      # auto / explicit / private
    sensitive_words: tuple = ()     # 用户自定义受保护词汇（query 命中即拒绝）
    max_results: int = 5
    timeout: int = 10
    max_retries: int = 2
    cache_ttl: int = 3600
    token_budget: int = 3000
    min_interval: float = 2.0


# ======================== 配置加载 ========================

def _to_bool(value) -> bool:
    """兼容 YAML 中的布尔值与字符串写法（true/"True"/"1"/"on"）"""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value)


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_ws_config() -> WebSearchConfig:
    """从 global_cfg 读取 web_search 配置段，缺省值兜底（配置缺失不影响启动）"""
    cfg = WebSearchConfig()
    ws = getattr(global_cfg, "web_search", None)
    if ws is None:
        return cfg
    cfg.provider = str(getattr(ws, "provider", cfg.provider)).strip().lower()
    cfg.api_key = str(getattr(ws, "api_key", cfg.api_key)).strip()
    cfg.base_url = str(getattr(ws, "base_url", cfg.base_url)).strip()
    # base_url 防御性规范化：常见配置失误是漏写协议前缀（如 api.xxx.com/v1/search），
    # requests 会抛 "Invalid URL: No scheme supplied"。自动补 https:// 前缀。
    if cfg.base_url and not cfg.base_url.startswith(("http://", "https://")):
        cfg.base_url = "https://" + cfg.base_url
    cfg.enabled = _to_bool(getattr(ws, "enabled", cfg.enabled))
    # 触发模式：auto=LLM自主 / explicit=仅用户明确要求 / private=自主但query仅限通用技术词汇
    cfg.trigger_mode = str(getattr(ws, "trigger_mode", cfg.trigger_mode)).strip().lower()
    if cfg.trigger_mode not in ("auto", "explicit", "private"):
        cfg.trigger_mode = "auto"
    raw_words = getattr(ws, "sensitive_words", None) or []
    if isinstance(raw_words, str):
        raw_words = [raw_words]
    cfg.sensitive_words = tuple(str(w).strip() for w in raw_words if str(w).strip())
    cfg.max_results = _to_int(getattr(ws, "max_results", cfg.max_results), cfg.max_results)
    cfg.timeout = _to_int(getattr(ws, "timeout", cfg.timeout), cfg.timeout)
    cfg.max_retries = _to_int(getattr(ws, "max_retries", cfg.max_retries), cfg.max_retries)
    cfg.cache_ttl = _to_int(getattr(ws, "cache_ttl", cfg.cache_ttl), cfg.cache_ttl)
    cfg.token_budget = _to_int(getattr(ws, "token_budget", cfg.token_budget), cfg.token_budget)
    cfg.min_interval = _to_float(getattr(ws, "min_interval", cfg.min_interval), cfg.min_interval)
    return cfg


# ======================== Provider 抽象层 ========================

class SearchProvider:
    """搜索后端抽象基类：实现 search() 即可插拔接入"""

    name = "base"

    def __init__(self, cfg: WebSearchConfig):
        self.cfg = cfg


    def search(self, query: str, max_results: int) -> list:
        raise NotImplementedError


    def _post_with_retry(self, url: str, headers: dict, payload: dict) -> dict:
        """带指数退避重试的同步 POST 请求。

        认证类错误（401/403）不重试，直接抛出。
        """
        last_err = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                resp = requests.post(
                    url, json=payload, headers=headers, timeout=self.cfg.timeout
                )
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout as e:
                last_err = f"请求超时（{self.cfg.timeout}s），可能是网络波动或需要代理"
            except requests.exceptions.ConnectionError as e:
                last_err = f"连接失败（检查网络连通性/代理设置）: {e}"
            except requests.exceptions.HTTPError as e:
                code = e.response.status_code if e.response is not None else "?"
                last_err = f"HTTP {code} 错误: {e}"
                if code in (401, 403, 402):
                    break  # 认证/配额类错误重试无意义
            except (ValueError, KeyError) as e:
                last_err = f"响应解析失败: {e}"
            except requests.exceptions.RequestException as e:
                last_err = f"请求异常: {e}"
            if attempt < self.cfg.max_retries:
                time.sleep(2 ** attempt)  # 指数退避：1s, 2s
        raise RuntimeError(last_err or "未知请求错误")


class TavilyProvider(SearchProvider):
    """Tavily：专为 LLM/RAG 设计，海外网络环境，免费额度 1000 次/月"""
    name = "tavily"
    DEFAULT_URL = "https://api.tavily.com/search"


    def search(self, query: str, max_results: int) -> list:
        url = self.cfg.base_url or self.DEFAULT_URL
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cfg.api_key}",
        }
        payload = {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        data = self._post_with_retry(url, headers, payload)
        results = []
        for item in (data.get("results") or [])[:max_results]:
            results.append(SearchResult(
                title=(item.get("title") or "").strip(),
                url=(item.get("url") or "").strip(),
                snippet=(item.get("content") or item.get("snippet") or "").strip(),
            ))
        return results


class BochaProvider(SearchProvider):
    """博查：国产搜索后端，国内直连，对中文内容召回较好"""
    name = "bocha"
    DEFAULT_URL = "https://api.bochaai.com/v1/web-search"


    def search(self, query: str, max_results: int) -> list:
        url = self.cfg.base_url or self.DEFAULT_URL
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cfg.api_key}",
        }
        payload = {"query": query, "count": max_results, "summary": True}
        data = self._post_with_retry(url, headers, payload)
        results = []
        pages = (data.get("data") or {}).get("webPages") or {}
        for item in (pages.get("value") or [])[:max_results]:
            results.append(SearchResult(
                title=(item.get("name") or "").strip(),
                url=(item.get("url") or "").strip(),
                snippet=(item.get("summary") or item.get("snippet") or "").strip(),
            ))
        return results


class ZhipuProvider(SearchProvider):
    """智谱 web-search-pro：复用 GLM API Key，国内直连，中文召回好。

    特殊性：走智谱 tools 端点（非标准搜索接口），请求体传 model="web-search-pro"，
    结果嵌在 choices[0].message.tool_calls[*].search_result 数组中。
    官方不支持指定返回条数，取回后客户端截断到 max_results。
    """
    name = "zhipu"
    DEFAULT_URL = "https://open.bigmodel.cn/api/paas/v4/tools"


    def search(self, query: str, max_results: int) -> list:
        url = self.cfg.base_url or self.DEFAULT_URL
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cfg.api_key}",
        }
        payload = {
            "model": "web-search-pro",
            "messages": [{"role": "user", "content": query}],
            "stream": False,
        }
        data = self._post_with_retry(url, headers, payload)
        # 业务层错误：智谱部分错误以 200 + error 字段返回
        if data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"智谱接口返回错误: {msg}")
        choices = data.get("choices") or []
        if not choices:
            return []
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        results = []
        for call in tool_calls:
            for item in (call.get("search_result") or []):
                results.append(SearchResult(
                    title=(item.get("title") or "").strip(),
                    url=(item.get("link") or item.get("url") or "").strip(),
                    snippet=(item.get("content") or item.get("snippet") or "").strip(),
                ))
        return results[:max_results]


class DuckDuckGoProvider(SearchProvider):
    """DuckDuckGo：免 Key 后端，依赖 duckduckgo-search 库，稳定性一般"""
    name = "duckduckgo"


    def search(self, query: str, max_results: int) -> list:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            raise RuntimeError(
                "未安装 duckduckgo-search 库，请执行: pip install duckduckgo-search"
            )
        results = []
        try:
            with DDGS(timeout=self.cfg.timeout) as ddgs:
                for item in ddgs.text(query, max_results=max_results):
                    results.append(SearchResult(
                        title=(item.get("title") or "").strip(),
                        url=(item.get("href") or item.get("url") or "").strip(),
                        snippet=(item.get("body") or "").strip(),
                    ))
        except Exception as e:
            raise RuntimeError(f"DuckDuckGo 搜索失败: {e}")
        return results


_PROVIDERS = {
    TavilyProvider.name: TavilyProvider,
    BochaProvider.name: BochaProvider,
    ZhipuProvider.name: ZhipuProvider,
    DuckDuckGoProvider.name: DuckDuckGoProvider,
}


def create_provider(cfg: WebSearchConfig) -> SearchProvider:
    """工厂函数：按配置实例化搜索后端"""
    cls = _PROVIDERS.get(cfg.provider)
    if cls is None:
        raise RuntimeError(
            f"未知的搜索后端 provider: {cfg.provider}，可选: {list(_PROVIDERS.keys())}"
        )
    return cls(cfg)


# ======================== 本地缓存（JSON 文件 + TTL） ========================

class SearchCache:
    """搜索结果本地缓存：同 query 在 TTL 内直接命中，节省 API 配额"""

    def __init__(self, cache_path: Path, ttl: int, max_entries: int = 200):
        self.path = cache_path
        self.ttl = ttl
        self.max_entries = max_entries
        self._data = {}
        self._load()


    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (OSError, ValueError) as e:
                logger.warning(f"搜索缓存读取失败，忽略: {e}")
                self._data = {}


    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
        except OSError as e:
            logger.warning(f"搜索缓存写入失败: {e}")


    @staticmethod
    def _key(query: str, max_results: int) -> str:
        return hashlib.md5(f"{query}|{max_results}".encode("utf-8")).hexdigest()


    def get(self, query: str, max_results: int):
        """命中返回结果列表（dict 形式），未命中返回 None"""
        if self.ttl <= 0:
            return None
        key = self._key(query, max_results)
        entry = self._data.get(key)
        if not entry:
            return None
        if time.time() - entry.get("ts", 0) > self.ttl:
            self._data.pop(key, None)
            return None
        return entry.get("results") or []


    def put(self, query: str, max_results: int, results: list):
        if self.ttl <= 0:
            return
        key = self._key(query, max_results)
        self._data[key] = {
            "query": query[:100],
            "ts": time.time(),
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet} for r in results
            ],
        }
        # 容量控制：超限时淘汰最旧的一半条目
        if len(self._data) > self.max_entries:
            sorted_items = sorted(self._data.items(), key=lambda kv: kv[1].get("ts", 0))
            for k, _ in sorted_items[:len(sorted_items) // 2]:
                self._data.pop(k, None)
        self._save()


# ======================== 限流器（最小间隔） ========================

class RateLimiter:
    """最小间隔限流：防止 LLM 连续刷搜索耗尽 API 配额"""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last_call = 0.0


    def wait(self):
        if self.min_interval <= 0:
            return
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.time()


# ======================== 隐私过滤（代码级硬约束） ========================

# 高置信敏感模式：命中即拒绝，所有 trigger_mode 下均生效。
# 路径/密钥类内容对搜索本身无价值，出现在 query 里只会泄露项目内部信息。
_SENSITIVE_PATTERNS = (
    (re.compile(r'[A-Za-z]:[\\/]'), "盘符路径（如 D:\\ 或 D:/）"),
    (re.compile(r'\w+\\\w+'), "反斜杠路径片段"),
    (re.compile(r'\b(?:sk|tvly|gsk)-[A-Za-z0-9_\-]{12,}'), "API Key 形态字符串（sk-/tvly-/gsk- 前缀）"),
    (re.compile(r'\b[0-9a-f]{32}\.[A-Za-z0-9_\-]{8,}\b'), "GLM Key 形态字符串（32位hex.后缀）"),
    (re.compile(r'\bBearer\s+\S+', re.IGNORECASE), "认证头 Bearer"),
)


def check_query_privacy(query: str, sensitive_words: tuple = ()) -> str:
    """检查 query 是否含敏感内容。返回命中原因，干净则返回空字符串。

    规则（所有模式生效）：
    1. 路径/密钥形态：盘符路径、反斜杠路径、sk-/tvly- 类 Key、GLM Key、Bearer 头
    2. 用户自定义 sensitive_words：不区分大小写的子串匹配
    """
    for pat, desc in _SENSITIVE_PATTERNS:
        if pat.search(query):
            return desc
    for w in sensitive_words:
        if w and w.lower() in query.lower():
            return f"受保护词汇 '{w}'"
    return ""


# ======================== 结果后处理与格式化 ========================

def _dedup(results: list) -> list:
    """按 URL 去重（忽略末尾斜杠与大小写）"""
    seen = set()
    out = []
    for r in results:
        url_key = r.url.rstrip("/").lower()
        if url_key and url_key not in seen:
            seen.add(url_key)
            out.append(r)
    return out


def format_results(query: str, results: list, provider_name: str,
                   token_budget: int, cached: bool) -> str:
    """将搜索结果格式化为编号列表，并按 token 预算截断。

    token 估算：中文约 1 token ≈ 2 字符，按字符预算 = token_budget * 2 保守控制。
    """
    if not results:
        return (
            f"搜索完成但未返回任何结果。\n查询: {query}\n"
            f"建议：更换关键词、改用英文查询，或拆分问题后重试。"
        )
    char_budget = max(500, token_budget * 2)
    total = len(results)
    entries = []
    used = 0
    for i, r in enumerate(results, 1):
        snippet = r.snippet or "（无摘要）"
        if len(snippet) > 300:
            snippet = snippet[:297] + "..."
        entry = f"{i}. {r.title or '（无标题）'}\n   URL: {r.url}\n   摘要: {snippet}"
        if used + len(entry) > char_budget:
            break
        entries.append(entry)
        used += len(entry)

    header = (
        f"搜索结果（后端: {provider_name}{'，来自本地缓存' if cached else ''}），"
        f"查询: {query}，共 {total} 条"
    )
    if len(entries) < total:
        header += f"，因 token 预算仅展示前 {len(entries)} 条"
    return header + "\n\n" + "\n\n".join(entries)


# ======================== 模块级单例 ========================

_cache_instance = None
_rate_limiter_instance = None


def _get_cache(cfg: WebSearchConfig) -> SearchCache:
    global _cache_instance
    if _cache_instance is None:
        cache_path = Path(global_cfg.base_path.logs_root) / "web_search_cache.json"
        _cache_instance = SearchCache(cache_path, cfg.cache_ttl)
    return _cache_instance


def _get_rate_limiter(cfg: WebSearchConfig) -> RateLimiter:
    global _rate_limiter_instance
    if _rate_limiter_instance is None:
        _rate_limiter_instance = RateLimiter(cfg.min_interval)
    return _rate_limiter_instance


# ======================== 工具入口 ========================

def execute_web_search(params: dict) -> str:
    """web_search 工具执行入口，供 tool_executor 调用。

    Args:
        params: {"query": str, "max_results": int(可选)}

    Returns:
        格式化的结果文本（成功）或 [BLOCKED]/[ERROR] 提示（失败/未启用），
        供包装为 {"role": "user", "content": "[web_search] ..."} 回注上下文。
    """
    query = str(params.get("query", "")).strip()
    try:
        max_results = int(params.get("max_results") or 0) or None
    except (TypeError, ValueError):
        max_results = None

    if not query:
        return ('[ERROR] web_search 缺少 query 参数。'
                '用法: <web_search query="搜索词" max_results="5"/>')

    cfg = load_ws_config()

    # 功能开关检查
    if not cfg.enabled:
        return (
            "[BLOCKED] web_search 功能未启用（config.yaml 中 web_search.enabled 为 false）。"
            "请基于已有知识回答，并向用户说明当前无法联网搜索。"
        )

    # API Key 检查（duckduckgo 免 Key）
    if cfg.provider != "duckduckgo" and not cfg.api_key:
        return (
            f"[BLOCKED] web_search 后端 '{cfg.provider}' 未配置 api_key"
            f"（在 config/model_key.yaml 的 web_search.api_key 中填写）。"
            f"请基于已有知识回答，并向用户说明无法联网搜索。"
        )

    # 隐私硬过滤（代码级，优先于缓存与真实请求，所有模式生效）
    leak_reason = check_query_privacy(query, cfg.sensitive_words)
    if leak_reason:
        return (
            f"[BLOCKED] web_search 隐私过滤：query 含{leak_reason}，已拒绝执行。\n"
            f"请将问题改写为面向公网知识的通用描述（如 'Python 3.14 free-threading 稳定性'），"
            f"严禁携带项目路径、文件名、函数名、密钥或内部报错细节，然后重新调用；或直接基于已有知识回答。"
        )

    if max_results is None:
        max_results = cfg.max_results
    max_results = max(1, min(max_results, 10))  # 钳制到 1~10

    # 缓存命中：直接返回，不消耗配额
    cache = _get_cache(cfg)
    cached = cache.get(query, max_results)
    if cached:
        results = [SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("snippet", ""),
        ) for item in cached]
        return format_results(query, results, cfg.provider, cfg.token_budget, cached=True)

    # 限流 + 真实请求
    try:
        provider = create_provider(cfg)
        _get_rate_limiter(cfg).wait()
        results = provider.search(query, max_results)
    except RuntimeError as e:
        return (
            f"[ERROR] web_search 执行失败: {e}\n"
            f"请基于已有知识回答，并明确告知用户本次未能联网搜索，不要立即重试。"
        )
    except Exception as e:  # noqa BLE001 兜底：任何异常都不能中断 Query Loop
        logger.error(f"web_search 未知异常: {e}")
        return (
            f"[ERROR] web_search 异常: {e}\n"
            f"请基于已有知识回答，并明确告知用户本次未能联网搜索。"
        )

    results = _dedup(results)
    cache.put(query, max_results, results)
    return format_results(query, results, cfg.provider, cfg.token_budget, cached=False)
