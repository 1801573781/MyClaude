"""
单元测试用例生成器

读取 myclaude_test_spec.md，对每个可单元测试的需求，调用 LLM 生成遵循
统一键值对 test_input 格式的 JSON 测试用例，输出到 tests/ 目录。
"""

import importlib
import inspect
import json
import re
import sys
from pathlib import Path

from src.utility.config_loader import global_cfg

_PROJECT_ROOT = Path(global_cfg.base_path.project_root)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _read_spec(spec_path: Path = None) -> str:
    """读取规格文档"""
    if spec_path is None:
        spec_path = _PROJECT_ROOT / "spec" / "myclaude_test_spec.md"
    return spec_path.read_text(encoding="utf-8")


def _extract_req_ids(spec_text: str, prefix: str) -> list[str]:
    """提取指定前缀的需求编号列表。"""
    ids = []
    for m in re.finditer(rf"\b({prefix}-\d{{3}})\b", spec_text):
        ids.append(m.group(1))
    return sorted(set(ids))


def _is_unit_testable(req_id: str) -> bool:
    """判断一个需求是否适合单元测试（纯逻辑，不依赖 LLM/完整进程）。"""
    unit_req_prefixes = [
        "TP-", "FO-", "MM-", "ME-0", "ME-02", "ME-03",
        "SL-0", "LL-", "CM-", "SK-", "ER-",
    ]
    for p in unit_req_prefixes:
        if req_id.startswith(p):
            return True
    return False


def _is_functional_unit(req_id: str) -> bool:
    """判断是否为需要完整函数调用的单元测试（而非纯解析器测试）。"""
    functional_prefixes = ["FO-", "CM-"]
    return any(req_id.startswith(p) for p in functional_prefixes)


_BATCH_GENERATION_TEMPLATE = """你是一个严格的单元测试用例生成器。根据以下需求规格和函数签名信息，一次性生成所有单元测试用例的 JSON 数组。

## 规格文档中的单元测试规范（必须严格遵守）

{spec_section}

## 所有待生成的需求列表（共 {total_count} 个）

{req_list}

## 函数签名速查表（test_input 的键名必须与此表一致）

{func_sigs}

## 输出要求
1. 输出一个合法的 JSON 数组，每个元素是一个测试用例 JSON 对象
2. 数组必须包含全部 {total_count} 个需求对应的测试用例，一个都不能少
3. 每个用例的 test_input 必须使用键值对格式 `'key1' : 'value1', 'key2' : 'value2'`，键名对应函数参数名
4. parse_tools 的 test_input 是纯文本字符串（模拟 LLM 输出的 content），不是键值对格式
5. target_function 必须是模块中真实存在的函数名。如果函数是类方法，使用 `ClassName.method_name` 格式
6. check_type 必须与函数职责精确匹配（详见规范）
7. 每个用例必须包含 id、description、target_module、target_function、test_input、expected_behavior、check_type 字段
8. 不要包含 markdown 代码块标记，直接输出 JSON 数组

请直接输出 JSON 数组，不要加任何解释。"""


_BATCH_RETRY_TEMPLATE = """你之前生成的以下测试用例有错误，请根据反馈修正后重新生成这些用例的 JSON 数组。

## 规格文档中的单元测试规范

{spec_section}

## 函数签名速查表

{func_sigs}

## 需要修正的用例（共 {failed_count} 个）

{failed_cases_info}

## 修正要求
1. 只输出需要修正的用例的 JSON 数组（不是全部用例）
2. 根据每个用例的错误信息修正对应字段
3. 确保 target_function 是模块中真实存在的函数名（类方法使用 `ClassName.method_name` 格式）
4. 确保 test_input 的键名与函数参数完全匹配
5. 确保 check_type 与函数职责匹配

请直接输出修正后的 JSON 数组，不要加任何解释。"""


def _get_spec_snippet(spec_text: str, req_id: str) -> str:
    """提取与需求编号相关的规格片段。"""
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


