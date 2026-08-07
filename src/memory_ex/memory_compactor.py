"""记忆整理模块。

两段式处理流水线：
1. Merge（合并）：同主题合并、时间线演进、因果链压缩、重复去重
2. Evict（淘汰）：删除无用条目

规则化优先，LLM 辅助。原子写入。可追溯日志。
"""

import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.memory_ex.scoring import ImportanceScorer

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """加载 Prompt 模板文件。"""
    try:
        return (_PROMPT_DIR / filename).read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(f"Prompt 模板未找到: {filename}")
        return ""


class MemoryCompactor:
    """记忆整理器。

    执行三段式流水线（Merge → Demote → Evict），
    将 Layer 1（MEMORY.md）维持在行数/token 限制内。
    """

    def __init__(self, mem_config: Any, store: Any):
        """初始化整理器。

        Args:
            mem_config: memory_ex.yaml 配置对象
            store: MemoryStore 实例
        """
        self._store = store
        self._scorer = ImportanceScorer(mem_config.scoring)

        comp_config = mem_config.compactor
        self._temperature = float(getattr(comp_config, "temperature", 0.3))
        self._max_tokens = int(getattr(comp_config, "max_tokens", 2048))
        self._timeout = int(getattr(comp_config, "timeout", 120))

        # 水位配置
        wm = mem_config.watermarks
        self._wm_warning = int(getattr(wm, "warning", 150))
        self._wm_trigger = int(getattr(wm, "trigger", 180))
        self._wm_hard_limit = int(getattr(wm, "hard_limit", 200))
        self._wm_target_after = int(getattr(wm, "target_after", 160))

        # 自动整理配置
        auto_config = mem_config.auto_compaction
        self._auto_enabled = bool(getattr(auto_config, "enabled", False))
        self._light_query_interval = int(getattr(auto_config, "light_query_interval", 10))

        self._llm_chat_fn = None
        self._progress_callback = None

    def set_llm_chat_fn(self, fn):
        """注入 LLM 调用函数。"""
        self._llm_chat_fn = fn

    def set_progress_callback(self, callback):
        """注入进度回调函数。

        Args:
            callback: 签名为 callback(step: str)，step 取值：
                      "rule_merge" / "llm_merge" / "evict"
        """
        self._progress_callback = callback

    def _report_progress(self, step: str) -> None:
        """报告当前整理步骤进度。"""
        if self._progress_callback:
            try:
                self._progress_callback(step)
            except Exception:
                pass

    # ===== 公开接口 =====

    def check_needed(self) -> bool:
        """检查是否需要整理。

        条件：
        - Layer 1 行数 ≥ warning（150）或 token ≥ 1500
        - 或距上次整理 ≥ 10 个 Query
        """
        stats = self._store.get_layer1_stats()
        if stats["lines"] >= self._wm_warning or stats["tokens"] >= 1500:
            return True

        query_count = self._store.get_query_count_since_last_compaction()
        if query_count >= self._light_query_interval:
            return True

        return False

    def run_auto_compaction(self) -> dict:
        """自动整理入口。

        检查配置开关和水位，满足条件则同步执行。
        """
        if not self._auto_enabled:
            return {"skipped": True, "reason": "auto_compaction disabled"}

        warning, trigger, hard_limit = self._store.check_water_level()

        if trigger or hard_limit:
            return self.run_full_compaction(mode="full")
        elif warning:
            # 预警，下次触发（记录但不执行）
            logger.info("Layer 1 水位预警，下次 Query 结束时触发整理")
            return {"skipped": True, "reason": "warning_level"}

        # 检查定时触发
        query_count = self._store.get_query_count_since_last_compaction()
        if query_count >= self._light_query_interval:
            return self.run_full_compaction(mode="light")

        return {"skipped": True, "reason": "below_threshold"}

    def run_full_compaction(self, mode: str = "full") -> dict:
        """执行完整三段式整理。

        仅对 Layer 1 已有内容做整理（Merge → Demote → Evict），
        不再负责从 Layer 0 搬运条目（构建职责已归还给 extractor）。

        Args:
            mode: "full"（Merge + Demote + Evict）或 "light"（仅 Merge）

        Returns:
            统计信息字典
        """
        start_time = datetime.now()
        compaction_id = f"compact_{start_time.strftime('%Y%m%d_%H%M%S')}"

        # 读取当前 Layer 1
        layer1_content = self._store.read_layer1()
        layer1_before_entries = len(self._parse_layer1_entries(layer1_content)) if layer1_content else 0

        # Step 1: Merge（仅对 Layer 1 已有内容合并）
        merged_count, layer1_content, merge_details = self._step_merge(layer1_content)

        stats = {
            "compaction_id": compaction_id,
            "trigger": "manual" if mode == "full" else "auto_light",
            "mode": mode,
            "layer1_before": layer1_before_entries,
            "merged": merged_count,
            "evicted": 0,
            "merge_details": merge_details,
        }

        if mode == "light":
            # 轻量整理仅执行 Merge
            self._store.write_layer1(layer1_content)
            stats["layer1_after"] = len(self._parse_layer1_entries(layer1_content)) if layer1_content else 0
            stats["duration_ms"] = int((datetime.now() - start_time).total_seconds() * 1000)
            stats["total_processed"] = merged_count
            self._store.add_compaction_log(stats)
            self._store.save_metadata()
            return stats

        # Step 2: Evict（原 Step 3，Demote 已移除）
        evicted_count, layer1_content = self._step_evict(layer1_content)
        stats["evicted"] = evicted_count

        # 写入 Layer 1
        self._store.write_layer1(layer1_content)

        stats["layer1_after"] = len(self._parse_layer1_entries(layer1_content)) if layer1_content else 0
        stats["duration_ms"] = int((datetime.now() - start_time).total_seconds() * 1000)

        # 记录日志
        self._store.add_compaction_log(stats)
        self._store.save_metadata()

        logger.info(
            f"整理完成: 合并 {merged_count}, "
            f"淘汰 {evicted_count}, 耗时 {stats['duration_ms']}ms"
        )

        stats["total_processed"] = merged_count + evicted_count
        return stats

    def evict_by_id(self, memory_id: str) -> bool:
        """从 Layer 1 淘汰指定条目。

        Args:
            memory_id: 条目 ID

        Returns:
            True 表示成功
        """
        layer1_content = self._store.read_layer1()
        if not layer1_content:
            return False

        lines = layer1_content.split("\n")
        new_lines = []
        found = False

        for line in lines:
            if memory_id in line:
                found = True
                continue
            new_lines.append(line)

        if found:
            self._store.write_layer1("\n".join(new_lines))
            self._store.update_metadata_entry(memory_id, status="processed")
            self._store.save_metadata()

        return found

    # ===== Step 1: Merge =====

    def _step_merge(self, layer1_content: str) -> Tuple[int, str, List[Dict]]:
        """执行合并步骤。

        对 Layer 1 已有内容执行规则化合并 + LLM 辅助合并。
        不再负责从 Layer 0 搬运条目（构建职责已归还给 extractor）。

        Returns:
            (合并计数, 新 Layer 1 内容, 合并详情列表)
        """
        # 解析 Layer 1 条目
        entries = self._parse_layer1_entries(layer1_content)
        if len(entries) < 2:
            return 0, layer1_content, []

        # 规则化合并
        self._report_progress("rule_merge")
        merged_count, entries, merge_details = self._rule_based_merge(entries)

        # LLM 辅助合并（如果仍有冗余）
        if self._llm_chat_fn and len(entries) > 5:
            self._report_progress("llm_merge")
            llm_merged, entries, llm_details = self._llm_assisted_merge(entries)
            merged_count += llm_merged
            merge_details.extend(llm_details)

        # 重建 Layer 1
        new_content = self._rebuild_layer1(entries)
        return merged_count, new_content, merge_details

    def _parse_layer1_entries(self, content: str) -> List[Dict]:
        """解析 Layer 1 的 Markdown 条目。

        格式：- [标签] 内容 (id=xxx)
        或    - [EVOLVED][TYPE] 内容
        """
        entries = []
        for line in content.split("\n"):
            line = line.strip()
            if not line or not line.startswith("- "):
                continue

            # 提取 id
            id_match = re.search(r"\(id=([^)]+)\)", line)
            entry_id = id_match.group(1) if id_match else ""

            # 提取 session_id
            session_match = re.search(r"\(session=([^)]+)\)", line)
            session_id = session_match.group(1) if session_match else ""

            # 移除 id 和 session 部分
            clean_line = re.sub(r"\s*\(id=[^)]+\)", "", line)
            clean_line = re.sub(r"\s*\(session=[^)]+\)", "", clean_line)

            # 提取标签
            tags = re.findall(r"\[([^\]]+)\]", clean_line)
            is_evolved = "EVOLVED" in tags

            # 提取内容（移除标签和前导 -）
            content_text = re.sub(r"^\-\s*(\[.*?\])*\s*", "", clean_line)

            entries.append({
                "id": entry_id,
                "session_id": session_id,
                "tags": tags,
                "content": content_text,
                "raw_line": line,
                "is_evolved": is_evolved,
            })

        return entries

    def _rule_based_merge(self, entries: List[Dict]) -> Tuple[int, List[Dict], List[Dict]]:
        """规则化合并（无需 LLM）。

        策略：
        1. 同标签 + 时间窗口内 → 拼接去重
        2. 重复去重（content_hash 或 Jaccard 相似度 > 0.8）

        Returns:
            (合并计数, 合并后的条目列表, 合并详情列表)
        """
        merged_count = 0
        result = []
        used = set()
        merge_details = []

        for i, entry in enumerate(entries):
            if i in used:
                continue

            # 查找可合并的条目
            merge_candidates = []
            for j in range(i + 1, len(entries)):
                if j in used:
                    continue
                other = entries[j]

                # 不合并进化条目
                if entry.get("is_evolved") or other.get("is_evolved"):
                    continue

                # 同标签合并 — 需同时满足内容相关性，防止仅因标签相似而拼接不相关内容
                if self._same_tags(entry["tags"], other["tags"]):
                    content_sim = self._jaccard_similarity(entry["content"], other["content"])
                    if content_sim > 0.3:
                        merge_candidates.append((j, other, "same_tags"))
                    # else: 标签相似但内容不相关，不进行规则合并，留给 LLM 判断
                # 重复去重
                elif self._jaccard_similarity(entry["content"], other["content"]) > 0.8:
                    merge_candidates.append((j, other, "duplicate"))

            if merge_candidates:
                # 记录合并前原始信息
                merged_from = [
                    {"content": entry["content"], "tags": list(entry["tags"]), "id": entry.get("id", "")}
                ]
                reasons = []

                # 合并到当前条目
                merged_content = entry["content"]
                merged_tags = list(entry["tags"])
                for j, candidate, reason in merge_candidates:
                    merged_from.append(
                        {"content": candidate["content"], "tags": list(candidate["tags"]), "id": candidate.get("id", "")}
                    )
                    if reason not in reasons:
                        reasons.append(reason)
                    if reason == "same_tags":
                        # 追加非重复内容
                        if candidate["content"] not in merged_content:
                            merged_content += f"；{candidate["content"]}"
                    else:
                        # 重复：保留信息更完整的
                        if len(candidate["content"]) > len(merged_content):
                            merged_content = candidate["content"]
                    # 合并标签
                    for tag in candidate["tags"]:
                        if tag not in merged_tags:
                            merged_tags.append(tag)
                    used.add(j)

                # 统计参与合并的总条目数（原始条目 + 候选条目）
                merged_count += len(merged_from)

                # 为合并后的条目生成新 ID
                import uuid as _uuid
                new_id = f"m_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_uuid.uuid4().hex[:6]}"

                # 保留第一个有 session_id 的条目的 session_id
                merged_session_id = entry.get("session_id", "")
                for _, candidate, _ in merge_candidates:
                    if not merged_session_id and candidate.get("session_id"):
                        merged_session_id = candidate["session_id"]
                        break

                # 标记被合并的旧条目为 merged 状态
                old_ids = [entry.get("id", "")] + [c.get("id", "") for _, c, _ in merge_candidates]
                for old_id in old_ids:
                    if old_id:
                        self._store.update_metadata_entry(old_id, status="merged")

                # 注册合并后条目的元数据和倒排索引
                self._store.update_metadata_entry(
                    new_id,
                    tags=merged_tags,
                    status="unprocessed",
                    is_consumed=False,
                    is_evolved=False,
                    created_at=datetime.now().isoformat(),
                    last_accessed=datetime.now().isoformat(),
                    access_count=0,
                    importance_score=None,
                )
                self._store.update_inverted_index(new_id, merged_tags, merged_content)

                entry["id"] = new_id
                entry["session_id"] = merged_session_id
                entry["content"] = merged_content
                entry["tags"] = merged_tags
                entry["raw_line"] = self._format_entry_line(entry)

                # 记录合并详情
                merge_details.append({
                    "reason": reasons,
                    "merged_from": merged_from,
                    "merged_into": merged_content,
                    "merged_tags": merged_tags,
                })

            result.append(entry)

        return merged_count, result, merge_details

    def _llm_assisted_merge(self, entries: List[Dict]) -> Tuple[int, List[Dict], List[Dict]]:
        """LLM 辅助合并（语义相似但标签不同、因果链压缩）。

        Returns:
            (合并计数, 合并后的条目列表, 合并详情列表)
        """
        if not self._llm_chat_fn:
            return 0, entries, []

        # 构建 Prompt
        prompt_template = _load_prompt("merge_prompt.txt")
        if not prompt_template:
            prompt_template = self._get_builtin_merge_prompt()

        entries_text = "\n".join(
            f"{i}. [{', '.join(e['tags'])}] {e['content']}"
            for i, e in enumerate(entries)
            if not e.get("is_evolved")
        )
        prompt = prompt_template.replace("{memory_entries}", entries_text)

        try:
            response = self._call_llm(prompt, timeout=self._timeout)
            if not response:
                return 0, entries, []

            # 解析 LLM 合并建议
            merged_indices, merged_content = self._parse_merge_response(response, len(entries))
            if not merged_indices:
                return 0, entries, []

            # 执行合并
            merged_entry = entries[merged_indices[0]].copy()
            merged_entry["content"] = merged_content
            all_tags = []
            for idx in merged_indices:
                for tag in entries[idx]["tags"]:
                    if tag not in all_tags:
                        all_tags.append(tag)
            merged_entry["tags"] = all_tags

            # 为 LLM 合并后的条目生成新 ID
            import uuid as _uuid
            new_id = f"m_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_uuid.uuid4().hex[:6]}"

            # 保留第一个有 session_id 的条目的 session_id
            merged_session_id = ""
            for idx in merged_indices:
                if entries[idx].get("session_id"):
                    merged_session_id = entries[idx]["session_id"]
                    break

            # 标记被合并的旧条目为 merged 状态
            for idx in merged_indices:
                old_id = entries[idx].get("id", "")
                if old_id:
                    self._store.update_metadata_entry(old_id, status="merged")

            # 注册合并后条目的元数据和倒排索引
            self._store.update_metadata_entry(
                new_id,
                tags=all_tags,
                status="unprocessed",
                is_consumed=False,
                is_evolved=False,
                created_at=datetime.now().isoformat(),
                last_accessed=datetime.now().isoformat(),
                access_count=0,
                importance_score=None,
            )
            self._store.update_inverted_index(new_id, all_tags, merged_content)

            merged_entry["id"] = new_id
            merged_entry["session_id"] = merged_session_id
            merged_entry["raw_line"] = self._format_entry_line(merged_entry)

            # 记录合并详情
            merge_details = [{
                "reason": ["llm_assisted"],
                "merged_from": [
                    {"content": entries[idx]["content"], "tags": list(entries[idx]["tags"]), "id": entries[idx].get("id", "")}
                    for idx in merged_indices
                ],
                "merged_into": merged_content,
                "merged_tags": all_tags,
            }]

            # 构建新列表
            new_entries = [merged_entry]
            for i, e in enumerate(entries):
                if i not in merged_indices:
                    new_entries.append(e)

            return len(merged_indices), new_entries, merge_details

        except Exception as e:
            logger.error(f"LLM 辅助合并失败: {e}")
            return 0, entries, []

    def _parse_merge_response(
        self, response: str, total_entries: int
    ) -> Tuple[List[int], str]:
        """解析 LLM 合并响应。

        预期格式：
            MERGE: [0, 2, 5] → 合并后的内容描述
        """
        match = re.search(r"MERGE:\s*\[([^\]]*)\]\s*→\s*(.+)", response)
        if not match:
            return [], ""

        try:
            indices_str = match.group(1)
            indices = [int(x.strip()) for x in indices_str.split(",") if x.strip()]
            content = match.group(2).strip()
            return indices, content
        except (ValueError, AttributeError):
            return [], ""

    # ===== Step 2: Evict =====

    def _step_evict(self, layer1_content: str) -> Tuple[int, str]:
        """执行淘汰步骤。

        Returns:
            (淘汰计数, 新 Layer 1 内容)
        """
        self._report_progress("evict")
        stats = self._store.get_layer1_stats()
        if stats["lines"] <= self._wm_hard_limit and stats["tokens"] <= 2000:
            return 0, layer1_content

        entries = self._parse_layer1_entries(layer1_content)
        if not entries:
            return 0, layer1_content

        # 计算分数并排序
        scored_entries = []
        for entry in entries:
            meta = self._store.get_metadata_entry(entry["id"]) if entry["id"] else None
            score = self._scorer.score(
                {"tags": entry["tags"], "content": entry["content"]},
                meta,
            )
            scored_entries.append((entry, score))

        scored_entries.sort(key=lambda x: x[1])

        # 最多淘汰 20%
        max_evict = max(1, int(len(entries) * 0.2))
        evicted_count = 0
        kept_entries = []

        for entry, score in scored_entries:
            stats = self._store.get_layer1_stats()
            if stats["lines"] <= self._wm_target_after and stats["tokens"] <= 1600:
                kept_entries.append(entry)
                continue

            if evicted_count >= max_evict:
                kept_entries.append(entry)
                continue

            # 进化条目不淘汰
            if entry.get("is_evolved"):
                kept_entries.append(entry)
                continue

            # 淘汰分数最低的
            if score < 0.1:
                if entry.get("id"):
                    self._store.update_metadata_entry(entry["id"], status="processed")
                    self._store.remove_metadata_entry(entry["id"])
                evicted_count += 1
                logger.info(f"淘汰条目: {entry.get('content', '')[:50]}")
            else:
                kept_entries.append(entry)

        new_content = self._rebuild_layer1(kept_entries)
        return evicted_count, new_content

    # ===== 辅助方法 =====

    def _rebuild_layer1(self, entries: List[Dict]) -> str:
        """从条目列表重建 Layer 1 内容。"""
        lines = []
        for entry in entries:
            line = self._format_entry_line(entry)
            if line:
                lines.append(line)
        return "\n\n".join(lines)

    def _format_entry_line(self, entry: Dict) -> str:
        """格式化单条记忆为 Layer 1 行。"""
        tags = entry.get("tags", [])
        content = entry.get("content", "")
        tags_str = "".join(f"[{t}]" for t in tags) if tags else ""

        line = f"- {tags_str} {content}".strip()
        if entry.get("id"):
            line += f" (id={entry['id']})"
        if entry.get("session_id"):
            line += f" (session={entry['session_id']})"
        return line

    def _same_tags(self, tags_a: List[str], tags_b: List[str]) -> bool:
        """判断两组标签是否相似（Jaccard 相似度 ≥ 0.5）。"""
        if not tags_a or not tags_b:
            return False
        set_a = set(tags_a)
        set_b = set(tags_b)
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union) >= 0.5

    def _jaccard_similarity(self, text_a: str, text_b: str) -> float:
        """计算两段文本的 Jaccard 相似度。

        对中文文本，空格分词无效，改用字符级 bigram 集合计算。
        对英文/混合文本，bigram 仍能有效捕捉相似度。
        """
        def _bigrams(text: str) -> set:
            text = text.strip()
            if len(text) < 2:
                return {text} if text else set()
            return {text[i:i+2] for i in range(len(text) - 1)}

        set_a = _bigrams(text_a)
        set_b = _bigrams(text_b)
        if not set_a or not set_b:
            return 0.0
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union)

    def _call_llm(self, prompt: str, timeout: int = 120) -> Optional[str]:
        """调用 LLM，带超时保护。

        直接将 timeout 传递给 simple_chat，由 httpx 在 HTTP 层处理超时，
        确保超时后连接被正确关闭，避免线程泄漏和僵尸连接。
        """
        if not self._llm_chat_fn:
            return None

        import time

        try:
            start = time.time()
            response = self._llm_chat_fn(
                prompt,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                timeout=float(timeout),
            )
            elapsed = time.time() - start

            if not response:
                if elapsed >= timeout * 0.9:
                    logger.warning(f"LLM 整理疑似超时（耗时 {elapsed:.1f}s，阈值 {timeout}s）")
                else:
                    logger.warning(f"LLM 整理返回空响应（耗时 {elapsed:.1f}s）")
                return None

            logger.info(f"LLM 整理成功（耗时 {elapsed:.1f}s）")
            return response
        except Exception as e:
            logger.error(f"LLM 整理调用异常: {e}")
            return None

    def _get_builtin_merge_prompt(self) -> str:
        """内置合并 Prompt。"""
        return (
            "你是一个记忆合并专家。以下是记忆索引层中的多条记忆条目。\n"
            "请分析哪些条目可以合并（语义相似但标签不同、因果链可压缩）。\n\n"
            "合并规则：\n"
            "1. 同一对象的不同描述 → 合并为一条完整描述\n"
            "2. 因果关系链 → 压缩为最终结论\n"
            "3. 保守原则：无法确定是否可合并时，不合并\n"
            "4. 合并后必须包含所有关键信息\n\n"
            "输出格式（每组合并一行）：\n"
            "MERGE: [序号1, 序号2, ...] → 合并后的内容\n"
            "或 NONE\n\n"
            "记忆条目：\n"
            "{memory_entries}"
        )
