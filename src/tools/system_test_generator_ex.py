#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
系统测试用例自动生成器。
读取系统规格文档，解析测试场景，结合 LLM 能力自动生成结构化系统测试用例（JSON 格式）。
可作为独立脚本运行。

与 unit_test_generator_ex.py 的区别：
- unit 版使用 jedi 分析代码结构获取先验知识
- system 版解析系统规格文档的第 7 章「系统测试场景」获取先验知识
- 两者共享相同的架构：配置加载、并发批量生成、验证纠错、ID 生成
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def _load_global_config() -> Any:
    """尝试加载全局配置，失败则返回 None。"""
    try:
        from src.utility.config_loader import load_config
        return load_config()
    except Exception:
        return None


def _get_llm_client(cfg: Any) -> Tuple[OpenAI, str]:
    """根据全局配置创建 OpenAI 客户端并返回模型名。"""
    if cfg is None:
        raise RuntimeError("无法加载全局配置，请确保在项目根目录运行或配置正确。")

    provider: str = cfg.model.provider
    provider_cfg = getattr(cfg, provider)
    api_key: str = provider_cfg.api_key
    base_url: str = provider_cfg.base_url
    model_name: str = provider_cfg.model_name

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model_name


# ---------------------------------------------------------------------------
# 命令行参数
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="系统测试用例自动生成器 —— 基于系统规格文档生成 JSON 测试用例"
    )
    parser.add_argument(
        "--spec",
        type=str,
        default=None,
        help="系统规格文档路径（绝对路径），默认从 spec/myclaude_spec.md 读取",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出测试用例文件的路径（绝对路径），默认输出到项目根目录/tests/system_test_cases_时间戳.json",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# 规格文档解析
# ---------------------------------------------------------------------------

def _read_spec(spec_path: Path) -> str:
    """读取系统规格文档。"""
    return spec_path.read_text(encoding="utf-8")


def _extract_test_scenarios(spec_text: str) -> List[Dict[str, Any]]:
    """从规格文档中解析系统测试场景。

    解析「## 7. 系统测试场景」章节，提取所有场景表格行。
    每个场景包含：scenario_id、section、description、expected_behavior。
    """
    scenarios: List[Dict[str, Any]] = []

    lines = spec_text.split("\n")
    start_idx = -1
    end_idx = len(lines)

    for i, line in enumerate(lines):
        if "## 7. 系统测试场景" in line:
            start_idx = i
        elif start_idx >= 0 and line.startswith("## 8."):
            end_idx = i
            break

    if start_idx < 0:
        return scenarios

    section_lines = lines[start_idx:end_idx]
    current_section = ""

    for line in section_lines:
        stripped = line.strip()
        # 匹配子章节标题，如 "### 7.1 对话引擎测试"
        sec_match = re.match(r"^###\s+7\.\d+\s+(.+)$", stripped)
        if sec_match:
            current_section = sec_match.group(1).strip()
            continue

        # 匹配表格行，格式: | TS-7.x.x | 描述 | 预期行为 |
        row_match = re.match(
            r"^\|\s*(TS-[\d.]+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$",
            stripped,
        )
        if row_match:
            scenario_id = row_match.group(1).strip()
            description = row_match.group(2).strip()
            expected = row_match.group(3).strip()
            # 跳过表头行
            if scenario_id.startswith("TS-") and "场景" not in scenario_id:
                scenarios.append({
                    "scenario_id": scenario_id,
                    "section": current_section,
                    "description": description,
                    "expected_behavior": expected,
                })

    return scenarios


# ---------------------------------------------------------------------------
# ID 生成
# ---------------------------------------------------------------------------

def _section_abbr(section: str) -> str:
    """从章节名生成缩写。"""
    mapping = {
        "对话引擎测试": "ENG",
        "工具解析测试": "TLS",
        "记忆系统测试": "MEM",
        "CLI 命令测试": "CLI",
        "测试模式测试": "TMD",
        "A2A 服务测试": "A2A",
        "日志系统测试": "LOG",
        "安全性测试": "SEC",
    }
    for key, val in mapping.items():
        if key in section:
            return val
    return section[:2].upper() if section else "XX"


def generate_ids(cases: List[Dict[str, Any]], start_counter: int = 0) -> int:
    """为测试用例生成唯一 ID，返回下一个可用计数器。"""
    counter = start_counter
    for case in cases:
        section = case.get("section", "")
        abbr = _section_abbr(section)
        cid = f"ST-{abbr}-{counter:03d}"
        case["id"] = cid
        counter += 1
    return counter


# ---------------------------------------------------------------------------
# LLM 交互
# ---------------------------------------------------------------------------

def _build_batch_prompt(scenarios: List[Dict[str, Any]]) -> str:
    """为一批场景构建 LLM prompt，要求生成系统测试条目。

    特别注意：prompt 中不使用 XML 关键词（如尖括号包裹的工具名），
    而是用引号包裹的形式代替，避免 XML 解析冲突。
    """
    scenario_descriptions: List[str] = []
    for idx, s in enumerate(scenarios):
        scenario_descriptions.append(
            f"### 场景 {idx}: {s['scenario_id']}\n"
            f"- 所属模块: {s['section']}\n"
            f"- 场景描述: {s['description']}\n"
            f"- 预期行为: {s['expected_behavior']}"
        )

    prompt = f"""你是一个系统测试用例生成专家。根据以下系统测试场景，为 MyClaude 终端 AI 编程助手生成系统测试用例。

## 被测系统简介

MyClaude 是一个基于国产大模型的终端 AI 编程助手。系统通过 XML 工具协议驱动 LLM 与本地文件系统交互。
LLM 在响应文本中嵌入 XML 标签触发工具调用，主要工具包括：
- 'create'：创建新文件
- 'str_replace'：精确替换文件内容
- 'bash'：执行 Shell 命令
- 'done'：终止任务
- 'file_view'：查看文件/目录
- 'use_skill'：加载技能指令

## 测试场景信息

{chr(10).join(scenario_descriptions)}

## 生成要求

每个场景生成 1~3 个测试用例（覆盖正常流程、边界条件、异常情况），以 JSON 数组返回。

每个用例必须包含以下字段（缺一不可）：
- "scenario_index": 对应上面「场景 N」的索引（整数）
- "user_prompt": 发送给 MyClaude 的自然语言指令（字符串），模拟真实用户输入
- "description": 测试用例名称与描述（字符串）
- "expected_behavior": 期望行为描述（字符串），必须包含具体的、可验证的判定标准
- "check_type": 验证类型（字符串），必须是以下之一：
  - "file_created": 验证文件被创建
  - "file_modified": 验证文件被修改
  - "tool_chain": 验证 XML 工具链完整执行
  - "log_generated": 验证日志文件生成
  - "startup": 验证系统正常启动
  - "memory_aware": 验证记忆系统感知
  - "skill_triggered": 验证 Skill 被触发
  - "path_safety": 验证路径安全
  - "general": 通用验证

### user_prompt 编写规范（重要）
- 必须是自然语言指令，模拟真实用户在终端输入的内容
- 长度适中（10~200 字符），不要太短或太长
- 正确示例：「写一个 Python 函数计算斐波那契数列」「用相对路径 test.py 创建一个文件」
- 错误示例：「测试」（太短）、「请帮我创建一个文件，文件名是 test.py，内容是 print hello 然后保存」（太长）

### expected_behavior 编写规范（重要）
- 必须包含具体的、可验证的行为描述，不能模糊笼统
- 必须包含通过/失败的判定关键词（如"应该"、"必须"、"不应"、"不能"、"应使用"、"应输出"等）
- 正确示例：「MyClaude 应使用 'create' 工具创建一个 Python 文件，文件中包含 hello 函数定义，完成后输出 'done' 标签」
- 错误示例：「正常执行」「处理成功」（太模糊，评判 LLM 无法判定）
- 边界/异常用例必须说明期望的异常类型或具体行为
- 重要：在 expected_behavior 中描述 XML 工具时，不要使用尖括号语法，请用引号包裹工具名，如 'create'、'str_replace'、'done' 等

### check_type 选择指南
- 涉及文件创建/修改 -> file_created / file_modified
- 涉及 XML 工具调用完整性 -> tool_chain
- 涉及日志文件生成 -> log_generated
- 涉及系统启动/配置加载 -> startup
- 涉及记忆系统感知 -> memory_aware
- 涉及 Skill 触发 -> skill_triggered
- 涉及路径安全/拦截 -> path_safety
- 其他通用验证 -> general

### 输出格式
只输出 JSON 数组，不要额外文字或 markdown 标记。

示例：
[
    {{
        "scenario_index": 0,
        "user_prompt": "写一个 Python 函数 hello",
        "description": "验证代码生成能力 - 创建函数",
        "expected_behavior": "MyClaude 应使用 'create' 工具创建一个 Python 文件，文件中包含 hello 函数定义，完成后输出 'done' 标签",
        "check_type": "file_created"
    }}
]
"""
    return prompt


def _extract_json(text: str) -> Optional[List[Dict[str, Any]]]:
    """从 LLM 响应中提取 JSON 数组。"""
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # 尝试提取代码块
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            result = json.loads(m.group(1).strip())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # 尝试提取最外层方括号
    m = re.search(r"\[([\s\S]*)\]", text)
    if m:
        try:
            result = json.loads("[" + m.group(1) + "]")
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    return None


def _call_llm(
        client: OpenAI,
        model: str,
        prompt: str,
        max_tokens: int = 4096,
) -> Optional[List[Dict[str, Any]]]:
    """调用 LLM 并解析 JSON 响应。"""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0,
        )
        content = resp.choices[0].message.content or ""
        return _extract_json(content)
    except Exception as e:
        print(f"[LLM 调用失败] {e}")
        return None