def _get_spec_unit_test_section(spec_text: str) -> str:
    """提取 spec 文档中单元测试规范部分（第13章）。"""
    lines = spec_text.split("\n")
    in_section = False
    section_lines = []
    for line in lines:
        if "## 13. 单元测试用例规范" in line:
            in_section = True
        elif in_section:
            if line.startswith("## ") or line.startswith("# "):
                # 遇到下一章或文档结束标记就停止
                if line.startswith("## 14.") or line.startswith("---") or line.startswith("> **文档结束**"):
                    break
            section_lines.append(line)
    if section_lines:
        return "\n".join(section_lines)
    # 降级：返回整个 spec 文档
    return spec_text


def _get_func_signature_info(target_module: str, target_function: str) -> str:
    """获取函数的签名信息字符串。"""
    try:
        mod = importlib.import_module(target_module)
        # 支持类方法：ClassName.method_name
        if "." in target_function:
            class_name, method_name = target_function.split(".", 1)
            if hasattr(mod, class_name):
                cls = getattr(mod, class_name)
                if hasattr(cls, method_name):
                    func = getattr(cls, method_name)
                    sig = inspect.signature(func)
                    return f"{target_function}{sig}"
                else:
                    return f"{target_function}(?) - 方法不存在"
            else:
                return f"{target_function}(?) - 类不存在"
        else:
            func = getattr(mod, target_function, None)
            if func and callable(func):
                sig = inspect.signature(func)
                return f"{target_function}{sig}"
            else:
                return f"{target_function}(?) - 函数不存在"
    except Exception as e:
        return f"{target_function}(?) - 导入失败: {e}"


def _build_func_sigs_table(req_list: list[str]) -> str:
    """为所有需求构建函数签名速查表。"""
    lines = []
    seen = set()
    for req_id in req_list:
        mod, func = _resolve_module_and_func(req_id)
        key = f"{mod}::{func}"
        if key not in seen:
            seen.add(key)
            sig_info = _get_func_signature_info(mod, func)
            lines.append(f"- {req_id[:2]} 类 → `{mod}` → `{sig_info}`")
    return "\n".join(lines)


def _call_llm(prompt: str, max_tokens: int = 8000) -> str:
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
    """从 LLM 输出中提取 JSON 对象。"""
    text = text.strip()
    # 移除可能的 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试用正则匹配最外层的 {}
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


# === 从 spec 文档动态解析需求映射（替代硬编码） ===

# 节号 → 模块路径（根据 spec 文档的节标题推断）
_SECTION_TO_MODULE = {
    2: "src.llm_tool.tool_executor",   # 2. 工具协议层
    3: "src.utility.file_tool",         # 3. 文件操作层
    4: "src.utility.llm_api_msg",      # 4. 消息管理层
    7: "src.query.chat_llm",           # 7. LLM 交互层
    8: "src.llm_tool.cmd_bash",        # 8. 命令执行层
}

# 兜底映射：无法从子节标题提取函数名的需求前缀
_PREFIX_FALLBACK = {
    "LL": ("src.query.chat_llm", "stream_chat"),
    "CM": ("src.llm_tool.cmd_bash", "tool_bash"),
    "MM": ("src.utility.llm_api_msg", "LLMAPIMessage.append_tool_exec_result"),
}

# 模块级缓存
_req_mapping: dict = None
_check_type_map: dict = None


def _build_req_mapping_from_spec(spec_text: str) -> dict:
    """从 spec 文档动态解析 req_id → (module, func) 映射。

    解析逻辑：
    1. 扫描 ## N. 标题 → 确定当前节号
    2. 扫描 ### N.M. 子标题（如 "工具解析（parse_tools）"）→ 提取函数名
    3. 扫描表格行 | REQ-ID | ... → 关联到当前节的模块和函数
    4. 兜底映射处理无法从标题提取的需求
    """
    mapping = {}
    current_section = None
    current_func = None

    for line in spec_text.split('\n'):
        m = re.match(r'^## (\d+)\.\s+', line)
        if m:
            current_section = int(m.group(1))
            current_func = None
            continue

        m = re.match(r'^### (\d+\.\d+)\s+(.+)', line)
        if m:
            title = m.group(2)
            func_match = re.search(r'（([\w.]+)）', title)
            current_func = func_match.group(1) if func_match else None
            continue

        if current_section in _SECTION_TO_MODULE:
            m = re.match(r'^\|\s*([A-Z]{2,3}-\d{3})\s*\|', line)
            if m and current_func:
                req_id = m.group(1)
                mapping[req_id] = (_SECTION_TO_MODULE[current_section], current_func)

    # FO-013, FO-014 在 "3.4 路径解析规则" 中（无函数名在标题中）
    mapping.setdefault("FO-013", ("src.utility.file_tool", "add_root_path"))
    mapping.setdefault("FO-014", ("src.utility.file_tool", "add_root_path"))

    # 兜底映射
    for prefix, (mod, func) in _PREFIX_FALLBACK.items():
        for req_id in re.findall(rf'\b({prefix}-\d{{3}})\b', spec_text):
            mapping.setdefault(req_id, (mod, func))

    return mapping


