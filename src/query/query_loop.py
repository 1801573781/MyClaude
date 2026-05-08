from enum import Enum
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Callable, Dict
from query import chat_llm
from message import llm_api_msg
from llm_tool import tool_executor
from utility.config_loader import global_cfg
from utility.normal_utility import strip_thinking
from query.session_log import SessionLog


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

        self._print_info = None
        self._print_llm_rsp = None
        self._print_tool_call = None
        self._print_tool_result = None

        self.req_tokens = 0
        self.rsp_tokens = 0


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
    ):

        # 赋值
        self._print_info = print_info
        self._print_llm_rsp = print_llm_rsp
        self._print_tool_call = print_tool_call
        self._print_tool_result = print_tool_result

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

        # 开始跟LLM多次循环交互，目的是为了完成用户任务（user_input）
        while turn < self.max_turns:
            turn += 1

            """1、发送请求给LLM"""
            with on_context_mgr(f"Thinking-{turn}"):  # 显示“thinking”在闪烁，表明系统未死，只是在等LLM的回复
                # 发送请求给LLM，前期准备
                thinking_begin = self._on_llm_req(turn, user_input)

                # 发送请求给LLM
                ai_response = chat_llm.chat_with_retry(self.api_messages.get_msg())

            """2. 解构 LLM response"""
            tools = self._on_llm_rsp(turn, thinking_begin, ai_response)

            """3. 开始处理工具"""
            quit_chat = self._handle_tools(tools)

            if not quit_chat == ChatOrNot.Continue:  # 如果不是继续chat，那就break循环
                break

        # 退出了循环
        if turn >= self.max_turns and quit_chat == ChatOrNot.Continue:  # 这种情况表明，实际上LLM并没有找到正确答案，但是，强制退出了
            self._print_info(f"达到最大轮次限制 ({self.max_turns})，强制结束")
        # 否则的话，就是正常退出，这里不用打印任何信息

        # 估算tokens
        req_tokens, rsp_tokens = self.session.get_tokens()
        self.req_tokens += req_tokens
        self.rsp_tokens += rsp_tokens


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


    def _on_llm_rsp(self, turn, thinking_begin, ai_response):
        # LLM回答结束的时间戳
        thinking_end = datetime.now().strftime("%Y-%m-%d %H : %M : %S")

        # 记录LLM回应的rsp
        self.session.log_llm_rsp(ai_response)

        """解构 LLM response"""

        # 去除thinking部分
        ai_response_clean = strip_thinking(ai_response)

        # 如果还需要跟LLM再一轮交互的话，需附加上LLM的response（去除thinking部分）
        self.api_messages.append_llm_response(ai_response_clean)

        # 是否给用户显示LLM的think过程
        if not self.show_thinking:
            ai_response_show = ai_response_clean
        else:
            ai_response_show = ai_response

        remaining_text, tools = tool_executor.parse_tools(ai_response_show)

        """打印部分 LLM response（有些内容不打印，显示一分神秘感）"""

        # 打印轮次，开始时间
        self._print_info(f"Thinking-{turn}, 开始时间：{thinking_begin}")

        # 然后打印 LLM response
        if remaining_text:
            self._print_llm_rsp(remaining_text)

        # 打印轮次，结束时间
        self._print_info(f"Thinking-{turn}, 结束时间：{thinking_end}")

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
