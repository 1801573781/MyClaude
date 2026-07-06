"""
系统测试用例生成器

读取 myclaude_test_spec.md，对每个需系统测试的需求，调用 LLM 生成符合
automation framework 格式的 JSON 测试用例，输出到 tests/ 目录。
"""

import json
import re
import sys
from pathlib import Path

from src.utility.config_loader import global_cfg

_PROJECT_ROOT = Path(global_cfg.base_path.project_root)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _read_spec(spec_path: Path = None) -> str:
    if spec_path is None:
        spec_path = _PROJECT_ROOT / "spec" / "myclaude_test_spec.md"
    return spec_path.read_text(encoding="utf-8")


def _extract_req_ids(spec_text: str, prefix: str) -> list[str]:
    ids = []
    for m in re.finditer(rf"\b({prefix}-\d{{3}})\b", spec_text):
        ids.append(m.group(1))
    return sorted(set(ids))


def _is_system_testable(req_id: str) -> bool:
    """判断是否适合系统测试（需 LLM/完整进程参与）。"""
    sys_prefixes = ["QL-", "CL-", "ME-01", "ME-02", "ME-03", "ME-04", "SL-0",
                    "LL-", "SK-00", "ER-00", "ER-01", "ER-02", "ER-03"]
    return any(req_id.startswith(p) for p in sys_prefixes)


_SYSTEM_TEST_SNIPPET_TEMPLATE = """你是一个严格的系统测试用例生成器。根据以下需求规格和示例，一次性生成所有系统测试用例的 JSON 数组。

## 规格文档中的系统测试规范（必须严格遵守）

{spec_section}

## 所有待生成的需求列表（共 {total_count} 个）

{req_list}

## 规则
1. user_prompt 是要实际执行给 MyClaude 进程的命令或自然语言指令。对于启动测试（check_type=startup），user_prompt 是实际的 CLI 命令（如 "myclaude --test-mode"），它代表的是整个命令行，不是 --prompt 参数的值。测试 --prompt 缺失场景时，user_prompt 应填入不带 --prompt 的命令（如 "myclaude --test-mode"），不应为空。
2. expected_behavior 必须包含明确的通过/失败判定标准，供 judge LLM 评判。
3. check_type 必须与验证目标匹配：file_created/file_modified/tool_chain/log_generated/startup/memory_aware/skill_triggered/path_safety/general
4. 每个用例必须包含 id、description、user_prompt、expected_behavior、check_type 字段
5. 输出是一个合法的 JSON 数组，不要包含 markdown 代码块标记

请直接输出 JSON 数组，不要加任何解释。"""

_SYSTEM_RETRY_TEMPLATE = """你之前生成的以下系统测试用例有错误，请根据反馈修正后重新生成这些用例的 JSON 数组。

## 规格文档中的系统测试规范

{spec_section}

## 需要修正的用例（共 {failed_count} 个）

{failed_cases_info}

## 修正要求
1. 只输出需要修正的用例的 JSON 数组（不是全部用例）
2. 根据每个用例的错误信息修正对应字段
3. 确保 id 格式为 "TC-前缀-数字"
4. 确保 user_prompt 是明确的命令或自然语言指令。对于 check_type=startup 的场景，user_prompt 是实际的 CLI 命令（如 "myclaude --test-mode"），如果测试的是缺少 --prompt 参数的场景，user_prompt 就应填入那个不带 --prompt 的命令，不应为空
5. 确保 expected_behavior 包含明确的通过/失败判定标准
6. 确保 check_type 是有效值

请直接输出修正后的 JSON 数组，不要加任何解释。"""


def _get_spec_snippet(spec_text: str, req_id: str) -> str:
    lines = spec_text.split("\n")
    found = []
    capture = False
    for line in lines:
        if req_id in line:
            capture = True
        if capture:
            found.append(line)
            if line.strip().startswith("|") and req_id not in line:
                if len(found) > 5:
                    break
    return "\n".join(found) if found else f"需求 {req_id}"


def _get_spec_system_test_section(spec_text: str) -> str:
    """提取 spec 文档中系统测试规范部分（第12章）。"""
    lines = spec_text.split("\n")
    in_section = False
    section_lines = []
    for line in lines:
        if "## 12. 系统测试用例规范" in line:
            in_section = True
        elif in_section:
            if line.startswith("## 13.") or line.startswith("---") or line.startswith("> **文档结束**"):
                break
            section_lines.append(line)
    return "\n".join(section_lines) if section_lines else spec_text


def _call_llm(prompt: str, max_tokens: int = 2000) -> str:
    """调用 LLM 生成测试用例。"""
    import openai

    model_provider = global_cfg.model.provider
    provider_cfg = getattr(global_cfg, model_provider)
    api_key = provider_cfg.api_key
    base_url = provider_cfg.base_url
    model_name = provider_cfg.model_name
    extra_body = getattr(provider_cfg, 'extra_body', None)

    client = openai.OpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "你是一个严格的测试用例生成器，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


_VALID_CHECK_TYPES = {
    "file_created", "file_modified", "tool_chain", "log_generated",
    "startup", "memory_aware", "skill_triggered", "path_safety", "general",
}