def _build_check_type_map_from_spec(spec_text: str) -> dict:
    """从 spec UT-008 表格解析 func_name → check_type 映射。"""
    mapping = {}
    in_section = False
    in_table = False
    for line in spec_text.split('\n'):
        if 'UT-008' in line and 'check_type 填写规则' in line:
            in_section = True
            continue
        if in_section and '| 验证目标 |' in line and 'check_type' in line:
            in_table = True
            continue
        if in_table and line.startswith('|') and '`' in line:
            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) >= 3:
                check_type = parts[1].strip('` ')
                scenario = parts[2]
                funcs = re.findall(r'`(\w+(?:\.\w+)?)\(\)`', scenario)
                for func in funcs:
                    mapping[func] = check_type
        elif in_table and not line.startswith('|'):
            break

    # 补充 spec 表格中未覆盖的映射
    mapping.setdefault("tool_bash", "general")
    mapping.setdefault("strip_thinking", "general")
    mapping.setdefault("file_view", "general")
    mapping.setdefault("add_root_path", "path_safety")
    mapping.setdefault("execute_code_tool", "tool_chain")
    mapping.setdefault("LLMAPIMessage.append_tool_exec_result", "general")
    mapping.setdefault("LLMAPIMessage.append_llm_response", "general")
    mapping.setdefault("LLMAPIMessage.init_api_msg", "general")

    return mapping


def _init_mappings(spec_text: str):
    """初始化需求映射和 check_type 映射（一次性解析并缓存）。"""
    global _req_mapping, _check_type_map
    if _req_mapping is None:
        _req_mapping = _build_req_mapping_from_spec(spec_text)
    if _check_type_map is None:
        _check_type_map = _build_check_type_map_from_spec(spec_text)


def _resolve_module_and_func(req_id: str) -> tuple[str, str]:
    """根据需求编号动态获取 target_module 和 target_function（从 spec 解析）。"""
    global _req_mapping
    if _req_mapping and req_id in _req_mapping:
        return _req_mapping[req_id]
    # 尝试前缀匹配
    if _req_mapping:
        prefix = req_id[:2]
        for req_key, (mod, func) in _req_mapping.items():
            if req_key.startswith(prefix):
                return (mod, func)
    return ("src.utility.file_tool", "file_create")


def _parse_test_input_params(test_input: str) -> list[str]:
    """从 test_input 字符串中提取参数名列表。"""
    params = []
    for m in re.finditer(r"'(\w+)'\s*:", test_input):
        params.append(m.group(1))
    return params


def _get_expected_check_type(func_name: str) -> str:
    """根据函数名动态获取预期的 check_type（从 spec 解析）。"""
    global _check_type_map
    if _check_type_map:
        return _check_type_map.get(func_name, "general")
    # 降级：极简内置映射
    builtin = {
        "file_create": "file_created",
        "file_str_replace": "file_modified",
        "parse_tools": "tool_chain",
        "execute_code_tool": "tool_chain",
        "add_root_path": "path_safety",
    }
    return builtin.get(func_name, "general")