def _assemble_cases(
    raw_items: List[Dict[str, Any]],
    scenarios_batch: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """将 LLM 返回的测试条目组装成完整测试用例（不含 ID）。"""
    cases: List[Dict[str, Any]] = []
    for item in raw_items:
        idx = item.get("scenario_index", -1)
        if not isinstance(idx, int) or idx < 0 or idx >= len(scenarios_batch):
            continue
        scenario = scenarios_batch[idx]

        cases.append({
            "id": "",  # 后续 generate_ids 统一生成
            "scenario_id": scenario["scenario_id"],
            "section": scenario["section"],
            "user_prompt": item.get("user_prompt", ""),
            "description": item.get("description", ""),
            "expected_behavior": item.get("expected_behavior", ""),
            "check_type": item.get("check_type", "general"),
            "_scenario_index": idx,  # 临时字段，纠错时使用
        })
    return cases


# ---------------------------------------------------------------------------
# 兜底检查与纠错
# ---------------------------------------------------------------------------

_VALID_CHECK_TYPES = {
    "file_created", "file_modified", "tool_chain", "log_generated",
    "startup", "memory_aware", "skill_triggered", "path_safety", "general",
}

_VERDICT_KEYWORDS = [
    "应该", "必须", "不应", "不能", "应使用", "应输出", "应返回",
    "应触发", "应拒绝", "应拦截", "应降级", "应自动", "应包含",
    "应生成", "应正常", "应报错", "应提示", "应清除", "应展开",
    "应保存", "应显示", "应打印", "应启动", "应退出", "应崩溃",
    "PASS", "FAIL", "通过", "失败",
]


def _validate_single_case(case: Dict[str, Any], seen_ids: set) -> List[str]:
    """验证单个测试用例，返回错误列表。"""
    errors: List[str] = []
    required_fields = [
        "id", "description", "user_prompt", "expected_behavior", "check_type",
    ]
    for field in required_fields:
        if field not in case or not case.get(field):
            errors.append(f"缺少必填字段 '{field}' 或字段为空")

    # 检查 user_prompt 长度
    up = case.get("user_prompt", "")
    if not isinstance(up, str):
        errors.append("user_prompt 必须是字符串")
    else:
        if len(up.strip()) < 10:
            errors.append(f"user_prompt 过短（{len(up.strip())} 字符），至少需要 10 字符")
        if len(up.strip()) > 200:
            errors.append(f"user_prompt 过长（{len(up.strip())} 字符），最多 200 字符")

    # 检查 expected_behavior 包含判定关键词
    eb = case.get("expected_behavior", "")
    if isinstance(eb, str) and eb.strip():
        if not any(kw in eb for kw in _VERDICT_KEYWORDS):
            errors.append("expected_behavior 缺少明确的判定关键词（如'应该'、'必须'、'不应'等）")

    # 检查 check_type 合法性
    ct = case.get("check_type", "")
    if ct and ct not in _VALID_CHECK_TYPES:
        errors.append(f"check_type '{ct}' 无效，必须是: {_VALID_CHECK_TYPES}")

    # 检查 description 非空
    desc = case.get("description", "")
    if not isinstance(desc, str) or not desc.strip():
        errors.append("description 不能为空")

    # 检查 id 唯一性
    cid = case.get("id", "")
    if cid and cid in seen_ids:
        errors.append(f"id '{cid}' 重复")
    if cid:
        seen_ids.add(cid)

    return errors


def validate_all_cases(
        cases: List[Dict[str, Any]],
        seen_ids: set,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """验证所有测试用例，返回 (有效用例, 错误用例及错误信息)。"""
    valid: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for case in cases:
        case_errors = _validate_single_case(case, seen_ids)
        if case_errors:
            errors.append({
                "original_case": case,
                "errors": case_errors,
            })
        else:
            valid.append(case)

    return valid, errors


# ---------------------------------------------------------------------------
# LLM 纠错
# ---------------------------------------------------------------------------

def _build_fix_prompt(
    error_cases: List[Dict[str, Any]],
    all_scenarios: List[Dict[str, Any]],
) -> str:
    """构建纠错 prompt。通过 all_scenarios 查找场景信息。"""
    scenario_index_map: Dict[str, int] = {}
    for idx, s in enumerate(all_scenarios):
        scenario_index_map[s["scenario_id"]] = idx

    error_descs: List[str] = []
    for ec in error_cases:
        oc = ec["original_case"]
        sid = oc.get("scenario_id", "?")
        si = scenario_index_map.get(sid, -1)
        if si >= 0:
            scenario = all_scenarios[si]
            sec_desc = f"{scenario['section']} - {scenario['description']}"
        else:
            sec_desc = "未知场景"
        error_descs.append(
            f"### scenario_index={si}: {sid}\n"
            f"- 场景描述: {sec_desc}\n"
            f"- 原 user_prompt: {oc.get('user_prompt', '')}\n"
            f"- 原 description: {oc.get('description', '')}\n"
            f"- 原 expected_behavior: {oc.get('expected_behavior', '')}\n"
            f"- 原 check_type: {oc.get('check_type', '')}\n"
            f"- 错误: {ec['errors']}"
        )

    prompt = f"""以下系统测试用例存在格式或内容错误，请修正。

{chr(10).join(error_descs)}

## 修正要求

输出修正后的 JSON 数组，每个元素字段：
- "scenario_index": 整数，与上面相同的 scenario_index
- "user_prompt": 修正后的自然语言指令（10~200 字符）
- "description": 修正后的测试描述
- "expected_behavior": 修正后的期望行为，必须包含判定关键词（如"应该"、"必须"、"不应"等）
- "check_type": 修正后的验证类型，必须是以下之一：file_created, file_modified, tool_chain, log_generated, startup, memory_aware, skill_triggered, path_safety, general

重要：在 expected_behavior 中描述 XML 工具时，不要使用尖括号语法，请用引号包裹工具名，如 'create'、'str_replace'、'done' 等。

只输出 JSON 数组，不要额外文字。
"""
    return prompt


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """查找项目根目录。"""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "src").is_dir() and (current / "config").is_dir():
            return current
        current = current.parent
    return Path.cwd()


def main() -> None:
    from datetime import datetime

    start_time = datetime.now()
    print(f"任务开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 加载配置
    cfg = _load_global_config()
    args = parse_args()
    project_root = _find_project_root()

    # 确定规格文档路径
    if args.spec is not None:
        spec_path = Path(args.spec).resolve()
    else:
        spec_path = project_root / "spec" / "myclaude_spec.md"

    # 确定输出路径
    if args.output is not None:
        output_path = Path(args.output).resolve()
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = (project_root / "tests" / f"system_test_cases_{timestamp}.json").resolve()

    error_output_path = output_path.with_suffix("")
    error_output_path = Path(str(error_output_path) + "_errors.json")
    if error_output_path == output_path:
        error_output_path = output_path.with_name(
            output_path.stem + "_errors" + output_path.suffix
        )

    if not spec_path.is_file():
        print(f"错误: 规格文档不存在: {spec_path}")
        sys.exit(1)

    # 加载 LLM 客户端
    print("[1/7] 加载配置...")
    if cfg is None:
        print("警告: 无法加载全局配置，将尝试使用环境变量。")
    client, model_name = _get_llm_client(cfg)
    print(f"  使用模型: {model_name}")
    print(f"  规格文档: {spec_path}")
    print(f"  output: {output_path}")

    # 读取规格文档
    print("[2/7] 读取系统规格文档...")
    spec_text = _read_spec(spec_path)
    print(f"  文档长度: {len(spec_text)} 字符")

    # 解析测试场景
    print("[3/7] 解析系统测试场景...")
    all_scenarios = _extract_test_scenarios(spec_text)
    print(f"  共提取 {len(all_scenarios)} 个测试场景")
    for s in all_scenarios:
        print(f"    - {s['scenario_id']}: [{s['section']}] {s['description']}")

    if not all_scenarios:
        print("没有找到可测试的场景，退出。")
        output_path.write_text("[]", encoding="utf-8")
        return

    # LLM 批量生成测试条目（并发）
    print("[4/7] 调用 LLM 生成系统测试用例（并发）...")
    batch_size = 5  # 每批最多 5 个场景
    all_raw_items: List[Dict[str, Any]] = []

    # 构建所有批次: (batch_num, batch, global_start_index)
    batches: List[Tuple[int, List[Dict[str, Any]], int]] = []
    global_scenario_index = 0
    for i in range(0, len(all_scenarios), batch_size):
        batch = all_scenarios[i: i + batch_size]
        batch_num = i // batch_size + 1
        batches.append((batch_num, batch, global_scenario_index))
        global_scenario_index += len(batch)

    def _process_batch(
        batch_info: Tuple[int, List[Dict[str, Any]], int]
    ) -> Tuple[int, Optional[List[Dict[str, Any]]], int]:
        """处理单个批次，返回 (batch_num, result, global_start_index)。"""
        batch_num, batch, gidx = batch_info
        scenario_ids = [f"{s['scenario_id']}: {s['description']}" for s in batch]
        print(f"  [批次 {batch_num}] 开始处理 ({len(batch)} 个场景):")
        for sid in scenario_ids:
            print(f"    - {sid}")
        prompt = _build_batch_prompt(batch)
        result = _call_llm(client, model_name, prompt, max_tokens=8192)
        if result:
            for item in result:
                if isinstance(item, dict) and "scenario_index" in item:
                    item["scenario_index"] = item["scenario_index"] + gidx
            print(f"    [批次 {batch_num}] LLM 返回 {len(result)} 条")
        else:
            print(f"    [批次 {batch_num}] 生成失败，跳过")
        return batch_num, result, gidx

    max_workers = min(5, len(batches)) if batches else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process_batch, b): b for b in batches}
        results_by_batch: Dict[int, Optional[List[Dict[str, Any]]]] = {}
        for future in as_completed(futures):
            batch_num, result, _ = future.result()
            results_by_batch[batch_num] = result

    # 按批次顺序合并结果
    for batch_num, _, _ in batches:
        result = results_by_batch.get(batch_num)
        if result:
            all_raw_items.extend(result)

    if not all_raw_items:
        print("LLM 未能生成任何用例，退出。")
        output_path.write_text("[]", encoding="utf-8")
        return

    # 组装完整用例
    all_raw_cases = _assemble_cases(all_raw_items, all_scenarios)
    if not all_raw_cases:
        print("组装后无有效用例，退出。")
        output_path.write_text("[]", encoding="utf-8")
        return
    filtered_count = len(all_raw_items) - len(all_raw_cases)
    if filtered_count > 0:
        print(f"  LLM 返回 {len(all_raw_items)} 条，组装成功 {len(all_raw_cases)} 个用例"
              f"（过滤 {filtered_count} 条格式不匹配）")
    else:
        print(f"  组装 {len(all_raw_cases)} 个用例")

    # 生成 ID
    print("[5/7] 生成用例 ID...")
    id_counter = generate_ids(all_raw_cases, start_counter=0)

    # 兜底检查与纠错
    print("[6/7] 兜底检查与纠错...")
    seen_ids: set = set()
    max_retries = 3

    valid_cases, error_cases = validate_all_cases(all_raw_cases, seen_ids)

    # 错误用例清理 _scenario_index 后保存（用集合去重）
    def _error_key(err: Dict[str, Any]) -> tuple:
        oc = err["original_case"]
        return (oc.get("scenario_id", ""), oc.get("user_prompt", ""), oc.get("description", ""))

    seen_error_keys: set = set()
    final_errors: List[Dict[str, Any]] = []
    for ec in error_cases:
        oc = ec["original_case"].copy()
        oc.pop("_scenario_index", None)
        err_entry = {"original_case": oc, "errors": ec["errors"]}
        ek = _error_key(err_entry)
        if ek not in seen_error_keys:
            seen_error_keys.add(ek)
            final_errors.append(err_entry)

    for retry in range(1, max_retries + 1):
        if not error_cases:
            break
        print(f"  第 {retry} 次纠错重试 ({len(error_cases)} 个错误)...")
        fix_prompt = _build_fix_prompt(error_cases, all_scenarios)
        fixed = _call_llm(client, model_name, fix_prompt, max_tokens=8192)
        if not fixed:
            print("    纠错调用失败，保留原始错误")
            break
        # 将 LLM 纠错结果组装并重新验证
        fixed_cases = _assemble_cases(fixed, all_scenarios)
        id_counter = generate_ids(fixed_cases, start_counter=id_counter)
        seen_ids_retry: set = seen_ids.copy()
        new_valid, new_errors = validate_all_cases(fixed_cases, seen_ids_retry)
        valid_cases.extend(new_valid)
        for case in new_valid:
            case.pop("_scenario_index", None)
        error_cases = new_errors
        for ec in error_cases:
            oc = ec["original_case"].copy()
            oc.pop("_scenario_index", None)
            err_entry = {"original_case": oc, "errors": ec["errors"]}
            ek = _error_key(err_entry)
            if ek not in seen_error_keys:
                seen_error_keys.add(ek)
                final_errors.append(err_entry)
        print(f"    修正后: {len(new_valid)} 个有效, {len(new_errors)} 个仍错误")
    print(f"  最终: {len(valid_cases)} 个有效, {len(final_errors)} 个错误")

    # 清理所有有效用例的 _scenario_index
    for case in valid_cases:
        case.pop("_scenario_index", None)

    # 输出文件
    print("[7/7] 输出结果...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(valid_cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  有效用例已输出到: {output_path} ({len(valid_cases)} 个)")

    if final_errors:
        error_output_path.parent.mkdir(parents=True, exist_ok=True)
        error_output_path.write_text(
            json.dumps(final_errors, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  错误用例已输出到: {error_output_path} ({len(final_errors)} 个)")
    else:
        print("  无错误用例")

    end_time = datetime.now()
    elapsed = end_time - start_time
    print(f"任务结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"总耗时: {elapsed}")
    print("完成！")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"发生未预期错误: {e}")
        traceback.print_exc()
        sys.exit(1)
