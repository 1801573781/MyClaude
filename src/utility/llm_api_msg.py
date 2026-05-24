from pathlib import Path
from src.utility.skill_loader import get_skill_loader


# 通过API与LLM交互时，Message的构建
class LLMAPIMessage:

    def __init__(self, role: str = "mycode"):
        self.project_root = Path(__file__).resolve().parent.parent.parent

        # 从同目录加载 sys_prompt.md 作为系统提示词
        self.api_messages = self._load_system_prompt(role)

        # 注入项目上下文（放在系统提示词之后、用户输入之前）
        project_context = self._load_project_context(role)
        if project_context:
            context_msg = {
                "role": "user",
                "content": f"[项目上下文]\n{project_context}"
            }
            self._append_info(context_msg)

        # 注入目录树缓存（放在项目上下文之后、用户输入之前）
        tree_cache = self._load_tree_cache()
        if tree_cache:
            tree_cache_msg = {
                "role": "user",
                "content": f"[项目目录树]\n{tree_cache}"
            }
            self._append_info(tree_cache_msg)


    @staticmethod
    def _load_system_prompt(role: str) -> list[dict]:
        """根据 role 从 config/role/{role}/ 目录加载系统提示词，包装为 API 消息格式，并追加技能清单（L1 Metadata）。"""
        project_root = Path(__file__).resolve().parent.parent.parent
        md_path = project_root / "config" / "role" / role / f"{role}_sys_prompt.md"

        # 兼容旧路径：若新路径不存在，回退到项目根目录的 sys_prompt.md
        if not md_path.exists():
            fallback_path = project_root / "sys_prompt.md"
            if fallback_path.exists():
                md_path = fallback_path

        base_prompt = ""
        if md_path.exists():
            base_prompt = md_path.read_text(encoding="utf-8")

        # 追加 L1 技能清单（如果有）
        skill_loader = get_skill_loader()
        skills_section = skill_loader.format_skills_prompt()
        if skills_section:
            base_prompt = base_prompt + "\n\n" + skills_section

        return [{"role": "system", "content": base_prompt}]


    @staticmethod
    def _load_project_context(role: str) -> str:
        """根据 role 从 config/role/{role}/ 目录加载项目上下文注入"""
        try:
            project_root = Path(__file__).resolve().parent.parent.parent
            md_path = project_root / "config" / "role" / role / f"{role}.md"

            # 兼容旧路径：若新路径不存在，回退到项目根目录的 MyClaude.md
            if not md_path.exists():
                fallback_path = project_root / "MyClaude.md"
                if fallback_path.exists():
                    md_path = fallback_path

            if md_path.exists():
                return md_path.read_text(encoding="utf-8")
            return ""
        except (OSError, UnicodeDecodeError) as e:
            print(f"[warn] 加载项目上下文失败: {e}")
            return ""


    @staticmethod
    def _load_tree_cache() -> str:
        """从项目根目录加载 .tree_cache.md 作为目录树上下文注入"""
        try:
            project_root = Path(__file__).resolve().parent.parent.parent
            tree_path = project_root / ".tree_cache.md"
            if tree_path.exists():
                return tree_path.read_text(encoding="utf-8")
            return ""
        except (OSError, UnicodeDecodeError) as e:
            print(f"[warn] 加载 .tree_cache.md 失败: {e}")
            return ""


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