def _validate_test_case(case: dict) -> str | None:
    """验证测试用例的合法性，返回错误信息或 None（表示通过）。"""
    # 1. 验证必填字段
    for field in ["id", "target_module", "target_function", "test_input", "expected_behavior", "check_type"]:
        if not case.get(field):
            return f"缺少 {field} 字段"

    # 2. 验证 target_module 真实存在
    target_module = case["target_module"]
    try:
        mod = importlib.import_module(target_module)
    except ModuleNotFoundError:
        return f"target_module '{target_module}' 不存在"

    # 3. 验证 target_function 真实存在（支持类方法）
    target_function = case["target_function"]
    if "." in target_function:
        class_name, method_name = target_function.split(".", 1)
        if not hasattr(mod, class_name):
            return f"类 '{class_name}' 在模块 '{target_module}' 中不存在"
        cls = getattr(mod, class_name)
        if not hasattr(cls, method_name):
            return f"方法 '{method_name}' 在类 '{class_name}' 中不存在"
        func = getattr(cls, method_name)
    else:
        if not hasattr(mod, target_function):
            return f"target_function '{target_function}' 在模块 '{target_module}' 中不存在"
        func = getattr(mod, target_function)
    if not callable(func):
        return f"'{target_function}' 不是可调用对象"

    # 4. 验证 test_input 参数与函数签名匹配（只检查无默认值的必填参数）
    test_input = case["test_input"]
    input_params = _parse_test_input_params(test_input)
    if not input_params:
        # 纯文本 test_input（如 parse_tools 的 LLM 内容），跳过参数匹配
        pass
    else:
        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError):
            pass
        else:
            # 只检查无默认值的必填参数
            required_params = [
                name for name, p in sig.parameters.items()
                if p.default is inspect.Parameter.empty
                and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            ]
            missing = [p for p in required_params if p not in input_params]
            if missing:
                return (f"test_input 缺少必填参数: {missing}"
                        f"（函数签名: {list(sig.parameters.keys())}，"
                        f"test_input 参数: {input_params}）")

    # 5. 验证 check_type 正确（parse_tools 强制 tool_chain）
    check_type = case["check_type"]
    if target_function == "parse_tools" and check_type != "tool_chain":
        return f"parse_tools 的 check_type 必须为 'tool_chain'，实际为 '{check_type}'"
    expected = _get_expected_check_type(target_function)
    if check_type != expected:
        return f"check_type 应为 '{expected}'，实际为 '{check_type}'"

    return None  # 验证通过


def _build_req_list_for_llm(unit_ids: list[str], spec_text: str) -> str:
    """构建需求列表描述，含函数签名提示和规格摘要。"""
    lines = []
    for req_id in unit_ids:
        mod, func = _resolve_module_and_func(req_id)
        spec_snip = _get_spec_snippet(spec_text, req_id)
        lines.append(f"### {req_id}\n- target_module: {mod}\n- target_function: {func}\n- spec: {spec_snip[:300]}")
    return "\n\n".join(lines)


