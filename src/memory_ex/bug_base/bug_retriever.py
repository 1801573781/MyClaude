"""BugRetriever — Bug库检索层。

两级召回：第一级路径粗筛，第二级 LLM 语义精筛。
在 LLM 即将执行文件修改操作时触发。
"""

import json
import re
from pathlib import Path

from .bug_store import BugRecord, BugStore


class BugRetriever:
    """两级召回检索器。"""

    PROMPT_DIR = Path(__file__).parent / "prompts"

    def __init__(self, llm_client, store: BugStore):
        """初始化检索器。

        Args:
            llm_client: LLM 客户端，需提供 stream_chat(messages) -> str 接口。
            store: BugStore 实例。
        """
        self.llm = llm_client
        self.store = store
        self.retrieval_prompt = self._load_prompt()

    def retrieve(
        self, file_paths: list[str], task_context: str, skip_stage2: bool = False
    ) -> list[BugRecord]:
        """两级召回。

        Args:
            file_paths: 即将修改的文件路径列表（从工具调用参数中提取）。
            task_context: 当前任务描述（对话上下文摘要）。
            skip_stage2: 是否跳过第二级 LLM 精筛（用于 /bug rt 手动测试）。

        Returns:
            相关的Bug记录列表。
        """
        # 第一级：路径确定性匹配
        candidates = self._stage1_path_match(file_paths)
        if not candidates:
            return []

        # 去重
        seen_ids = set()
        unique_candidates = []
        for r in candidates:
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                unique_candidates.append(r)
        candidates = unique_candidates

        # 第二级：LLM 语义精筛
        if skip_stage2 or not task_context:
            return candidates

        filtered = self._stage2_semantic_filter(candidates, task_context)
        return filtered

    def _stage1_path_match(self, file_paths: list[str]) -> list[BugRecord]:
        """第一级：根据文件路径召回所有 open 状态的相关Bug。

        策略：精确匹配 + 同模块匹配（召回率优先，留给第二级过滤）。
        """
        candidates = set()

        for fp in file_paths:
            # 精确匹配：Bug的 affected_files 包含该路径
            records = self.store.get_by_file(fp)
            candidates.update(records)

            # 模块匹配：同模块的其他Bug也作为候选
            module = self.store._resolve_module(fp)
            module_records = self.store.get_by_module(module)
            candidates.update(module_records)

        return list(candidates)

    def _stage2_semantic_filter(
        self, candidates: list[BugRecord], task_context: str
    ) -> list[BugRecord]:
        """第二级：用 LLM 判断候选Bug与当前任务的相关性。"""
        if not candidates:
            return []

        # 构造候选记录摘要
        candidates_json = json.dumps(
            [
                {
                    "id": r.id,
                    "title": r.title,
                    "affected_files": r.affected_files,
                    "affected_functions": r.affected_functions,
                    "root_cause": r.root_cause,
                    "caution": r.caution,
                }
                for r in candidates
            ],
            ensure_ascii=False,
            indent=2,
        )

        file_paths_str = ", ".join(
            set(r.affected_files[0] for r in candidates if r.affected_files)
        )

        prompt = self.retrieval_prompt
        prompt = prompt.replace("{task_context}", task_context)
        prompt = prompt.replace("{file_paths}", file_paths_str)
        prompt = prompt.replace("{candidates_json}", candidates_json)

        messages = [{"role": "user", "content": prompt}]
        response = self._call_llm(messages)
        if not response:
            return candidates  # LLM 调用失败时返回全部候选

        # 解析 LLM 返回的相关 ID 列表
        relevant_ids = self._parse_relevant_ids(response)
        if not relevant_ids:
            return []

        # 按 LLM 返回的顺序保留记录
        id_to_record = {r.id: r for r in candidates}
        filtered = []
        for rid in relevant_ids:
            if rid in id_to_record:
                filtered.append(id_to_record[rid])

        return filtered

    def _load_prompt(self) -> str:
        """加载 prompts/retrieval_prompt.txt。"""
        prompt_path = self.PROMPT_DIR / "retrieval_prompt.txt"
        if not prompt_path.exists():
            return (
                "当前任务上下文：\n{task_context}\n\n"
                "即将修改的文件：\n{file_paths}\n\n"
                "以下是候选历史Bug记录：\n{candidates_json}\n\n"
                "请判断每条记录与当前任务的相关性，返回相关的记录 ID 列表。"
                '格式：["id1", "id2"]'
            )
        return prompt_path.read_text(encoding="utf-8")

    def _call_llm(self, messages: list[dict]) -> str:
        """调用 LLM 并返回完整文本。"""
        try:
            if hasattr(self.llm, "stream_chat"):
                result = self.llm.stream_chat(messages)
                if isinstance(result, tuple):
                    return result[0]
                return result
            elif hasattr(self.llm, "chat"):
                result = self.llm.chat(messages)
                if isinstance(result, tuple):
                    return result[0]
                return result
            else:
                return ""
        except Exception:
            return ""

    def _parse_relevant_ids(self, response: str) -> list[str]:
        """解析 LLM 返回的相关 ID 列表。"""
        response = response.strip()

        # 尝试提取 JSON 数组
        # 尝试提取 ```json ... ``` 代码块
        code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)```", response, re.DOTALL)
        if code_block_match:
            response = code_block_match.group(1).strip()

        # 尝试直接解析
        try:
            data = json.loads(response)
            if isinstance(data, list):
                return [str(item) for item in data]
        except json.JSONDecodeError:
            pass

        # 尝试提取最外层的 [ ... ]
        bracket_match = re.search(r"\[.*\]", response, re.DOTALL)
        if bracket_match:
            try:
                data = json.loads(bracket_match.group(0))
                if isinstance(data, list):
                    return [str(item) for item in data]
            except json.JSONDecodeError:
                pass

        # 尝试提取所有 bug_ 开头的 ID
        id_matches = re.findall(r'bug_\d{8}_\d{3}', response)
        if id_matches:
            return id_matches

        return []
