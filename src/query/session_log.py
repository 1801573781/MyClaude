import json

from utility.config_loader import global_cfg
from utility.file_tool import file_append
from datetime import datetime


class SessionLog:

    def __init__(self):
        self.log_root = global_cfg.code_project.logs_root
        self.session_file_name = ""
        self.req_tokens = 0
        self.rsp_tokens = 0

    def init_session(self):
        now = datetime.now()
        self.session_file_name = "MyClaude " + now.strftime("%Y-%m-%d %H-%M-%S") + ".md"

        save_session = [
            {"time": now.strftime("%Y-%m-%d %H : %M : %S")},
            {"file name": self.session_file_name}
        ]

        self._save_session_log(save_session)


    def get_tokens(self):
        return self.req_tokens, self.rsp_tokens

    def log_turn(self, turn):
        dict_info = {"turn": turn}
        self.log_dict_info(dict_info)


    def log_llm_req(self, req_dict):
        # req_dict，直接就是个 dict
        self.log_dict_info(req_dict)

        # 统计给LLM发送请求的tokens（近似计算）
        json_str = json.dumps(req_dict, ensure_ascii=False)
        byte_size = len(json_str.encode('utf-8'))
        self.req_tokens += byte_size // 2  # 粗略估计, 2个字节，一个token


    def log_llm_rsp(self, llm_rsp):
        dict_info = {"role": "assistant", "content": llm_rsp}
        self.log_dict_info(dict_info)

        # 统计LLM输出的tokens（近似计算）
        self.rsp_tokens += len(llm_rsp) // 2  # 粗略估计, 2个字节，一个token


    def log_tool_call(self, tool_name, tool_paras):
        dict_info = {
            "tool_name": tool_name,
            "tool_paras": tool_paras
        }
        self.log_dict_info(dict_info)


    def log_tool_result(self, tool_name, result):
        dict_info = {
            "tool_name": tool_name,
            "exec_result": result
        }
        self.log_dict_info(dict_info)


    def log_dict_info(self, dict_info):
        save_session = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H : %M : %S")

        save_session.append({"time": timestamp})
        save_session.append(dict_info)

        self._save_session_log(save_session)


    """持久化 session 会话的历史"""
    def _save_session_log(self, save_session):
        if not save_session:
            return

            # 把未保存的条目格式化为 Markdown
        md_chunks = []
        for item in save_session:
            md_chunks.append(self._format_log_item(item))

        # chunk 之间只换行，末尾加分隔符作为批次分隔
        content = "\n\n".join(
            md_chunks) + "\n\n════════════════════════════════════════════════════════════════════════════════════\n\n"

        file_append(self.log_root, self.session_file_name, content)


    """把单个日志项（dict 或 list）格式化为 Markdown 字符串"""
    def _format_log_item(self, item) -> str:
        # 展平嵌套列表（比如 _log_llm_req 塞进来的 api_messages 列表）
        if isinstance(item, list):
            parts = [self._format_log_item(sub) for sub in item]
            return "\n\n".join(parts)

        if not isinstance(item, dict):
            return f"```\n{str(item)}\n```"

        lines = []

        # 时间戳
        if "time" in item:
            lines.append(f"**🕐 {item['time']}**")

        # 轮次标记
        if "turn" in item:
            lines.append(f"## 🔄 Turn {item['turn']}")

        # 会话文件名（初始化时）
        if "file name" in item:
            lines.append(f"> 📄 Session: `{item['file name']}`")

        # LLM 消息（system / user / assistant）
        if "role" in item:
            role = item["role"]
            content = item.get("content", "")
            emoji = {"system": "⚙️", "user": "👤", "assistant": "🤖"}.get(role, "📝")
            lines.append(f"### {emoji} {role.upper()}")
            lines.append("")
            # content 直接放入，保留换行，让它自然渲染 Markdown
            lines.append(content)

        # 工具调用记录
        if "tool_name" in item:
            tool = item["tool_name"]
            lines.append(f"### 🔧 Tool: `{tool}`")
            lines.append("")
            if "tool_paras" in item:
                # lines.append(f"**参数:** `{item['tool_paras']}`")
                paras = item["tool_paras"]
                if isinstance(paras, dict):
                    lines.append("**参数:**")
                    if "path" in paras:
                        lines.append(f"- 路径: `{paras['path']}`, 文件内容略")
                    if "command" in paras:
                        lines.append(f"- 命令: `{paras['command']}`")
                    # content 不再重复打印，因为 assistant 消息里已经有了完整代码
                else:
                    lines.append(f"**参数:** `{paras}`")
            if "exec_result" in item:
                result = item["exec_result"]
                if isinstance(result, dict):
                    lines.append(f"**结果:**")
                    lines.append(result.get("content", str(result)))
                else:
                    lines.append(f"**结果:** {str(result)}")

        return "\n".join(lines)