def generate_unit_test_cases(spec_text: str, max_retries: int = 3) -> list[dict]:
    """批量生成所有单元测试用例，无效后整批重试。"""
    _init_mappings(spec_text)  # 从 spec 动态解析模块/函数映射
    req_ids = _extract_req_ids(spec_text, "FO")
    req_ids += _extract_req_ids(spec_text, "TP")
    req_ids += _extract_req_ids(spec_text, "CM")
    req_ids += _extract_req_ids(spec_text, "MM")

    unit_ids = [r for r in req_ids if _is_unit_testable(r)]
    total = len(unit_ids)
    print(f"发现 {total} 个可单元测试的需求")

    spec_section = _get_spec_unit_test_section(spec_text)
    func_sigs = _build_func_sigs_table(unit_ids)
    req_list = _build_req_list_for_llm(unit_ids, spec_text)

    # 第一步：批量生成全部用例
    prompt = _BATCH_GENERATION_TEMPLATE.format(
        spec_section=spec_section,
        total_count=total,
        req_list=req_list,
        func_sigs=func_sigs,
    )

    cases = []
    for attempt in range(max_retries + 1):
        raw = _call_llm(prompt, max_tokens=16000)
        parsed = _extract_json(raw)
        if isinstance(parsed, list):
            # 补全字段
            for item in parsed:
                if isinstance(item, dict):
                    rid = item.get("id", "")
                    if rid.startswith("UT-"):
                        prefix = rid[3:5]
                        num = rid[6:] if len(rid) > 6 else ""
                        orig = f"{prefix}-{num}" if num else rid
                    else:
                        orig = rid
                    mod, func = _resolve_module_and_func(orig)
                    item.setdefault("target_module", mod)
                    item.setdefault("target_function", func)
            cases = parsed
            break
        elif isinstance(parsed, dict):
            cases = [parsed]
            break
        else:
            # 尝试从文本中提取数组
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            if m:
                try:
                    cases = json.loads(m.group(0))
                    print(f"  [尝试从文本中提取数组成功，{len(cases)} 条]")
                    break
                except json.JSONDecodeError:
                    pass
            if attempt < max_retries:
                print(f"  JSON 解析失败，重试 {attempt+1}/{max_retries}")
                prompt = f"你上一次的输出不是合法的 JSON 数组。请严格输出一个 JSON 数组，每个元素是一个测试用例对象。\n\n{_BATCH_GENERATION_TEMPLATE.format(spec_section=spec_section, total_count=total, req_list=req_list, func_sigs=func_sigs)}"
            else:
                print(f"  FAIL: 多次重试仍无法解析 JSON")
                return []

    if not cases:
        print("  无有效用例")
        return []

    # 第二步：验证并收集失败用例
    valid_cases = []
    failed_cases = []  # (case, error_message)
    for case in cases:
        error = _validate_test_case(case)
        if error is None:
            valid_cases.append(case)
        else:
            failed_cases.append((case, error))

    print(f"  首轮验证: {len(valid_cases)} 通过, {len(failed_cases)} 失败")

    # 第三步：对失败用例整批重试修正
    retry_round = 0
    while failed_cases and retry_round < max_retries:
        retry_round += 1
        failed_info = "\n\n".join(
            f"用例 {c['id']}: {err}" for c, err in failed_cases
        )
        retry_prompt = _BATCH_RETRY_TEMPLATE.format(
            spec_section=spec_section,
            func_sigs=func_sigs,
            failed_count=len(failed_cases),
            failed_cases_info=failed_info,
        )
        raw = _call_llm(retry_prompt, max_tokens=8000)
        parsed = _extract_json(raw)
        if isinstance(parsed, list):
            new_failed = []
            for item in parsed:
                if isinstance(item, dict):
                    rid = item.get("id", "")
                    prefix = rid[3:5] if rid.startswith("UT-") and len(rid) > 5 else ""
                    num = rid[6:] if rid.startswith("UT-") and len(rid) > 6 else ""
                    orig = f"{prefix}-{num}" if prefix and num else rid
                    mod, func = _resolve_module_and_func(orig)
                    item.setdefault("target_module", mod)
                    item.setdefault("target_function", func)
                    error = _validate_test_case(item)
                    if error is None:
                        valid_cases.append(item)
                    else:
                        new_failed.append((item, error))
            passed_this_round = len(parsed) - len(new_failed)
            failed_cases = new_failed
            print(f"  第 {retry_round} 轮修正: 通过 {passed_this_round} 条, 仍失败 {len(failed_cases)} 条")
        else:
            print(f"  第 {retry_round} 轮修正: JSON 解析失败，放弃本轮")
            break

    if failed_cases:
        print(f"  ⚠️ {len(failed_cases)} 条用例最终仍未通过验证")
        for c, err in failed_cases:
            print(f"    [{c.get('id', '?')}] {err}")
            print(f"      用例内容: {json.dumps(c, ensure_ascii=False)}")

    return valid_cases, failed_cases


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

    parser = argparse.ArgumentParser(description="单元测试用例生成器")
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
        help='输出文件名前缀（默认：YYYYMMDD_HHMMSS_unit_test_cases）'
    )

    args = parser.parse_args()

    spec_path = Path(args.spec) if args.spec else None
    spec_text = _read_spec(spec_path)
    valid_cases, failed_cases = generate_unit_test_cases(spec_text)

    output_prefix = args.output
    if output_prefix is None:
        from datetime import datetime
        output_prefix = f"{datetime.now():%Y%m%d_%H%M%S}_unit_test_cases"

    output_dir = _PROJECT_ROOT / "tests"
    save_cases(valid_cases, output_dir, output_prefix)
    print(f"\n共生成 {len(valid_cases)} 条单元测试用例")

    # 保存失败用例
    if failed_cases:
        from datetime import datetime
        error_file = f"{datetime.now():%Y%m%d_%H%M%S}_unit_test_cases_error.json"
        error_path = output_dir / error_file
        error_data = [{"case": c, "error": e} for c, e in failed_cases]
        error_path.write_text(
            json.dumps(error_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已保存 {len(failed_cases)} 条失败用例到 {error_path}")


if __name__ == "__main__":
    main()
