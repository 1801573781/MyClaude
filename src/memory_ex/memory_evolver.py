"""记忆进化模块。

对应设计文档第四章。

四类进化操作：
1. Pattern Recognition（模式识别）
2. Conflict Resolution（矛盾解决）
3. Generalization（归纳通用解法）
4. Trend Insight（趋势洞察）

特性：
- 加法操作，只向 Layer 1 追加新条目，不修改已有条目
- LLM Prompt 合并优化（单次调用输出四类结果）
- 分批处理（>50 条时分批）
- 不中断策略 + 细粒度进度反馈
- 结果即时持久化
"""

import json
import logging
import re
from collections import defaultdict
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


class MemoryEvolver:
    """记忆进化器。

    通过对已有记忆的深度推理，发现隐含的模式、规律、偏好和趋势，
    生成原数据中不存在的高层认知。

    进化是加法操作，产出新条目追加到 Layer 1。
    """

    def __init__(self, mem_config: Any, store: Any):
        """初始化进化器。

        Args:
            mem_config: memory_ex.yaml 配置对象
            store: MemoryStore 实例
        """
        self._store = store

        evo_config = mem_config.evolver
        self._temperature = float(getattr(evo_config, "temperature", 0.3))
        self._max_tokens = int(getattr(evo_config, "max_tokens", 1024))

        auto_config = mem_config.auto_evolution
        self._auto_enabled = bool(getattr(auto_config, "enabled", False))
        self._accumulation_threshold = int(
            getattr(auto_config, "accumulation_threshold", 10)
        )
        self._trend_query_interval = int(
            getattr(auto_config, "trend_query_interval", 200)
        )
        self._batch_size = int(getattr(auto_config, "batch_size", 50))

        self._llm_chat_fn = None
        self._progress_callback = None

    def set_llm_chat_fn(self, fn):
        """注入 LLM 调用函数。"""
        self._llm_chat_fn = fn

    def set_progress_callback(self, callback):
        """注入进度回调函数。

        Args:
            callback: 签名为 callback(batch: int, total_batches: int, stage: str, elapsed: float)
        """
        self._progress_callback = callback

    # ===== 公开接口 =====

    def check_needed(self) -> bool:
        """检查是否需要进化。

        条件：Layer 0 中 evolved=False 的 unprocessed 条目 ≥ 10 条。
        """
        unevolved = self._store.get_unevolved_entries()
        return len(unevolved) >= self._accumulation_threshold

    def run_auto_evolution(self) -> dict:
        """自动进化入口。

        检查配置开关和积累量，满足条件则同步执行。
        """
        if not self._auto_enabled:
            return {"skipped": True, "reason": "auto_evolution disabled"}

        unevolved = self._store.get_unevolved_entries()
        if len(unevolved) < self._accumulation_threshold:
            return {"skipped": True, "reason": "insufficient_accumulation"}

        return self.run_full_evolution()

    def run_full_evolution(self) -> dict:
        """执行全类型进化。

        Returns:
            进化统计信息字典
        """
        start_time = datetime.now()
        evolution_id = f"evo_{start_time.strftime('%Y%m%d_%H%M%S')}"

        unevolved = self._store.get_unevolved_entries()
        if len(unevolved) < self._accumulation_threshold:
            return {
                "skipped": True,
                "reason": "insufficient_accumulation",
                "unevolved_count": len(unevolved),
            }

        # 分批处理
        batches = self._create_batches(unevolved)
        total_batches = len(batches)

        all_evolutions: List[Dict] = []
        types_executed = set()

        for batch_idx, batch in enumerate(batches):
            elapsed = (datetime.now() - start_time).total_seconds()
            self._report_progress(batch_idx + 1, total_batches, "进化中", elapsed)

            # 检查 Layer 0 是否需要先归档
            if len(unevolved) > 200 and batch_idx == 0:
                logger.info("待进化记录 > 200，先执行 Layer 0 归档")
                self._store.archive_layer0()

            # LLM 进化（合并 Prompt，单次调用输出四类结果）
            evolutions = self._evolve_batch(batch)
            if evolutions:
                all_evolutions.extend(evolutions)
                for evo in evolutions:
                    types_executed.add(evo.get("type", ""))

                # 即时持久化
                self._write_evolutions(evolutions)

            # 标记本批条目为已进化
            for entry in batch:
                self._store.update_layer0_entry(
                    entry["id"], {"evolved": True}
                )
                self._store.update_metadata_entry(entry["id"], is_evolved=True)

            # 即时保存元数据
            self._store.save_metadata()

        elapsed = (datetime.now() - start_time).total_seconds()
        self._report_progress(total_batches, total_batches, "完成", elapsed)

        # 统计
        patterns_found = sum(1 for e in all_evolutions if e.get("type") == "PATTERN")
        resolved_count = sum(1 for e in all_evolutions if e.get("type") == "RESOLVED")
        generalizations_found = sum(
            1 for e in all_evolutions if e.get("type") == "GENERALIZED"
        )
        trends_found = sum(1 for e in all_evolutions if e.get("type") == "TREND")

        stats = {
            "evolution_id": evolution_id,
            "timestamp": start_time.isoformat(),
            "trigger": "manual",
            "types_executed": list(types_executed),
            "stats": {
                "layer0_consumed": len(unevolved),
                "evolutions_generated": len(all_evolutions),
                "patterns_found": patterns_found,
                "generalizations_found": generalizations_found,
                "conflicts_resolved": resolved_count,
                "trends_found": trends_found,
            },
            "evolutions": [
                {
                    "id": f"evo_{i+1:03d}",
                    "type": e.get("type", ""),
                    "conclusion": e.get("conclusion", ""),
                    "confidence": e.get("confidence", 0.0),
                    "sources": e.get("sources", []),
                }
                for i, e in enumerate(all_evolutions)
            ],
            "duration_ms": int(elapsed * 1000),
        }

        self._store.add_evolution_log(stats)
        self._store.save_metadata()

        logger.info(
            f"进化完成: 产生 {len(all_evolutions)} 条新认知, "
            f"耗时 {stats['duration_ms']}ms"
        )

        return stats

    # ===== 分批处理 =====

    def _create_batches(self, entries: List[Dict]) -> List[List[Dict]]:
        """将待进化记录分批。

        按主题标签聚类，每批 ≤ batch_size（默认 50），同一标签组尽量放入同一批。
        """
        if len(entries) <= self._batch_size:
            return [entries]

        # 按标签聚类
        tag_groups: Dict[str, List[Dict]] = defaultdict(list)
        no_tag = []
        for entry in entries:
            tags = entry.get("tags", [])
            if tags:
                primary_tag = tags[0]
                tag_groups[primary_tag].append(entry)
            else:
                no_tag.append(entry)

        batches = []
        current_batch = []

        for tag, group in tag_groups.items():
            if len(current_batch) + len(group) <= self._batch_size:
                current_batch.extend(group)
            else:
                if current_batch:
                    batches.append(current_batch)
                if len(group) <= self._batch_size:
                    current_batch = group
                else:
                    # 超大标签组，拆分
                    for i in range(0, len(group), self._batch_size):
                        batches.append(group[i : i + self._batch_size])
                    current_batch = []

        # 无标签条目放入最后
        if no_tag:
            if current_batch and len(current_batch) + len(no_tag) <= self._batch_size:
                current_batch.extend(no_tag)
                batches.append(current_batch)
            else:
                if current_batch:
                    batches.append(current_batch)
                for i in range(0, len(no_tag), self._batch_size):
                    batches.append(no_tag[i : i + self._batch_size])
        elif current_batch:
            batches.append(current_batch)

        return batches

    # ===== LLM 进化 =====

    def _evolve_batch(self, batch: List[Dict]) -> List[Dict]:
        """对一批记录执行四类进化（单次 LLM 调用）。

        Args:
            batch: 一批待进化记录

        Returns:
            进化结果列表
        """
        if not self._llm_chat_fn:
            logger.warning("LLM 调用函数未注入，跳过进化")
            return []

        # 构建合并 Prompt
        prompt = self._build_combined_prompt(batch)

        try:
            response = self._call_llm(prompt, timeout=30)
            if not response:
                return []

            # 解析四类进化结果
            evolutions = self._parse_combined_response(response, batch)
            return evolutions

        except Exception as e:
            logger.error(f"LLM 进化失败: {e}")
            return []

    def _build_combined_prompt(self, batch: List[Dict]) -> str:
        """构建合并 Prompt（单次调用输出四类结果）。

        将 Pattern Recognition、Conflict Resolution、Generalization、
        Trend Insight 四类进化的 Prompt 合并为一个。
        """
        # 格式化记忆条目
        entries_text = "\n".join(
            f"{i+1}. [{', '.join(e.get('tags', []))}] {e.get('content', '')} "
            f"(id={e.get('id', '')}, query={e.get('query_id', 0)}, "
            f"time={e.get('timestamp', '')})"
            for i, e in enumerate(batch)
        )

        # 加载各 Prompt 模板并提取核心指令
        pattern_prompt = _load_prompt("pattern_prompt.txt")
        conflict_prompt = _load_prompt("conflict_prompt.txt")
        generalization_prompt = _load_prompt("generalization_prompt.txt")
        trend_prompt = _load_prompt("trend_prompt.txt")

        # 如果模板未加载，使用内置
        if not pattern_prompt:
            pattern_prompt = self._get_builtin_prompt("pattern")
        if not conflict_prompt:
            conflict_prompt = self._get_builtin_prompt("conflict")
        if not generalization_prompt:
            generalization_prompt = self._get_builtin_prompt("generalization")
        if not trend_prompt:
            trend_prompt = self._get_builtin_prompt("trend")

        combined = f"""你是一个记忆进化专家。以下是 AI 编程助手的记忆记录（按时间排列）：

{entries_text}

请从以下四个维度分析这些记忆，各自独立输出结果：

===== 1. 模式识别 (PATTERN) =====
{pattern_prompt}

===== 2. 矛盾解决 (CONFLICT) =====
{conflict_prompt}

===== 3. 归纳通用解法 (GENERALIZATION) =====
{generalization_prompt}

===== 4. 趋势洞察 (TREND) =====
{trend_prompt}

===== 输出格式 =====
对每个维度，按以下格式输出（如果没有发现则输出 NONE）：

PATTERN:
- CONCLUSION: [模式描述]
- CONFIDENCE: [0.0~1.0]
- REASONING: [推理过程]
- SOURCES: [来源条目序号，如 1,3,5]

CONFLICT:
- CONCLUSION: [统一结论]
- CONFIDENCE: [0.0~1.0]
- REASONING: [仲裁理由]
- SOURCES: [来源条目序号]

GENERALIZATION:
- RULE: [通用规则]
- CASES: [案例序号列表]
- CONFIDENCE: [0.0~1.0]
- REASONING: [归纳过程]

TREND:
- TREND: [趋势描述]
- PREDICTION: [预测建议]
- CONFIDENCE: [0.0~1.0]
- REASONING: [分析过程]
"""
        return combined

    def _parse_combined_response(
        self, response: str, batch: List[Dict]
    ) -> List[Dict]:
        """解析 LLM 合并响应，提取四类进化结果。

        Args:
            response: LLM 响应文本
            batch: 对应的原始记忆条目

        Returns:
            进化结果列表
        """
        evolutions = []

        # 解析 PATTERN
        pattern = self._parse_section(response, "PATTERN", "CONCLUSION")
        if pattern:
            confidence = self._extract_field(response, "PATTERN", "CONFIDENCE")
            reasoning = self._extract_field(response, "PATTERN", "REASONING")
            sources = self._extract_sources(response, "PATTERN", batch)
            evolutions.append({
                "type": "PATTERN",
                "conclusion": pattern,
                "confidence": confidence,
                "reasoning": reasoning,
                "sources": sources,
            })

        # 解析 CONFLICT
        conflict = self._parse_section(response, "CONFLICT", "CONCLUSION")
        if conflict:
            confidence = self._extract_field(response, "CONFLICT", "CONFIDENCE")
            reasoning = self._extract_field(response, "CONFLICT", "REASONING")
            sources = self._extract_sources(response, "CONFLICT", batch)
            evolutions.append({
                "type": "RESOLVED",
                "conclusion": conflict,
                "confidence": confidence,
                "reasoning": reasoning,
                "sources": sources,
            })

        # 解析 GENERALIZATION
        rule = self._parse_section(response, "GENERALIZATION", "RULE")
        if rule:
            confidence = self._extract_field(response, "GENERALIZATION", "CONFIDENCE")
            reasoning = self._extract_field(response, "GENERALIZATION", "REASONING")
            sources = self._extract_sources(response, "GENERALIZATION", batch)
            evolutions.append({
                "type": "GENERALIZED",
                "conclusion": rule,
                "confidence": confidence,
                "reasoning": reasoning,
                "sources": sources,
            })

        # 解析 TREND
        trend = self._parse_section(response, "TREND", "TREND")
        if trend:
            confidence = self._extract_field(response, "TREND", "CONFIDENCE")
            prediction = self._extract_field(response, "TREND", "PREDICTION")
            reasoning = self._extract_field(response, "TREND", "REASONING")
            sources = self._extract_sources(response, "TREND", batch)
            evolutions.append({
                "type": "TREND",
                "conclusion": f"{trend} 预测：{prediction}" if prediction else trend,
                "confidence": confidence,
                "reasoning": reasoning,
                "sources": sources,
            })

        return evolutions

    def _parse_section(
        self, response: str, section_name: str, field_name: str
    ) -> Optional[str]:
        """从响应中解析指定维度的指定字段。

        Args:
            response: LLM 响应
            section_name: 维度名（PATTERN/CONFLICT/GENERALIZATION/TREND）
            field_name: 字段名（CONCLUSION/RULE/TREND）

        Returns:
            字段值，None 表示未找到或 NONE
        """
        # 找到维度块
        pattern = rf"{section_name}:\s*\n(.*?)(?=\n[A-Z]+:|$)"
        match = re.search(pattern, response, re.DOTALL)
        if not match:
            return None

        block = match.group(1)
        if "NONE" in block:
            return None

        # 提取字段
        field_pattern = rf"-?\s*{field_name}:\s*(.+?)(?=\n-|\Z)"
        field_match = re.search(field_pattern, block, re.DOTALL)
        if field_match:
            value = field_match.group(1).strip()
            if value.upper() == "NONE":
                return None
            return value

        return None

    def _extract_field(
        self, response: str, section_name: str, field_name: str
    ) -> Any:
        """从响应中提取指定字段的值。

        对于 CONFIDENCE 返回 float，其他返回 str。
        """
        pattern = rf"{section_name}:\s*\n(.*?)(?=\n[A-Z]+:|$)"
        match = re.search(pattern, response, re.DOTALL)
        if not match:
            return 0.0 if field_name == "CONFIDENCE" else ""

        block = match.group(1)
        field_pattern = rf"-?\s*{field_name}:\s*(.+?)(?=\n-|\Z)"
        field_match = re.search(field_pattern, block, re.DOTALL)
        if field_match:
            value = field_match.group(1).strip()
            if field_name == "CONFIDENCE":
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return 0.0
            return value

        return 0.0 if field_name == "CONFIDENCE" else ""

    def _extract_sources(
        self, response: str, section_name: str, batch: List[Dict]
    ) -> List[str]:
        """提取来源条目 ID。

        LLM 返回的是序号（如 1,3,5），需映射为实际条目 ID。
        """
        pattern = rf"{section_name}:\s*\n(.*?)(?=\n[A-Z]+:|$)"
        match = re.search(pattern, response, re.DOTALL)
        if not match:
            return []

        block = match.group(1)
        sources_match = re.search(r"-?\s*SOURCES:\s*(.+?)(?=\n-|\Z)", block, re.DOTALL)
        if not sources_match:
            return []

        sources_str = sources_match.group(1).strip()
        # 解析序号
        try:
            indices = [int(x.strip()) for x in re.split(r"[,\s]+", sources_str) if x.strip()]
        except ValueError:
            return []

        # 映射为条目 ID（序号是 1-based）
        source_ids = []
        for idx in indices:
            if 1 <= idx <= len(batch):
                entry_id = batch[idx - 1].get("id", "")
                if entry_id:
                    source_ids.append(entry_id)

        return source_ids

    # ===== 持久化 =====

    def _write_evolutions(self, evolutions: List[Dict]) -> None:
        """将进化结果追加到 Layer 1。"""
        layer1_content = self._store.read_layer1()
        new_lines = []

        for evo in evolutions:
            evo_type = evo.get("type", "")
            conclusion = evo.get("conclusion", "")
            confidence = evo.get("confidence", 0.0)
            sources = evo.get("sources", [])

            # 格式: - [EVOLVED][TYPE] 结论
            #        来源: Query X, Y | 置信度: 0.XX | 进化时间: YYYY-MM-DD
            date_str = datetime.now().strftime("%Y-%m-%d")
            is_hypothesis = confidence < 0.6
            hypothesis_tag = " [HYPOTHESIS]" if is_hypothesis else ""

            line = f"- [EVOLVED][{evo_type}]{hypothesis_tag} {conclusion}"
            line += f"\n  来源: {', '.join(sources[:5])} | 置信度: {confidence:.2f} | 进化时间: {date_str}"

            new_lines.append(line)

        if new_lines:
            if layer1_content and layer1_content.strip():
                new_content = layer1_content.rstrip() + "\n" + "\n".join(new_lines)
            else:
                new_content = "\n".join(new_lines)
            self._store.write_layer1(new_content)

    # ===== 辅助 =====

    def _call_llm(self, prompt: str, timeout: int = 30) -> Optional[str]:
        """调用 LLM，带超时保护。

        进化操作不设硬超时中断（设计文档 4.8 节），
        但线程级超时仍保留以防止无限等待。
        """
        if not self._llm_chat_fn:
            return None

        import threading

        result = {"response": None, "done": False}

        def _call():
            try:
                response = self._llm_chat_fn(
                    prompt,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
                result["response"] = response
            except Exception as e:
                logger.error(f"LLM 调用异常: {e}")
            finally:
                result["done"] = True

        thread = threading.Thread(target=_call, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if not result["done"]:
            logger.warning(f"LLM 进化超时（{timeout}s）")
            return None

        return result["response"]

    def _report_progress(
        self, batch: int, total_batches: int, stage: str, elapsed: float
    ) -> None:
        """报告进度。"""
        if self._progress_callback:
            try:
                self._progress_callback(batch, total_batches, stage, elapsed)
            except Exception:
                pass
        else:
            logger.info(f"[记忆进化] {stage} (批次 {batch}/{total_batches}) | 已耗时 {elapsed:.0f}s")

    def _get_builtin_prompt(self, prompt_type: str) -> str:
        """获取内置 Prompt 模板。"""
        prompts = {
            "pattern": (
                "请分析：\n"
                "1. 是否存在隐含的模式、偏好或规则？\n"
                "2. 如果存在，用一句话精确描述该模式。\n"
                "3. 评估你的置信度（0.0~1.0），并给出理由。\n"
                "输出格式：\n"
                "- CONCLUSION: [模式描述] 或 NONE\n"
                "- CONFIDENCE: [0.0~1.0]\n"
                "- REASONING: [推理过程]\n"
                "- SOURCES: [来源条目序号]"
            ),
            "conflict": (
                "请分析：\n"
                "1. 这些记忆中是否存在相互矛盾的信息？\n"
                "2. 如果存在矛盾，哪条记忆正确？或是否存在更高级的统一解释？\n"
                "3. 给出统一结论。\n"
                "输出格式：\n"
                "- CONCLUSION: [统一结论] 或 NONE\n"
                "- CONFIDENCE: [0.0~1.0]\n"
                "- REASONING: [仲裁理由]\n"
                "- SOURCES: [来源条目序号]"
            ),
            "generalization": (
                "请分析：\n"
                "1. 这些记录是否存在共性原因或通用模式？\n"
                "2. 如果存在，归纳出一条可复用的预防规则或解决策略。\n"
                "3. 列出该规则覆盖的具体案例。\n"
                "输出格式：\n"
                "- RULE: [通用规则] 或 NONE\n"
                "- CASES: [案例序号列表]\n"
                "- CONFIDENCE: [0.0~1.0]\n"
                "- REASONING: [归纳过程]\n"
                "- SOURCES: [来源条目序号]"
            ),
            "trend": (
                "请分析：\n"
                "1. 项目在架构、技术选型、关注点方面是否存在明显的演进趋势？\n"
                "2. 如果存在，描述趋势并给出预测建议。\n"
                "3. 如果数据不足以判断趋势，返回 NONE。\n"
                "输出格式：\n"
                "- TREND: [趋势描述] 或 NONE\n"
                "- PREDICTION: [预测建议]\n"
                "- CONFIDENCE: [0.0~1.0]\n"
                "- REASONING: [分析过程]\n"
                "- SOURCES: [来源条目序号]"
            ),
        }
        return prompts.get(prompt_type, "")