def _validate_system_test_case(case: dict) -> str | None:
    """验证系统测试用例的合法性，返回错误信息或 None（表示通过）。"""
    for field in ["id", "description", "user_prompt", "expected_behavior", "check_type"]:
        if not case.get(field):
            if field == "user_prompt":
                return "缺少必填字段 'user_prompt' 或字段为空。对于 startup 类型测试，user_prompt 应填入实际 CLI 命令（如 'myclaude --test-mode'），不是 --prompt 参数的值"
            return f"缺少 {field} 字段"
    case_id = case["id"]
    if not re.match(r"^TC-[A-Z]+-\d{3}$", case_id):
        return f"id 格式错误: '{case_id}'，应为 'TC-前缀-数字'"
    check_type = case["check_type"]
    if check_type not in _VALID_CHECK_TYPES:
        return f"check_type '{check_type}' 无效"
    user_prompt = case["user_prompt"]
    if len(user_prompt.strip()) < 10:
        return "user_prompt 过短"
    expected = case["expected_behavior"]
    verdict_keywords = ["通过", "失败", "PASS", "FAIL", "应该", "必须", "不应", "不能"]
    if not any(kw in expected for kw in verdict_keywords):
        return "expected_behavior 缺少明确的通过/失败判定标准"
    return None


def _build_sys_req_list(spec_text: str, sys_ids: list[str]) -> str:
    """构建系统测试需求列表。"""
    lines = []
    for rid in sys_ids:
        snip = _get_spec_snippet(spec_text, rid)
        lines.append(f"### {rid}\n{snip[:300]}")
    return "\n\n".join(lines)


def generate_system_test_cases(spec_text: str, max_retries: int = 3) -> list[dict]:
    """批量生成所有系统测试用例，无效后整批重试。"""
    req_ids = _extract_req_ids(spec_text, "QL")
    req_ids += _extract_req_ids(spec_text, "CL")
    req_ids += _extract_req_ids(spec_text, "SL")
    req_ids += _extract_req_ids(spec_text, "SK")
    req_ids += _extract_req_ids(spec_text, "ME")
    req_ids += _extract_req_ids(spec_text, "LL")
    req_ids += _extract_req_ids(spec_text, "ER")
    req_ids += _extract_req_ids(spec_text, "FO")

    sys_ids = [r for r in req_ids if _is_system_testable(r)]
    total = len(sys_ids)
    print(f"发现 {total} 个可系统测试的需求")

    spec_section = _get_spec_system_test_section(spec_text)
    req_list = _build_sys_req_list(spec_text, sys_ids)

    prompt = _SYSTEM_TEST_SNIPPET_TEMPLATE.format(
        spec_section=spec_section,
        total_count=total,
        req_list=req_list,
    )

    # 第一步：批量生成
    cases = []
    for attempt in range(max_retries + 1):
        raw = _call_llm(prompt, max_tokens=16000)
        parsed = _extract_json(raw)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and not item.get("id"):
                    item.setdefault("id", "TC-UNKNOWN")
            cases = parsed
            break
        elif isinstance(parsed, dict):
            cases = [parsed]
            break
        else:
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            if m:
                try:
                    cases = json.loads(m.group(0))
                    break
                except json.JSONDecodeError:
                    pass
            if attempt < max_retries:
                print(f"  JSON 解析失败，重试 {attempt+1}/{max_retries}")
            else:
                print(f"  FAIL: 多次重试仍无法解析 JSON")
                return []

    if not cases:
        print("  无有效用例")
        return []

    # 第二步：验证
    valid_cases = []
    failed_cases = []
    for case in cases:
        error = _validate_system_test_case(case)
        if error is None:
            valid_cases.append(case)
        else:
            failed_cases.append((case, error))

    print(f"  首轮验证: {len(valid_cases)} 通过, {len(failed_cases)} 失败")

    # 第三步：整批重试失败用例
    retry_round = 0
    while failed_cases and retry_round < max_retries:
        retry_round += 1
        failed_info = "\n\n".join(
            f"用例 {c.get('id','?')}: {err}" for c, err in failed_cases
        )
        retry_prompt = _SYSTEM_RETRY_TEMPLATE.format(
            spec_section=spec_section,
            failed_count=len(failed_cases),
            failed_cases_info=failed_info,
        )
        raw = _call_llm(retry_prompt, max_tokens=8000)
        parsed = _extract_json(raw)
        if isinstance(parsed, list):
            new_failed = []
            for item in parsed:
                if isinstance(item, dict):
                    error = _validate_system_test_case(item)
                    if error is None:
                        valid_cases.append(item)
                    else:
                        new_failed.append((item, error))
            failed_cases = new_failed
            print(f"  第 {retry_round} 轮修正: 仍失败 {len(failed_cases)} 条")
        else:
            print(f"  第 {retry_round} 轮修正: JSON 解析失败，放弃本轮")
            break

    if failed_cases:
        print(f"  ⚠️ {len(failed_cases)} 条用例最终仍未通过验证，已丢弃")

    return valid_cases


def save_cases(cases: list[dict], output_dir: Path, prefix: str):
    """保存所有测试用例到单个 JSON 文件。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / f"{prefix}.json"
    filepath.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已保存 {len(cases)} 条用例到 {filepath}")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="系统测试用例生成器")
    parser.add_argument(
        '--spec',
        type=str,
        default=None,
        help='需求规格文档路径（默认：spec/myclaude_test_spec.md）'
    )
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='输出文件名前缀（默认：YYYYMMDD_HHMMSS_system_test_cases）'
    )

    args = parser.parse_args()

    spec_path = Path(args.spec) if args.spec else None
    spec_text = _read_spec(spec_path)
    cases = generate_system_test_cases(spec_text)

    output_prefix = args.output
    if output_prefix is None:
        from datetime import datetime
        output_prefix = f"{datetime.now():%Y%m%d_%H%M%S}_system_test_cases"

    output_dir = _PROJECT_ROOT / "tests"
    save_cases(cases, output_dir, output_prefix)
    print(f"\n共生成 {len(cases)} 条系统测试用例")


if __name__ == "__main__":
    main()
