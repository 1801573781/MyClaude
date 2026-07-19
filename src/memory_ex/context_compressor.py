"""Session 内上下文压缩器（独立组件）。

对应设计文档第五章。

ContextCompressor 不继承 MemoryInterface，作为独立组件存在。
query_loop.py 直接持有 ContextCompressor 引用，不通过 Memory 接口调用。

职责：
- Token 水位监控（优先使用 API 返回的 prompt_tokens，降级为本地估算）
- 三段上下文结构压缩（固定区不动 / 摘要区 / 滚动区）
- 工具结果截断（file_view 截断、bash 截断、create/str_replace 保留）
- 恢复期管理（压缩后 2-3 轮内不再压缩，但支持轻量压缩和强制压缩）
- 结束检测（检测到用户可能结束时不压缩）
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """加载 Prompt 模板文件。"""
    try:
        return (_PROMPT_DIR / filename).read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(f"Prompt 模板未找到: {filename}")
        return ""


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（字符数 / 2.5）。"""
    return int(len(text) / 2.5)


def _estimate_messages_tokens(messages: List[Dict]) -> int:
    """估算 api_messages 列表的总 token 数。"""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += _estimate_tokens(str(part.get("text", "")))
        # role 标签的固定开销
        total += 4
    return total


class ContextCompressor:
    """Session 内上下文压缩器。

    独立于 Memory 接口，由 query_loop.py 直接持有。

    操作对象是 Session 内的 api_messages（对话历史），
    与跨 Session 的 Layer 0/1/2 记忆系统是正交关注点。
    """

    # 结束检测关键词
    _END_KEYWORDS = {"好的", "谢谢", "就这样", "可以了", "没了", "明白", "了解"}

    # 技术关键词（用于判断是否真正结束）
    _TECH_KEYWORDS = {
        "py", "python", "bug", "error", "配置", "config", "架构", "数据库",
        "api", "工具", "路径", "文件", "代码", "函数", "类", "模块",
        "测试", "test", "部署", "创建", "修改", "删除", "运行", "执行",
    }

    def __init__(self, config: Any):
        """初始化上下文压缩器。

        Args:
            config: 全局配置对象
        """
        self._config = config
        self._recovery_counter = 0  # 恢复期计数
        self._llm_chat_fn = None
        self._last_compression_stats: Optional[Dict] = None

        # 从 config/context_compression.yaml 或全局配置加载
        self._compress_config = self._load_compress_config(config)

    def _load_compress_config(self, config: Any) -> Any:
        """加载上下文压缩配置。"""
        from types import SimpleNamespace

        # 尝试从全局配置读取
        if hasattr(config, "context_compression"):
            return config.context_compression

        # 尝试从独立 YAML 加载
        config_path = Path("D:/AI/MyClaude/config/context_compression.yaml")
        if config_path.exists():
            try:
                import yaml

                with open(config_path, "r", encoding="utf-8") as f:
                    yaml_data = yaml.safe_load(f)
                    if yaml_data and "context_compression" in yaml_data:
                        return _dict_to_namespace(yaml_data["context_compression"])
            except Exception as e:
                logger.warning(f"加载 context_compression.yaml 失败: {e}")

        # 默认配置
        return SimpleNamespace(
            enabled=True,
            max_context_length=128000,
            compress_threshold=0.85,
            warning_threshold=0.70,
            target_ratio=0.40,
            keep_recent_turns=3,
            recovery_turns=3,
            summary_max_tokens=2048,
            summary_temperature=0.1,
            tool_result_truncate_lines=20,
            file_view_large_threshold=50,
            fallback_token_estimation=True,
        )

    def set_llm_chat_fn(self, fn):
        """注入 LLM 调用函数（用于生成摘要）。"""
        self._llm_chat_fn = fn

    def compress(
        self,
        api_messages: list,
        prompt_tokens: int,
        initial_msg_count: int,
    ) -> Tuple[list, Optional[Dict]]:
        """压缩 Session 内上下文。

        对应设计文档第五章 5.4-5.5 节。

        Args:
            api_messages: 当前 api_messages 列表
            prompt_tokens: 上一轮 API 返回的 prompt_tokens（0 表示无）
            initial_msg_count: 固定区消息数量（不参与压缩）

        Returns:
            (new_api_messages, compression_stats)
            compression_stats 为 None 表示未执行压缩
        """
        if not self._compress_config.enabled:
            return api_messages, None

        # 计算 token 水位
        max_context = int(getattr(self._compress_config, "max_context_length", 128000))
        if prompt_tokens and prompt_tokens > 0:
            current_tokens = prompt_tokens
        elif getattr(self._compress_config, "fallback_token_estimation", True):
            current_tokens = _estimate_messages_tokens(api_messages)
        else:
            return api_messages, None

        ratio = current_tokens / max_context
        threshold = float(getattr(self._compress_config, "compress_threshold", 0.85))

        # 低于阈值，不压缩
        if ratio < threshold:
            return api_messages, None

        # 结束检测：如果用户可能结束，跳过压缩
        if self._is_likely_ending(api_messages):
            logger.info("检测到用户可能结束对话，跳过压缩")
            return api_messages, None

        # 首轮对话不压缩
        scrollable = api_messages[initial_msg_count:]
        if len(scrollable) < 4:
            logger.info("对话轮次过少，不压缩")
            return api_messages, None

        # 恢复期处理
        recovery_turns = int(getattr(self._compress_config, "recovery_turns", 3))
        if self._recovery_counter > 0:
            if ratio < 0.95:
                # 恢复期内 < 95%：尝试轻量压缩
                logger.info(f"恢复期内（剩余 {self._recovery_counter} 轮），尝试轻量压缩")
                new_messages, stats = self._light_compress(
                    api_messages, initial_msg_count, current_tokens, max_context
                )
                if stats:
                    self._recovery_counter = recovery_turns
                return new_messages, stats
            else:
                # 恢复期内 ≥ 95%：强制完整压缩
                logger.warning("恢复期内 token ≥ 95%，强制完整压缩")

        # 执行完整压缩
        new_messages, stats = self._full_compress(
            api_messages, initial_msg_count, current_tokens, max_context
        )

        if stats:
            self._recovery_counter = recovery_turns
            self._last_compression_stats = stats

        return new_messages, stats

    def _full_compress(
        self,
        api_messages: list,
        initial_msg_count: int,
        current_tokens: int,
        max_context: int,
    ) -> Tuple[list, Optional[Dict]]:
        """执行完整压缩（含 LLM 摘要）。

        流程：
        1. 确定压缩范围（保留最近 N 轮原文）
        2. 提取关键信息（调用 LLM）
        3. 合并到现有摘要
        4. 重构 api_messages
        5. 截断历史工具结果
        """
        keep_recent = int(getattr(self._compress_config, "keep_recent_turns", 3))

        # 分割固定区和可压缩区
        fixed_messages = api_messages[:initial_msg_count]
        scrollable = api_messages[initial_msg_count:]

        # 查找现有摘要区
        summary_idx = None
        for i, msg in enumerate(scrollable):
            content = msg.get("content", "")
            if isinstance(content, str) and content.startswith("[CONTEXT_SUMMARY]"):
                summary_idx = i
                break

        # 确定待压缩的轮次
        # 保留最近 keep_recent * 2 条消息（user + assistant 一对）
        keep_count = keep_recent * 2
        if summary_idx is not None:
            compress_end = summary_idx  # 摘要区之前的内容可压缩
            if compress_end <= 0:
                # 没有可压缩的内容
                pass
            to_compress = scrollable[:compress_end]
        else:
            # 没有摘要区，保留最后 keep_count 条
            if len(scrollable) <= keep_count:
                # 内容太少，仅做工具结果截断
                return self._light_compress(
                    api_messages, initial_msg_count, current_tokens, max_context
                )
            compress_end = len(scrollable) - keep_count
            to_compress = scrollable[:compress_end]

        if not to_compress:
            return api_messages, None

        # 提取关键信息（调用 LLM）
        new_summary = self._generate_summary(to_compress)
        if new_summary is None:
            # LLM 摘要失败，回退到不压缩
            logger.warning("LLM 摘要生成失败，回退到不压缩")
            return api_messages, None

        # 合并到现有摘要
        existing_summary = ""
        if summary_idx is not None:
            existing_content = scrollable[summary_idx].get("content", "")
            if isinstance(existing_content, str):
                # 移除 [CONTEXT_SUMMARY] 前缀
                existing_summary = existing_content.replace("[CONTEXT_SUMMARY]", "", 1).strip()

        if existing_summary:
            merged_summary = self._merge_summaries(existing_summary, new_summary)
        else:
            merged_summary = new_summary

        # 重构 api_messages
        summary_msg = {"role": "user", "content": f"[CONTEXT_SUMMARY]\n{merged_summary}"}

        if summary_idx is not None:
            # 替换旧摘要区，删除被压缩的轮次
            kept_scrollable = scrollable[summary_idx + 1:]
            # 但需要保留最近 keep_count 条
            if len(kept_scrollable) > keep_count:
                kept_scrollable = kept_scrollable[-keep_count:]
        else:
            # 创建新摘要区
            kept_scrollable = scrollable[compress_end:]

        new_messages = fixed_messages + [summary_msg] + kept_scrollable

        # 截断历史工具结果
        new_messages = self._truncate_tool_results(new_messages, initial_msg_count)

        # 统计
        new_tokens = _estimate_messages_tokens(new_messages)
        ratio_before = current_tokens / max_context
        ratio_after = new_tokens / max_context

        stats = {
            "ratio_before": ratio_before,
            "ratio_after": ratio_after,
            "tokens_before": current_tokens,
            "tokens_after": new_tokens,
            "compressed_turns": len(to_compress),
            "mode": "full",
            "timestamp": datetime.now().isoformat(),
        }

        logger.info(
            f"完整压缩: {ratio_before:.0%} → {ratio_after:.0%} "
            f"(压缩 {len(to_compress)} 条消息)"
        )

        return new_messages, stats

    def _light_compress(
        self,
        api_messages: list,
        initial_msg_count: int,
        current_tokens: int,
        max_context: int,
    ) -> Tuple[list, Optional[Dict]]:
        """执行轻量压缩（仅截断工具结果，不做 LLM 摘要）。

        适用于恢复期内或工具结果膨胀的场景。
        """
        new_messages = self._truncate_tool_results(api_messages, initial_msg_count)
        new_tokens = _estimate_messages_tokens(new_messages)

        if new_tokens >= current_tokens:
            # 没有减少，说明没有可截断的工具结果
            return api_messages, None

        ratio_before = current_tokens / max_context
        ratio_after = new_tokens / max_context

        stats = {
            "ratio_before": ratio_before,
            "ratio_after": ratio_after,
            "tokens_before": current_tokens,
            "tokens_after": new_tokens,
            "mode": "light",
            "timestamp": datetime.now().isoformat(),
        }

        logger.info(
            f"轻量压缩: {ratio_before:.0%} → {ratio_after:.0%} "
            f"(仅截断工具结果)"
        )

        return new_messages, stats

    def _generate_summary(self, messages: List[Dict]) -> Optional[str]:
        """调用 LLM 生成对话摘要。

        Args:
            messages: 待压缩的对话消息列表

        Returns:
            摘要文本，None 表示失败
        """
        if not self._llm_chat_fn:
            logger.warning("LLM 调用函数未注入，无法生成摘要")
            return None

        # 格式化对话记录
        conversation = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, str):
                conversation.append(f"[{role}] {content[:500]}")  # 截断超长内容
        conversation_text = "\n".join(conversation)

        # 加载 Prompt
        prompt_template = _load_prompt("summary_prompt.txt")
        if not prompt_template:
            prompt_template = self._get_builtin_summary_prompt()

        prompt = prompt_template.replace("{conversation_turns}", conversation_text)

        try:
            import threading

            result = {"response": None, "done": False}

            def _call():
                try:
                    response = self._llm_chat_fn(
                        prompt,
                        temperature=float(
                            getattr(self._compress_config, "summary_temperature", 0.1)
                        ),
                        max_tokens=int(
                            getattr(self._compress_config, "summary_max_tokens", 2048)
                        ),
                    )
                    result["response"] = response
                except Exception as e:
                    logger.error(f"LLM 摘要调用异常: {e}")
                finally:
                    result["done"] = True

            thread = threading.Thread(target=_call, daemon=True)
            thread.start()
            thread.join(timeout=15)

            if not result["done"]:
                logger.warning("LLM 摘要超时")
                return None

            return result["response"]

        except Exception as e:
            logger.error(f"生成摘要失败: {e}")
            return None

    def _merge_summaries(self, existing: str, new: str) -> str:
        """合并旧摘要和新摘要。

        简化实现：按 section 合并去重。
        """
        sections = ["已完成任务", "关键决策", "用户偏好", "未解决问题", "代码变更历史"]
        merged_parts = []

        for section in sections:
            existing_section = self._extract_section(existing, section)
            new_section = self._extract_section(new, section)

            if existing_section or new_section:
                merged_parts.append(f"## {section}")
                # 去重合并
                lines = set()
                for line in (existing_section + "\n" + new_section).split("\n"):
                    line = line.strip()
                    if line.startswith("- "):
                        lines.add(line)
                for line in sorted(lines):
                    merged_parts.append(line)

        return "\n".join(merged_parts) if merged_parts else new

    def _extract_section(self, text: str, section_name: str) -> str:
        """从摘要文本中提取指定 section 的内容。"""
        pattern = rf"##\s*{re.escape(section_name)}\s*\n(.*?)(?=\n##|$)"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""

    def _truncate_tool_results(
        self, messages: list, initial_msg_count: int
    ) -> list:
        """截断历史轮次中的大工具结果。

        对应设计文档第五章 5.7 节。

        当前轮完整保留，历史轮（2 轮及以前）的工具结果按策略截断。
        """
        if len(messages) <= initial_msg_count + 4:
            return messages

        # 找到当前轮的起始位置（最后一个 user 消息）
        current_turn_start = len(messages)
        for i in range(len(messages) - 1, initial_msg_count - 1, -1):
            if messages[i].get("role") == "user":
                current_turn_start = i
                break

        # 历史轮次的消息索引
        historical_end = current_turn_start

        # 找到当前轮的前一轮起始位置
        prev_turn_start = historical_end
        user_count = 0
        for i in range(historical_end - 1, initial_msg_count - 1, -1):
            if messages[i].get("role") == "user":
                user_count += 1
                if user_count >= 2:
                    prev_turn_start = i + 1
                    break
        else:
            prev_turn_start = initial_msg_count

        # 截断 prev_turn_start 之前的工具结果
        new_messages = list(messages)
        large_threshold = int(
            getattr(self._compress_config, "file_view_large_threshold", 50)
        )
        truncate_lines = int(
            getattr(self._compress_config, "tool_result_truncate_lines", 20)
        )

        for i in range(initial_msg_count, prev_turn_start):
            msg = new_messages[i]
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue

            # 检测工具结果类型并截断
            if "[TOOL_RESULT]" in content or "[BLOCKED]" in content:
                new_messages[i] = {
                    "role": msg["role"],
                    "content": self._truncate_content(content, truncate_lines),
                }
            elif content.startswith("[file_view]"):
                lines = content.split("\n")
                if len(lines) > large_threshold:
                    new_messages[i] = {
                        "role": msg["role"],
                        "content": self._truncate_file_view(content, large_threshold),
                    }

        return new_messages

    def _truncate_content(self, content: str, max_lines: int) -> str:
        """截断长文本内容，保留最后 N 行。"""
        lines = content.split("\n")
        if len(lines) <= max_lines:
            return content

        truncated = lines[-max_lines:]
        header = f"... (省略前 {len(lines) - max_lines} 行)\n"
        return header + "\n".join(truncated)

    def _truncate_file_view(self, content: str, threshold: int) -> str:
        """截断大 file_view 结果：保留前 5 行 + 后 5 行。"""
        lines = content.split("\n")
        if len(lines) <= threshold:
            return content

        head = lines[:5]
        tail = lines[-5:]
        middle = f"... (省略 {len(lines) - 10} 行)"
        return "\n".join(head + [middle] + tail)

    def _is_likely_ending(self, messages: list) -> bool:
        """结束检测：判断用户是否可能结束对话。

        对应设计文档第五章 5.8 节。
        """
        # 获取最后的用户消息
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    last_user_msg = content
                break

        if not last_user_msg:
            return False

        # 信号 1: 包含结束关键词且后无具体指令
        for keyword in self._END_KEYWORDS:
            if keyword in last_user_msg:
                # 检查关键词后面是否紧跟具体指令
                after_keyword = last_user_msg.split(keyword, 1)[-1].strip()
                if len(after_keyword) > 20 or self._has_tech_keywords(after_keyword):
                    return False
                return True

        # 信号 2: 连续 2 轮用户输入 < 10 字符
        user_msgs = []
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and not content.startswith("["):
                    user_msgs.append(content)
                if len(user_msgs) >= 2:
                    break

        if len(user_msgs) >= 2 and all(len(m) < 10 for m in user_msgs):
            return True

        # 信号 3: 距上次工具调用已超过 3 轮
        # 统计最近 6 条消息中是否有工具结果
        recent = messages[-6:] if len(messages) >= 6 else messages
        has_tool_result = any(
            "[TOOL_RESULT]" in msg.get("content", "")
            or "[BLOCKED]" in msg.get("content", "")
            for msg in recent
            if isinstance(msg.get("content", ""), str)
        )
        if not has_tool_result and len(user_msgs) >= 3:
            return True

        return False

    def _has_tech_keywords(self, text: str) -> bool:
        """检查文本中是否包含技术关键词。"""
        text_lower = text.lower()
        for kw in self._TECH_KEYWORDS:
            if kw.lower() in text_lower:
                return True
        return False

    def _get_builtin_summary_prompt(self) -> str:
        """内置摘要 Prompt。"""
        return (
            "你是一个对话摘要专家。以下是一个 AI 编程助手与用户的早期对话记录。\n"
            "请提取关键信息，按以下结构输出：\n\n"
            "## 已完成任务\n"
            "- [文件名] [操作] [简述]\n\n"
            "## 关键决策\n"
            "- [决策内容] [原因]\n\n"
            "## 用户偏好（本次 Session）\n"
            "- [偏好描述]\n\n"
            "## 未解决问题\n"
            "- [问题描述]\n\n"
            "## 代码变更历史\n"
            "- [文件名]: [版本演进]\n\n"
            "要求：\n"
            "1. 丢弃闲聊、重复信息、已过时的上下文\n"
            "2. 每条不超过一行\n"
            "3. 如果某类信息没有，留空该 section\n"
            "4. 保持中文\n\n"
            "对话记录：\n"
            "{conversation_turns}"
        )


def _dict_to_namespace(d: dict):
    """递归将 dict 转为 SimpleNamespace。"""
    from types import SimpleNamespace

    if not isinstance(d, dict):
        return d
    return SimpleNamespace(
        **{k: _dict_to_namespace(v) for k, v in d.items()}
    )
