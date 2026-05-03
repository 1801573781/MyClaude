import sys
from pathlib import Path

# ===== 兼容 PyCharm / cmd 各种运行方式 =====
# 把当前文件所在目录（src/message/）加入 sys.path，确保能找到同目录的 sys_prompt
_current_dir = Path(__file__).parent.resolve()
if str(_current_dir) not in sys.path:
    sys.path.insert(0, str(_current_dir))
# ============================================

import sys_prompt


# 通过API与LLM交互时，Message的构建
class LLMAPIMessage:

    def __init__(self):
        self.api_messages = sys_prompt.system_prompt.copy()  # 复制系统提示词列表


    def get_msg(self):
        return self.api_messages


    def init_api_msg(self, user_input):
        """
        初始化发送给 LLM 的 messages 列表：复制系统提示词，追加用户消息。
        """
        msg = {
            "role": "user",
            "content": user_input
        }

        self._append_info(msg)

    # 尾部添加微信息
    def append_micro_info(self, role, micro_info):
        """
        代码看起来很简单，就是 api_messages.append(micro_info)，但是这里蕴含着一种思想：
        如果添加的是微信息，那么就不涉及对 api_messages 中的 memory 的压缩等操作，
        直接将微信息 append 到 api_messages 尾部即可
        """
        msg = {
            "role": role,
            "content": micro_info
        }

        self._append_info(msg)

    # 尾部添加微 LLM 的 response
    def append_llm_response(self, llm_response):
        """
        1、现在，先简单地：api_messages.append(llm_response)
        2、后续，要考虑转记忆、压缩等等
        """
        msg = {
            "role": "assistant",
            "content": llm_response
        }

        self._append_info(msg)

    # 尾部添加 tool 执行结果
    def append_tool_exec_result(self, result_msg):
        # 因为result_msg直接就是 dict，所以直接append即可
        self._append_info(result_msg)

    # 这是一个内部的简化实现
    def _append_info(self, msg):
        self.api_messages.append(msg)
