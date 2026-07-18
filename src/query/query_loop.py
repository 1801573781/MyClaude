from enum import Enum
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Callable, Dict
from src.query import chat_llm
from src.utility import llm_api_msg
from src.llm_tool import tool_executor
from src.utility.config_loader import global_cfg
from src.utility.normal_utility import strip_thinking
from src.query.session_log import SessionLog
from src.query.todo_manager import TodoManager
from src.memory.factory import create_memory
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


    def __init__(self, show_thinking: bool = False, role: str = "mycode"):
        self.show_thinking = show_thinking
        self.role = role

        self.api_messages = llm_api_msg.LLMAPIMessage(role=self.role)
        self.session = SessionLog()
        self.max_turns = 0
        self.is_multi_turns = None
        self._query_counter = 0

        # 通过工厂函数创建记忆实例（根据 config.yaml memory.backend 选择后端）
        self._init_memory()
        self._memory_used = False

        self._print_info = None
        self._print_llm_rsp = None
        self._print_llm_reasoning = None
        self._print_tool_call = None
        self._print_tool_result = None

        # 精确 token 统计（从 API usage 获取，非粗略估算）
        self.prompt_cache_hit = 0    # 输入（命中缓存）
        self.prompt_cache_miss = 0   # 输入（未命中缓存）
        self.completion_tokens = 0   # 输出

        # 追问无工具兜底计数器
        self._no_tool_retry = 0

        # 斜杠命令上下文（None 表示普通对话模式）
        self._command_context = None

        # 死循环熔断：记录上一轮的工具签名（用于 AskUserQuestion 重复提问检测）
        self._last_tool_sig = None

        # TodoWrite 有状态进度管理
        self._todo_manager = TodoManager()
        self._on_todo_update = None  # 回调：todo 列表更新时通知 CLI 渲染
        self._last_todo_sig = None  # 死循环熔断：上一轮 todo 状态签名


    def _init_memory(self):
        """通过工厂函数创建记忆实例，容错降级为 NoopMemory。"""
        try:
            self._memory = create_memory(global_cfg)
            logger.info(f"记忆模块初始化成功: {type(self._memory).__name__}")
        except Exception as e:
            logger.warning(f"记忆模块初始化失败，降级为 NoopMemory: {e}")
            from src.memory.memory_interface import NoopMemory
            self._memory = NoopMemory()


    def clear_memory(self) -> int:
        """清除所有记忆（封装调用，CLI 不直接接触记忆模块细节）。

        Returns:
            清除的记忆条数；若记忆模块未启用，返回 0
        """
        if self._memory is None:
            return 0
        return self._memory.clear_all()

    def reset_context(self):
        """重置上下文：保留 system prompt 及初始化消息，清空对话历史。
        保留此方法用于内部调用，CLI 层不再直接映射到 /r ctx 命令。
        """
        if self.api_messages:
            self.api_messages.reset_context()
            logger.info("上下文已重置（保留系统提示词，清空对话历史）")


    def new_session(self) -> int:
        """开启一个新的 Session：清空 api_messages（仅保留 system_prompt）、
        清空记忆、创建新的 SessionLog 实例。

        Returns:
            清除的记忆条数；若记忆模块未启用，返回 0
        """
        # 1. 重置 api_messages（仅保留 system_prompt）
        self.api_messages.reset_context()

        # 2. 清空记忆
        cleared = self.clear_memory()

        # 3. 创建新的 SessionLog 实例
        self.session = SessionLog()

        # 4. 重置 Query 计数器
        self._query_counter = 0

        # 5. 重置 token 统计
        self.prompt_cache_hit = 0
        self.prompt_cache_miss = 0
        self.completion_tokens = 0

        logger.info(f"新 Session 已开启，清除记忆 {cleared} 条")
        return cleared


    def get_tokens(self):
        """返回详细的 token 统计字典。
        keys: prompt_cache_hit, prompt_cache_miss, completion_tokens, total
        """
        total = self.prompt_cache_hit + self.prompt_cache_miss + self.completion_tokens
        return {
            "prompt_cache_hit": self.prompt_cache_hit,
            "prompt_cache_miss": self.prompt_cache_miss,
            "completion_tokens": self.completion_tokens,
            "total": total,
        }


    def run(
            self,
            user_input: str,
            on_context_mgr: Callable[[str], AbstractContextManager],
            print_info: Callable[[str], None],
            print_llm_rsp: Callable[[str], None],
            print_tool_call: Callable[[str, Dict], None],
            print_tool_result: Callable[[str, str, dict | None], None],
            print_llm_reasoning: Callable[[str, int], None] = None,
            command_context: dict | None = None,
            on_todo_update: Callable[[dict], None] = None,
    ):

        # 赋值
        self._print_info = print_info
        self._print_llm_rsp = print_llm_rsp
        self._print_tool_call = print_tool_call
        self._print_tool_result = print_tool_result
        self._print_llm_reasoning = print_llm_reasoning
        self._on_todo_update = on_todo_update

        """
        每一次 query_loop.run 的调用，都是与 LLM 的一次新 Query。
        api_messages 跨 Query 累积，不在此处重置。
        只有 /new session 才会清空上下文。
        """
        turn = 0
        quit_chat = ChatOrNot.Continue

        self._query_counter += 1
        self.session.start_query(self._query_counter, user_input)
        self.max_turns = global_cfg.cli.max_turns
        self._no_tool_retry = 0  # 每次新 Turn 重置追问计数器
        self.is_multi_turns = None  # 每次 Turn 开始时未确定
        self._todo_manager.reset()  # 每次新 Turn 重置 todo 列表

        # 如果有斜杠命令上下文，用命令内容重置 api_messages
        self._command_context = command_context
        if command_context:
            self.api_messages.reset_with_command(
                command_name=command_context["command_name"],
                command_content=command_context["command_content"],
                user_argument=command_context["user_argument"],
            )

        # 新任务开始，执行记忆维护（遗忘过期记忆，不删除持久化数据）
        try:
            self._memory.maintain()
        except Exception as e:
            logger.warning(f"记忆维护失败: {e}")

        # 开始跟LLM多次循环交互，目的是为了完成用户任务（user_input）
        while turn < self.max_turns:
            turn += 1

            """1、发送请求给LLM"""
            with on_context_mgr(f"Thinking-{turn}"):  # 显示“thinking”在闪烁，表明系统未死，只是在等LLM的回复
                # 发送请求给LLM，前期准备
                thinking_begin = self._on_llm_req(turn, user_input)

                # 发送请求给LLM
                ai_response, is_truncated, reasoning_content, usage = chat_llm.chat_with_retry(self.api_messages.get_msg())  # noqa E501

                # 累积精确 token 统计
                if usage:
                    cached = usage.get("cached_tokens", 0)
                    prompt_total = usage.get("prompt_tokens", 0)
                    self.prompt_cache_hit += cached
                    self.prompt_cache_miss += (prompt_total - cached)
                    self.completion_tokens += usage.get("completion_tokens", 0)

            """2. 解构 LLM response"""
            tools, remaining_text = self._on_llm_rsp(turn, thinking_begin,
                                                     ai_response, reasoning_content)

            """3. 开始处理工具"""
            quit_chat, tool_exec_info = self._handle_tools(tools)

            # 每轮对话后，存储完整轮次记忆（用户问题 + LLM思考 + 应答 + 工具执行）
            if self._memory_used and user_input:
                self._save_turn_memory(turn, user_input, reasoning_content,
                                       remaining_text, tool_exec_info)

            if not quit_chat == ChatOrNot.Continue:  # 如果不是继续chat，那就break循环
                break

        # 退出了循环
        if turn >= self.max_turns and quit_chat == ChatOrNot.Continue:  # 这种情况表明，实际上LLM并没有找到正确答案，但是，强制退出了
            self._print_info(f"达到最大轮次限制 ({self.max_turns})，强制结束")
        # 否则的话，就是正常退出，这里不用打印任何信息

        # 确保最后一个 Turn 的内容被持久化，并关闭当前 Query
        self.session.end_query()

        # 会话结束时：执行记忆维护（压缩 + 遗忘）
        if self._memory_used:
            try:
                self._memory.compact()
                self._memory.maintain()
                logger.info("记忆生命周期维护完成")
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
        # 第一轮，需要初始化（init_session 内部有守护，只执行一次）
        if turn == 1:
            self.session.init_session()

            # 斜杠命令模式：api_messages 已由 reset_with_command 设置好，跳过 init_api_msg
            if self._command_context:
                # 用命令名+参数作为记忆检索的查询文本
                query_text = self._command_context.get("user_argument") or user_input
            else:
                self.api_messages.init_api_msg(user_input)
                query_text = user_input

            # 注入记忆上下文（通过 MemoryInterface 统一接口）
            # 先用 user_input 触发检索，再注入检索结果 + 工作记忆
            try:
                mem_context = self._memory.get_context_for_query(query_text)
                self._memory_used = True

                # 解析检索结果数量，打印 [记忆召回] 信息（即使0条也打印）
                recall_count = self._count_recalled(mem_context)
                self._print_info(f"[记忆召回] 已召回 {recall_count} 条相关记忆")

                if mem_context:
                    self.api_messages.append_micro_info("user", mem_context)
                    self.session.log_dict_info({"role": "user", "content": mem_context})
                    logger.debug(f"记忆上下文已注入，长度: {len(mem_context)}")
            except Exception as e:
                logger.warning(f"记忆上下文注入失败: {e}")

        # 倒数最后一轮，命令式提醒
        if turn == self.max_turns and self.is_multi_turns:
            command = "命令：如果你已完成所有修改，请立即调用 <llm_tool>done</llm_tool> 结束任务。不要继续调用其他工具。"
            self.api_messages.append_micro_info("user", command)

        # 每轮注入 [TODO_STATUS] 上下文（如果有活跃的 todo 列表）
        todo_context = self._todo_manager.get_context_message()
        if todo_context:
            self.api_messages.append_micro_info("user", todo_context)
            self.session.log_dict_info({"role": "user", "content": todo_context})

        # 事前记录轮次及发送给LLM的req
        self.session.log_turn(turn)
        self.session.log_llm_req(self.api_messages.get_msg())

        # 这里的thinking，不是LLM的thinking，是LLM的整个应答
        thinking_begin = datetime.now().strftime("%Y-%m-%d %H : %M : %S")

        return thinking_begin


    def _on_llm_rsp(self, turn, thinking_begin, ai_response, reasoning_content):
        # LLM回答结束的时间戳
        thinking_end = datetime.now().strftime("%Y-%m-%d %H : %M : %S")

        # 记录推理内容（如果提供商支持）
        self.session.log_reasoning_content(reasoning_content)

        # 记录LLM回应的原始内容（日志保留完整内容）
        self.session.log_llm_rsp(ai_response)

        # 去除thinking部分（针对 Claude 风格，DeepSeek 无此部分）
        ai_response_clean = strip_thinking(ai_response)

        # 如果实际响应为空但推理内容存在，使用推理内容作为有效响应
        # （某些 LLM 将所有内容放在思考过程中，实际响应为空）
        if not ai_response_clean.strip() and reasoning_content:
            ai_response_clean = reasoning_content.strip()

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

        # 仅从 ai_response 解析工具（不 fallback 到 reasoning_content）
        remaining_text, tools = tool_executor.parse_tools(ai_response_show)

        # 打印部分 LLM response（有些内容不打印，显示一分神秘感）
        self._print_info(f"Thinking-{turn}, 开始时间：{thinking_begin}")

        # 打印推理内容给前端（打字机效果，完成后自动折叠）
        if reasoning_content:
            if self._print_llm_reasoning:
                self._print_llm_reasoning(reasoning_content, turn)
            else:
                self._print_llm_rsp(reasoning_content)

        self._print_info(f"Thinking-{turn}, 结束时间：{thinking_end}")

        if remaining_text:
            self._print_llm_rsp(remaining_text)

        # 记忆存储已移至 run() 中的 _save_turn_memory()，统一打包整轮对话

        return tools, remaining_text


    def _get_follow_up_prompt(self, retry_count: int) -> str:
        """根据追问次数返回递进式的提醒消息，给 LLM 两条路：输出工具或输出 done。

        Args:
            retry_count: 追问次数（1, 2, 3）

        Returns:
            对应次数的追问消息文本
        """
        prompts = {
            1: "[系统提醒] 你既没有输出工具，也没有输出 <done>。如果任务尚未完成，请继续输出下一个工具。如果你在等待用户的回应，请输出 <done> 结束本轮。",
            2: "[系统追问] 请立即输出工具继续执行，或输出 <done> 结束本轮。",
            3: "[最终提醒] 这是你最后的机会。输出工具继续，或输出 <done> 结束。否则本轮将自动结束，等待用户输入。",
        }
        return prompts.get(retry_count, prompts[3])

    def _follow_up_for_tools(self):
        """无工具时追问 LLM 最多 3 次，尝试获取工具列表。

        追问成功后，将追问消息和 LLM 回复追加到正式 api_messages，
        确保后续工具执行结果前面有完整的 assistant 消息，避免上下文断裂。

        Returns:
            解析到的工具列表；若 3 次追问仍无工具，返回空列表。
        """
        import copy

        if self._no_tool_retry >= 3:
            return []

        self._no_tool_retry += 1
        prompt = self._get_follow_up_prompt(self._no_tool_retry)
        # 方案 E：第一次追问静默（不打印到屏幕），只注入给 LLM
        silent = (self._no_tool_retry == 1)
        if not silent:
            self._print_info(f"[追问 {self._no_tool_retry}/3] {prompt}")

        try:
            # 构建临时消息列表（深拷贝，不污染正式对话历史）
            temp_msgs = copy.deepcopy(self.api_messages.get_msg())
            temp_msgs.append({"role": "user", "content": prompt})

            ai_response, is_truncated, reasoning_content, usage = chat_llm.chat_with_retry(temp_msgs)

            # 记录追问对话到 session（用于日志审计）
            reason = f"[系统追问] Turn 中未检测到工具调用，发起第 {self._no_tool_retry}/3 次追问，尝试获取工具或 done 信号"
            self.session.log_dict_info({"role": "system", "content": reason})
            self.session.log_turn(-self._no_tool_retry)  # 负数 turn 表示追问
            self.session.log_llm_req(temp_msgs)
            self.session.log_llm_rsp(ai_response)
            self.session.log_reasoning_content(reasoning_content)
        except Exception as e:
            logger.error(f"追问 LLM 失败: {e}")
            return []

        # 解析工具（仅从 ai_response，不回退到 reasoning_content）
        ai_response_clean = strip_thinking(ai_response)

        # 如果实际响应为空但推理内容存在，使用推理内容作为有效响应
        if not ai_response_clean.strip() and reasoning_content:
            ai_response_clean = reasoning_content.strip()

        _, tools = tool_executor.parse_tools(ai_response_clean)

        if tools:
            if not silent:
                self._print_info(f"[追问结果] 成功获取到 {len(tools)} 个工具，进入执行")
            # 追问成功：将追问消息和 LLM 回复追加到正式 api_messages，
            # 确保后续工具执行结果前面有完整的 assistant 消息，避免上下文断裂
            self.api_messages.append_micro_info("user", prompt)
            self.api_messages.append_llm_response(ai_response_clean)
        else:
            if not silent:
                self._print_info(f"[追问结果] 第 {self._no_tool_retry}/3 次仍未获得工具")

        return tools


    def _handle_tools(self, tools):
        """执行工具并返回 (quit_chat, tool_exec_info)。
        tool_exec_info 为列表，每个元素是 {"tool": 工具名, "params": 参数, "result": 结果文本}。
        """
        # 1. 如果 LLM response 中没有工具
        if not tools:
            # 第一轮（is_multi_turns 未确定）：无工具无 done → 单轮场景
            if self.is_multi_turns is None:
                self.is_multi_turns = False
                self.session.log_dict_info({"role": "system", "content": "LLM 未调用工具，本轮结束，等待用户输入"})
                return ChatOrNot.QuitByNoneTool, []

            # is_multi_turns = True 的后续轮次：无工具无 done → 触发追问
            while self._no_tool_retry < 3:
                tools = self._follow_up_for_tools()
                if tools:
                    # 追问得到了工具，重置计数器
                    self._no_tool_retry = 0
                    break
                # 未获得工具，_follow_up_for_tools 已自增 _no_tool_retry，继续循环追问
            if not tools:
                # 追问无果，兜底结束 Turn
                self._print_info("追问 3 次后仍未获得工具，本轮结束，等待用户输入")
                self.session.log_dict_info({"role": "system", "content": "追问 3 次后仍未获得工具，本轮结束，等待用户输入"})
                return ChatOrNot.QuitByNoneTool, []
            # 继续往下执行（会进入后面的 exec_tools / done_tools 处理）

        # 有工具时重置追问计数器
        self._no_tool_retry = 0

        # 第一轮有工具 → 动态确定为多轮场景
        if self.is_multi_turns is None:
            self.is_multi_turns = True

        # 既然有工具，那就执行工具'''
        done_tools = [t for t in tools if t["llm_tool"] == "done"]
        exec_tools = [t for t in tools if t["llm_tool"] != "done"]

        # ===== AskUserQuestion 特殊处理 =====
        has_ask_user = any(t["llm_tool"] == "AskUserQuestion" for t in exec_tools)

        if has_ask_user:
            # 死循环熔断：检查是否连续两轮问了相同问题
            ask_tools = [t for t in exec_tools if t["llm_tool"] == "AskUserQuestion"]
            current_sig = "AskUserQuestion:" + "|".join(
                t["params"].get("question", "") for t in ask_tools
            )
            if self._last_tool_sig == current_sig:
                self._print_info("[熔断] 检测到连续两轮提出相同问题，强制终止以避免死循环")
                self.session.log_dict_info({
                    "role": "system",
                    "content": "[熔断] AskUserQuestion 连续两轮相同问题，强制终止"
                })
                return ChatOrNot.QuitByNoneTool, []
            self._last_tool_sig = current_sig

            # AskUserQuestion 排到最前面优先执行
            exec_tools.sort(key=lambda t: 0 if t["llm_tool"] == "AskUserQuestion" else 1)
        else:
            self._last_tool_sig = None

        # TodoWrite 排到最前面优先执行（先更新计划，再执行动作）
        # 排序键：todowrite=0, AskUserQuestion=1, 其他=2
        exec_tools.sort(key=lambda t: 0 if t["llm_tool"] == "todowrite" else (1 if t["llm_tool"] == "AskUserQuestion" else 2))

        tool_exec_info = []

        # 执行普通工具
        if exec_tools:
            self._print_info(f"执行 {len(exec_tools)} 个工具...")

            for t in exec_tools:
                self._print_tool_call(t["llm_tool"], t["params"])  # 打印：工具名称，工具参数
                self.session.log_tool_call(t["llm_tool"], t["params"])

                # ===== TodoWrite 拦截：交给 TodoManager 处理 =====
                if t["llm_tool"] == "todowrite":
                    try:
                        result_msg = self._todo_manager.update_from_xml(
                            t["params"].get("content", "")
                        )
                        # 通知 CLI 渲染 todo 列表
                        if self._on_todo_update:
                            self._on_todo_update(self._todo_manager.get_display_data())
                    except Exception as e:
                        logger.error(f"TodoWrite 执行异常: {e}")
                        result_msg = {
                            "role": "user",
                            "content": f"[ERROR] TodoWrite 执行失败: {e}"
                        }
                else:
                    try:
                        result_msg = tool_executor.execute_code_tool(t)  # 工具执行
                    except Exception as e:
                        logger.error(f"工具执行异常 [{t['llm_tool']}]: {e}")
                        result_msg = {
                            "role": "user",
                            "content": f"[ERROR] 工具 {t['llm_tool']} 执行失败: {e}"
                        }

                self._print_tool_result(t["llm_tool"], result_msg.get("content", ""), t["params"])  # 打印：工具执行结果
                self.session.log_tool_result(t["llm_tool"], result_msg)

                # TodoWrite 执行后记录 todo 快照到会话日志
                if t["llm_tool"] == "todowrite":
                    self.session.log_todo_snapshot(self._todo_manager.get_display_data())

                # 将 tool 的执行结果，append 到 api_messages
                self.api_messages.append_tool_exec_result(result_msg)

                # 收集工具执行信息（用于记忆存储）
                tool_exec_info.append({
                    "tool": t["llm_tool"],
                    "params": t["params"],
                    "result": result_msg.get("content", "")[:500],  # 截断过长结果
                })

        # ===== AskUserQuestion 后跳过 done 检测 =====
        # 如果本轮包含 AskUserQuestion，忽略同轮的 done 信号，强制继续下一轮
        if has_ask_user:
            return ChatOrNot.Continue, tool_exec_info

        # ===== 死循环熔断：纯 todowrite 轮的状态变化检测 =====
        non_todo_tools = [t for t in exec_tools if t["llm_tool"] != "todowrite"]
        has_todowrite = any(t["llm_tool"] == "todowrite" for t in exec_tools)

        if has_todowrite and not non_todo_tools:
            # 纯 todowrite 轮：检查 todo 状态是否有变化
            current_todo_sig = self._todo_manager.todo_list.status_signature()
            if self._last_todo_sig is not None and self._last_todo_sig == current_todo_sig:
                self._print_info("[熔断] 连续两轮纯 todowrite 且状态无变化，强制终止")
                self.session.log_dict_info({
                    "role": "system",
                    "content": "[熔断] TodoWrite 连续两轮状态无变化，强制终止"
                })
                return ChatOrNot.QuitByNoneTool, tool_exec_info
            self._last_todo_sig = current_todo_sig
        else:
            # 有非 todowrite 工具，重置签名
            self._last_todo_sig = None

        # 处理 done
        if done_tools:
            msg = done_tools[0]["params"].get("message", "任务完成")
            self._print_info(msg)
            self.session.log_dict_info({"role": "assistant", "content": msg})

            self._todo_manager.reset()  # 任务完成，清空 todo 列表
            return ChatOrNot.QuitByDone, tool_exec_info

        # self._print_info("no tools")
        return ChatOrNot.Continue, tool_exec_info


    def _save_turn_memory(self, turn: int, user_input: str,
                          reasoning_content: str, remaining_text: str,
                          tool_exec_info: list) -> None:
        """将整轮对话打包为一条完整记忆（用户输入 + LLM 思考 + 应答 + 工具执行）。

        解决原有分开存储导致召回不完整的问题。
        """
        try:
            parts = []  # noqa E352

            # 1. 用户输入
            parts.append(f"[用户输入] {user_input}")

            # 2. LLM 思考过程
            if reasoning_content:
                parts.append(f"[LLM 思考] {reasoning_content}")

            # 3. LLM 应答
            if remaining_text:
                parts.append(f"[LLM 应答] {remaining_text}")

            # 4. 工具执行
            if tool_exec_info:
                for i, info in enumerate(tool_exec_info):
                    parts.append(f"[工具执行{i+1}] 工具: {info['tool']}")
                    parts.append(f"[工具参数{i+1}] {info['params']}")
                    parts.append(f"[工具结果{i+1}] {info['result']}")

            content = "\n\n".join(parts)
            self._memory.add("", content, metadata={
                "turn": turn,
                "has_tools": bool(tool_exec_info),
                "has_reasoning": bool(reasoning_content),
            })
            logger.debug(f"Turn {turn} 完整记忆已存储，长度: {len(content)}")
        except Exception as e:
            logger.warning(f"完整轮次记忆存储失败: {e}")


    @staticmethod
    def _count_recalled(mem_context: str) -> int:
        """从记忆注入上下文中统计所有召回的记忆条目数。

        统计所有以 ``- [`` 开头的行（包括检索记忆和工作记忆），
        确保 CLI 打印数量与 session_log / myclaude.log 记录一致。
        """
        count = 0
        for line in mem_context.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- [") and "id=" in stripped:
                count += 1
        return count


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
