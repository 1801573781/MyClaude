from enum import Enum
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Callable, Dict, Optional
from src.query import chat_llm
from src.message import llm_api_msg
from src.llm_tool import tool_executor
from src.utility.config_loader import global_cfg
from src.utility.normal_utility import strip_thinking
from src.query.session_log import SessionLog
from src.memory.memory_manager import MemoryManager
import logging

logger = logging.getLogger(__name__)


class ChatOrNot(Enum):
    QuitByNoneTool = 1
    QuitByDone = 2
    Continue = 3


class QueryLoop:
    """
    与 LLM 的多轮交互引擎。
    不负责任何终端显示，只通过回调接口通知外部。
    """


    def __init__(self, show_thinking: bool = False):
        self.show_thinking = show_thinking

        self.api_messages = None
        self.session = None
        self.max_turns = 0
        self.is_chat_mode = True

        self._memory_manager = self._init_memory_manager()
        self.memory_manager = self._memory_manager  # 公开引用，供 CLI 使用 /r memory 等
        self._memory_used = False

        self._print_info = None
        self._print_llm_rsp = None
        self._print_llm_reasoning = None
        self._print_tool_call = None
        self._print_tool_result = None

        self.req_tokens = 0
        self.rsp_tokens = 0


    @staticmethod
    def _init_memory_manager():
        """根据全局配置初始化 MemoryManager，CLI 层无需感知。"""
        from src.utility.config_loader import global_cfg
        from src.memory.memory_manager import MemoryManager

        mem_enabled = getattr(global_cfg.memory, 'enabled', False)

        if not mem_enabled:
            return None

        mem_cfg_dict = {
            'enabled': getattr(global_cfg.memory, 'enabled', True),
            'storage_path': getattr(global_cfg.memory, 'storage_path', '.memdir'),
            'similarity_threshold': getattr(global_cfg.memory, 'similarity_threshold', 0.15),
            'short_term_max_entries': getattr(global_cfg.memory, 'short_term_max_entries', 50),
            'short_term_max_tokens': getattr(global_cfg.memory, 'short_term_max_tokens', 8000),
            'compress_batch_size': getattr(global_cfg.memory, 'compress_batch_size', 20),
            'working_memory_max_tokens': getattr(global_cfg.memory, 'working_memory_max_tokens', 2000),
            'long_term_max_inject': getattr(global_cfg.memory, 'long_term_max_inject', 5),
            'forget_older_than_days': getattr(global_cfg.memory, 'forget_older_than_days', 30),
            'forget_importance_below': getattr(global_cfg.memory, 'forget_importance_below', 0.2),
        }
        memory_manager = MemoryManager(config={'memory': mem_cfg_dict})

        # 为 MemoryCompressor 注入 LLM 回调
        def _llm_call_simple(messages, max_tokens, temperature):
            result, _, _ = chat_llm.chat_with_retry(messages)
            return result

        memory_manager.set_llm_call(_llm_call_simple)

        return memory_manager


    def get_tokens(self):
        return self.req_tokens, self.rsp_tokens


    def run(
            self,
            user_input: str,
            on_context_mgr: Callable[[str], AbstractContextManager],
            print_info: Callable[[str], None],
            print_llm_rsp: Callable[[str], None],
            print_tool_call: Callable[[str, Dict], None],
            print_tool_result: Callable[[str], None],
            print_llm_reasoning: Callable[[str], None] = None,
    ):

        # 赋值
        self._print_info = print_info
        self._print_llm_rsp = print_llm_rsp
        self._print_tool_call = print_tool_call
        self._print_tool_result = print_tool_result
        self._print_llm_reasoning = print_llm_reasoning

        """
        每一次 query_loop.run 的调用，都是与LLM的一次新的session
        相关变量都需要重新初始化
        """
        turn = 0
        quit_chat = ChatOrNot.Continue

        self.api_messages = llm_api_msg.LLMAPIMessage()
        self.session = SessionLog()
        self.max_turns = global_cfg.cli.max_turns
        self.is_chat_mode = True

        # 新任务开始，清空上一轮的工作记忆
        if self._memory_manager is not None:
            try:
                self._memory_manager.clear_working_memory()
            except Exception as e:
                self._print_info(f"[清空工作记忆失败] {e}")

        # 开始跟LLM多次循环交互，目的是为了完成用户任务（user_input）
        while turn < self.max_turns:
            turn += 1

            """1、发送请求给LLM"""
            with on_context_mgr(f"Thinking-{turn}"):  # 显示“thinking”在闪烁，表明系统未死，只是在等LLM的回复
                # 发送请求给LLM，前期准备
                thinking_begin = self._on_llm_req(turn, user_input)

                # 发送请求给LLM
                ai_response, is_truncated, reasoning_content = chat_llm.chat_with_retry(self.api_messages.get_msg())

            """2. 解构 LLM response"""
            tools = self._on_llm_rsp(turn, user_input, thinking_begin, ai_response, reasoning_content)

            """3. 开始处理工具"""
            quit_chat = self._handle_tools(tools)

            if not quit_chat == ChatOrNot.Continue:  # 如果不是继续chat，那就break循环
                break

        # 退出了循环
        if turn >= self.max_turns and quit_chat == ChatOrNot.Continue:  # 这种情况表明，实际上LLM并没有找到正确答案，但是，强制退出了
            self._print_info(f"达到最大轮次限制 ({self.max_turns})，强制结束")
        # 否则的话，就是正常退出，这里不用打印任何信息

        # 确保最后一个 Turn 的内容被持久化
        self.session.flush_turn()

        # 估算tokens
        req_tokens, rsp_tokens = self.session.get_tokens()
        self.req_tokens += req_tokens
        self.rsp_tokens += rsp_tokens

        # 会话结束时：持久化工作记忆 → 压缩 → 遗忘
        if self._memory_manager is not None and self._memory_used:
            try:
                count = self._memory_manager.persist_working_to_short()
                logger.info(f"已持久化 {count} 条工作记忆为短期记忆")

                compressed_count = self._memory_manager.compress_short_term()
                logger.info(f"本次压缩生成 {compressed_count} 条长期记忆")

                forgot_count = self._memory_manager.forget()
                logger.info(f"本次遗忘 {forgot_count} 条长期记忆")
            except Exception as e:
                logger.error(f"记忆生命周期处理失败: {e}")


    """
    1. 本来想着，这里跟CLI那个模块，完全解耦，但是发现做不到
    2. 只能做到部分解耦：
       2.1 什么时候打印，打印什么内容，本该是CLI模块的内容，还是放在这里实现了
       2.2 打印的方法与技术，对这里透明，由CLI模块“注册”进来
    3. 纠结了好几天，最终决定与自己和解，不再追求完全解耦了
    """

    # _on_llm_req，表示在发请求信息给LLM之前，做的（部分）事情，可能不是所有事情
    def _on_llm_req(self, turn, user_input):
        # 第一轮，需要初始化
        if turn == 1:
            self.session.init_session()

            self.api_messages.init_api_msg(user_input)

            # 注入记忆上下文（如果启用了记忆模块）
            if self._memory_manager is not None:
                self._memory_used = True  # 只要启用记忆，就需要走生命周期流程
                try:
                    mem_context = self._memory_manager.inject_context(
                        current_query=user_input
                    )
                    if mem_context:
                        wrapped = (
                            "[系统提醒] 以下是与当前任务相关的历史记忆，仅供参考：\n\n"
                            + mem_context
                        )
                        self.api_messages.append_micro_info("user", wrapped)
                except Exception as e:
                    self._print_info(f"[记忆注入失败] {e}")

        # 倒数最后一轮，命令式提醒
        if turn == self.max_turns and not self.is_chat_mode:
            command = "命令：如果你已完成所有修改，请立即调用 <llm_tool>done</llm_tool> 结束任务。不要继续调用其他工具。"
            self.api_messages.append_micro_info("user", command)

        # 事前记录轮次及发送给LLM的req
        self.session.log_turn(turn)
        self.session.log_llm_req(self.api_messages.get_msg())

        # 这里的thinking，不是LLM的thinking，是LLM的整个应答
        thinking_begin = datetime.now().strftime("%Y-%m-%d %H : %M : %S")

        return thinking_begin


    def _on_llm_rsp(self, turn, user_input, thinking_begin, ai_response, reasoning_content):
        # LLM回答结束的时间戳
        thinking_end = datetime.now().strftime("%Y-%m-%d %H : %M : %S")

        # 记录推理内容（如果提供商支持）
        self.session.log_reasoning_content(reasoning_content)

        # 记录LLM回应的原始内容（日志保留完整内容）
        self.session.log_llm_rsp(ai_response)

        # 去除thinking部分（针对 Claude 风格，DeepSeek 无此部分）
        ai_response_clean = strip_thinking(ai_response)

        '''
        # ---------- 新增：压缩 assistant 消息 ----------
        compressed_response = self._compress_assistant_message(ai_response_clean)
        
        # 将压缩后的响应追加到历史消息中（用于下一轮对话）
        self.api_messages.append_llm_response(compressed_response)
        # -----------------------------------------
        '''

        # 或者是直接附上原始的LLM的response（去除thinking部分），现在看来，LLM response消息不能压缩后再扔回去
        self.api_messages.append_llm_response(ai_response_clean)

        # 是否给用户显示LLM的think过程
        if not self.show_thinking:
            ai_response_show = ai_response_clean
        else:
            ai_response_show = ai_response

        remaining_text, tools = tool_executor.parse_tools(ai_response_show)

        # 如果 ai_response 中没有工具，尝试从 reasoning_content 宽松提取工具（兜底）
        if reasoning_content and not tools:
            _, tools_from_reasoning = tool_executor.parse_tools(reasoning_content)
            if tools_from_reasoning:
                tools = tools_from_reasoning
                remaining_text = ""   # 确保不重复打印

        # 打印部分 LLM response（有些内容不打印，显示一分神秘感）
        self._print_info(f"Thinking-{turn}, 开始时间：{thinking_begin}")

        # 打印推理内容给前端（打字机效果，完成后自动折叠）
        if reasoning_content:
            if self._print_llm_reasoning:
                self._print_llm_reasoning(reasoning_content)
            else:
                self._print_llm_rsp(reasoning_content)

        self._print_info(f"Thinking-{turn}, 结束时间：{thinking_end}")

        if remaining_text:
            self._print_llm_rsp(remaining_text)

        # 每轮对话后，将摘要存入工作记忆
        if self._memory_manager is not None:
            try:
                # 用户输入摘要（截取前100个字符）
                user_summary = user_input[:100] if user_input else ""

                # LLM 推理过程摘要
                thinking_summary = ""
                if reasoning_content:
                    thinking_summary = reasoning_content[:250].strip()
                    if len(reasoning_content) > 250:
                        thinking_summary += "..."

                # LLM 应答摘要
                response_summary = ""
                if remaining_text:
                    response_summary = remaining_text[:250].strip()
                    if len(remaining_text) > 250:
                        response_summary += "..."

                # 工具调用摘要
                tool_details = []
                for t in tools:
                    name = t.get("llm_tool", "")
                    params = t.get("params", {})
                    path = params.get("path", "")
                    summ = params.get("summary", "")
                    if name in ("create", "str_replace") and path:
                        detail = f"{name}({path}"
                        if summ:
                            detail += f"{summ}"
                        detail += ")"
                    elif name == "file_view" and path:
                        detail = f"file_view({path})"
                    elif name == "bash":
                        detail = "bash"
                    elif name == "done":
                        detail = "done"
                    else:
                        detail = name
                    tool_details.append(detail)
                tool_summary = "; ".join(tool_details) if tool_details else "无工具调用"

                # 构造换行分隔的 content（方便直接阅读）
                content_parts = [f"[Turn {turn}]"]
                content_parts.append(f"用户输入: {user_summary}")
                if thinking_summary:
                    content_parts.append(f"LLM推理过程: {thinking_summary}")
                if response_summary:
                    content_parts.append(f"LLM应答: {response_summary}")
                content_parts.append(f"LLM工具调用: {tool_summary}")
                memory_content = "\n".join(content_parts)

                # 截断到 800 字符以内
                if len(memory_content) > 800:
                    memory_content = memory_content[:797] + "..."

                # 构造结构化 metadata（保留完整字段，供 memory_injector 格式化展示）
                metadata = {
                    "turn": turn,
                    "user_input": user_summary,
                    "llm_reasoning": thinking_summary,
                    "llm_response": response_summary,
                    "llm_tool_call": tool_summary
                }

                self._memory_manager.add_memory(
                    content=memory_content,
                    mem_type="working",
                    importance=0.5,
                    metadata=metadata
                )
            except Exception as e:
                self._print_info(f"[记忆存储失败] {e}")

        return tools


    def _handle_tools(self, tools):
        # 1. 如果 LLM response 中没有工具，那么直接会话结束
        if not tools:
            # 如果是非聊天模式（那就是编码模式），须提示用户一句：流程结束了；如果是聊天模式，那就直接结束
            if not self.is_chat_mode:
                self._print_info("LLM 未调用 done 工具，但已无后续操作，自动结束")
                self.session.log_dict_info({"role": "system", "content": "LLM 未调用 done 工具，但已无后续操作，自动结束"})

            return ChatOrNot.QuitByNoneTool

        # 如果 LLM response 中有工具，那就不是单纯的聊天
        self.is_chat_mode = False

        # 既然有工具，那就执行工具'''
        done_tools = [t for t in tools if t["llm_tool"] == "done"]
        exec_tools = [t for t in tools if t["llm_tool"] != "done"]

        # 执行普通工具
        if exec_tools:
            self._print_info(f"执行 {len(exec_tools)} 个工具...")

            for t in exec_tools:
                self._print_tool_call(t["llm_tool"], t["params"])  # 打印：工具名称，工具参数
                self.session.log_tool_call(t["llm_tool"], t["params"])

                result_msg = tool_executor.execute_code_tool(t)  # 工具执行

                self._print_tool_result(t["llm_tool"], result_msg.get("content", ""))  # 打印：工具执行结果
                self.session.log_tool_result(t["llm_tool"], result_msg)

                # 将 tool 的执行结果，append 到 api_messages
                self.api_messages.append_tool_exec_result(result_msg)

        # 处理 done
        if done_tools:
            msg = done_tools[0]["params"].get("message", "任务完成")
            self._print_info(msg)
            self.session.log_dict_info({"role": "assistant", "content": msg})

            return ChatOrNot.QuitByDone

        # self._print_info("no tools")
        return ChatOrNot.Continue


    @staticmethod
    def _compress_assistant_message(raw: str) -> str:
        """
        将 assistant 消息中的工具调用压缩为简短形式。
        - <create>...</create>  -> <create path="..." summary="..."/>
        - <str_replace>...</str_replace> -> <str_replace path="..." summary="..."/>
        其他标签原样保留（已足够短）。
        """
        import re

        # 压缩 <create> 标签
        def replace_create(match):
            path = match.group(1)
            summary = match.group(2) or ""
            # 可选：截断摘要（但 LLM 已被要求 ≤50 字符，这里暂不处理）
            return f'<create path="{path}" summary="{summary}"/>'

        create_pattern = re.compile(
            r'<create\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>(.*?)</create>',
            re.DOTALL
        )
        compressed = create_pattern.sub(replace_create, raw)

        # 压缩 <str_replace> 标签
        def replace_str_replace(match):
            path = match.group(1)
            summary = match.group(2) or ""
            return f'<str_replace path="{path}" summary="{summary}"/>'

        str_replace_pattern = re.compile(
            r'<str_replace\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>.*?</str_replace>',
            re.DOTALL
        )
        compressed = str_replace_pattern.sub(replace_str_replace, compressed)

        # file_view, bash，原样已足够短，无需改动
        '''
        bash_pattern = re.compile(r'<bash>(.*?)</bash>', re.DOTALL)
        compressed = bash_pattern.sub(r'<bash command="\1"/>', compressed)
        '''

        return compressed
