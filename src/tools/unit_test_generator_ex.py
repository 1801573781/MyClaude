#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单元测试用例自动生成器。
递归分析 Python 项目，结合 LLM 能力自动生成结构化单元测试用例（JSON 格式）。
可作为独立脚本运行。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jedi
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
        description="单元测试用例自动生成器 —— 递归分析 Python 项目并生成 JSON 测试用例"
    )
    parser.add_argument(
        "--root",
        type=str,
        default=None,
        help="Python 项目的根目录（绝对路径），默认从 config.yaml 读取 base_path.project_root",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出测试用例文件的路径（绝对路径），默认输出到 root/tests/unit_test_cases_时间戳.json",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# .gitignore 处理
# ---------------------------------------------------------------------------

def load_gitignore(root: Path) -> List[str]:
    """读取根目录下的 .gitignore，返回忽略模式列表。"""
    gi_path = root / ".gitignore"
    if not gi_path.is_file():
        return []
    lines = gi_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    patterns: List[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def should_ignore(rel_path: str, patterns: List[str]) -> bool:
    """判断相对路径是否匹配任一 gitignore 模式（简易实现）。"""
    import fnmatch

    parts = rel_path.replace("\\", "/")
    for pat in patterns:
        pat = pat.replace("\\", "/")
        # 匹配路径任意位置
        if fnmatch.fnmatch(parts, pat):
            return True
        if fnmatch.fnmatch(parts, f"*/{pat}"):
            return True
        # 目录模式
        if pat.endswith("/") and parts.startswith(pat.rstrip("/")):
            return True
    return False


# ---------------------------------------------------------------------------
# 文件收集
# ---------------------------------------------------------------------------

def collect_py_files(root: Path, ignore_patterns: List[str]) -> List[Path]:
    """递归收集所有 .py 文件（跳过被 gitignore 忽略的）。"""
    py_files: List[Path] = []
    for p in root.rglob("*.py"):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if should_ignore(str(rel), ignore_patterns):
            continue
        py_files.append(p)
    return py_files


# ---------------------------------------------------------------------------
# Jedi 分析
# ---------------------------------------------------------------------------

def _path_to_module(file_path: Path, root: Path) -> str:
    """将文件路径转为 Python 导入路径。"""
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        rel = file_path
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    # 过滤掉空字符串和 __init__
    parts = [p for p in parts if p and p != "__init__"]
    return ".".join(parts)


def analyze_file_with_jedi(file_path: Path, root: Path) -> List[Dict[str, Any]]:
    """
    使用 Jedi 分析单个 .py 文件，提取全局函数和类中的静态方法。
    返回函数信息列表，每个元素包含：
      - name: 函数名
      - target_module: 导入路径
      - params: [{"name": str, "default": str|None, "type_hint": str|None}, ...]
      - docstring: 文档字符串或空字符串
      - is_static: 是否为静态方法
    """
    source: str
    try:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    module_str = _path_to_module(file_path, root)
    results: List[Dict[str, Any]] = []

    try:
        script = jedi.Script(code=source, path=str(file_path))
        names = script.get_names(all_scopes=True, definitions=True)
    except Exception:
        return results

    seen_signatures = set()

    for name in names:
        if name.type != "function":
            continue

        # 判断是否为静态方法
        is_static = False
        if name.full_name and "." in name.full_name:
            # Jedi 中 staticmethod 的 full_name 通常不包含类名作为前缀
            # 检查父级定义
            parent = name.parent()
            if parent is not None and parent.type == "class":
                # 进一步检查是否标注了 @staticmethod
                try:
                    func_defs = name.goto(
                        follow_imports=True, follow_builtin_imports=True
                    )
                    for fd in func_defs:
                        if fd.type == "function":
                            line = fd.line - 1  # 0-based
                            # 向前查找装饰器
                            for offset in range(1, min(5, line + 1)):
                                check_line = source.splitlines()[line - offset].strip()
                                if check_line.startswith("@staticmethod"):
                                    is_static = True
                                    break
                                if check_line.startswith("def ") or check_line.startswith("class "):
                                    break
                            if is_static:
                                break
                except Exception:
                    pass

        # 只收集全局函数和静态方法
        if not is_static:
            # 检查是否为模块级函数（不在类内部）
            parent = name.parent()
            if parent is not None and parent.type == "class":
                continue  # 跳过普通方法和 classmethod

        func_name = name.name
        sig_key = f"{module_str}.{func_name}"
        if sig_key in seen_signatures:
            continue
        seen_signatures.add(sig_key)

        # 获取参数信息
        params: List[Dict[str, Optional[str]]] = []
        docstring = ""
        try:
            signatures = name.get_signatures()
            if signatures:
                sig = signatures[0]
                for param in sig.params:
                    param_info = {
                        "name": param.name,
                        "default": param.description.split("=")[-1].strip()
                        if "=" in (param.description or "")
                        else None,
                        "type_hint": None,
                    }
                    # 尝试获取类型提示
                    try:
                        if hasattr(param, "infer"):
                            # jedi 0.18+
                            pass
                    except Exception:
                        pass
                    if param.name in ("self", "cls"):
                        continue
                    params.append(param_info)

                # 获取文档字符串
                try:
                    ds = sig.docstring()
                    if ds:
                        docstring = ds.strip()
                except Exception:
                    pass
        except Exception:
            pass

        # 如果没有通过签名获取到参数，尝试从 goto 定义中提取
        if not params:
            try:
                func_defs = name.goto(
                    follow_imports=True, follow_builtin_imports=True
                )
                for fd in func_defs:
                    if fd.type == "function":
                        try:
                            sigs = fd.get_signatures()
                            if sigs:
                                for p in sigs[0].params:
                                    if p.name in ("self", "cls"):
                                        continue
                                    params.append({
                                        "name": p.name,
                                        "default": None,
                                        "type_hint": None,
                                    })
                                if not docstring:
                                    try:
                                        ds = sigs[0].docstring()
                                        if ds:
                                            docstring = ds.strip()
                                    except Exception:
                                        pass
                                break
                        except Exception:
                            pass
            except Exception:
                pass

        if not params:
            continue  # 无参数的函数暂不生成用例

        results.append({
            "name": func_name,
            "target_module": module_str,
            "params": params,
            "docstring": docstring,
            "is_static": is_static,
        })

    return results


# ---------------------------------------------------------------------------
# ID 生成
# ---------------------------------------------------------------------------

def _module_abbr(module: str) -> str:
    """从模块路径生成缩写（取每个部分前两个字符，大写）。"""
    parts = module.split(".")
    abbr_parts = [p[:2].upper() if len(p) >= 2 else p.upper() for p in parts if p]
    return "".join(abbr_parts[-2:]) if len(abbr_parts) >= 2 else (abbr_parts[-1] if abbr_parts else "XX")


# ---------------------------------------------------------------------------
# LLM 交互
# ---------------------------------------------------------------------------

def _build_batch_prompt(funcs: List[Dict[str, Any]]) -> str:
    """为一批函数构建 LLM prompt，要求生成测试条目（func_index / values / description / expected_behavior）。"""
    func_descriptions: List[str] = []
    for idx, f in enumerate(funcs):
        params_str = ", ".join(
            f"{p['name']}"
            + (f": {p['type_hint']}" if p.get("type_hint") else "")
            + (f" = {p['default']}" if p.get("default") else "")
            for p in f["params"]
        )
        doc = f["docstring"] if f["docstring"] else "(无文档字符串)"
        func_descriptions.append(
            f"### 函数 {idx}: {f['name']}\n"
            f"- 参数列表: {json.dumps([p['name'] for p in f['params']], ensure_ascii=False)}\n"
            f"- 签名: ({params_str})\n"
            f"- 文档: {doc}"
        )

    prompt = f"""你是一个 Python 单元测试用例生成专家。为以下函数生成测试用例。

## 被测函数信息

{chr(10).join(func_descriptions)}

## 生成要求

每个函数生成 3~5 个测试用例（正常值、边界值、异常值），以 JSON 数组返回。

每个用例只需要包含三个字段（缺一不可）：
- "func_index": 对应上面「函数 N」的索引（整数）
- "values": 参数值列表（顺序必须与上面的参数列表一致，值为字符串）
- "description": 测试用例名称与描述（字符串）
- "expected_behavior": 期望行为描述（字符串），精确反映给定输入下函数的预期行为

### values 格式规则
- values 是字符串列表，顺序与函数参数列表严格一致
- 每个值都用引号包裹，如 ["'D:/tmp'", "'test.py'", "None"]
- 字符串值内部如有单引号，请用双引号包裹外层，如 ["\"it's\""]
- 为每个参数生成多样化值：正常值、边界值（空字符串/0/None/空列表）、异常值（类型不匹配/超范围）

### 输出格式
只输出 JSON 数组，不要额外文字或 markdown 标记。

示例：
[
    {{
        "func_index": 0,
        "values": ["'/tmp'", "'new.py'", "'print(1)'"],
        "description": "正常创建文件",
        "expected_behavior": "文件创建成功，返回成功信息"
    }}
]
"""
    return prompt


def _extract_json(text: str) -> Optional[List[Dict[str, Any]]]:
    """从 LLM 响应中提取 JSON 数组。"""
    # 尝试直接解析
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            result = json.loads(m.group(1).strip())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # 尝试提取最外层 [ ... ]
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
    funcs_batch: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """将 LLM 返回的测试条目组装成完整测试用例（不含 ID）。"""
    cases: List[Dict[str, Any]] = []
    for item in raw_items:
        idx = item.get("func_index", -1)
        if not isinstance(idx, int) or idx < 0 or idx >= len(funcs_batch):
            continue
        func = funcs_batch[idx]
        params = [p["name"] for p in func["params"]]
        values = item.get("values", [])
        if not isinstance(values, list) or len(values) != len(params):
            continue
        # 构建 test_input
        kv_pairs = [f"'{key}' : {val}" for key, val in zip(params, values)]  # noqa
        test_input = ", ".join(kv_pairs)
        cases.append({
            "id": "",  # 后续 generate_ids 统一生成
            "target_module": func["target_module"],
            "target_function": func["name"],
            "test_input": test_input,
            "description": item.get("description", ""),
            "expected_behavior": item.get("expected_behavior", ""),
            "_func_index": idx,  # 临时字段，纠错时使用
        })
    return cases


# ---------------------------------------------------------------------------
# 兜底检查与纠错
# ---------------------------------------------------------------------------

def _validate_single_case(
        case: Dict[str, Any],
        expected_param_names: List[str],
        type_hints: Dict[str, Optional[str]],
        seen_ids: set,
) -> List[str]:
    """验证单个测试用例，返回错误列表。"""
    errors: List[str] = []
    required_fields = [
        "id", "description", "target_module",
        "target_function", "test_input", "expected_behavior",
    ]
    for field in required_fields:
        if field not in case or not case.get(field):
            errors.append(f"缺少必填字段 '{field}' 或字段为空")

    # 检查 test_input 格式
    ti = case.get("test_input", "")
    if not isinstance(ti, str):
        errors.append("test_input 必须是字符串")
    else:
        # 解析键值对（使用参数名精确匹配，避免 value 内部结构干扰）
        parsed = _parse_test_input_by_params(ti, expected_param_names)
        if parsed is None:
            errors.append(
                f"test_input 格式错误，必须是键值对格式 'key1':'value1', 'key2':'value2'，"  # noqa
                f"当前值: {ti[:100]}"
            )
        else:
            # 检查键完整性
            actual_keys = set(parsed.keys())
            expected_keys = set(expected_param_names)
            missing = expected_keys - actual_keys
            extra = actual_keys - expected_keys
            if missing:
                errors.append(f"test_input 缺少参数: {missing}")
            if extra:
                errors.append(f"test_input 包含多余参数: {extra}")

            # 检查值类型兼容性
            for key, val in parsed.items():
                hint = type_hints.get(key)
                if hint:
                    compat = _check_type_compatibility(val, hint)
                    if not compat:
                        errors.append(
                            f"参数 '{key}' 的值 '{val}' 与类型提示 '{hint}' 不兼容"
                        )

    # 检查 id 唯一性
    cid = case.get("id", "")
    if cid in seen_ids:
        errors.append(f"id '{cid}' 重复")
    seen_ids.add(cid)

    # 非空检查
    if not case.get("description", "").strip():
        errors.append("description 不能为空")
    if not case.get("expected_behavior", "").strip():
        errors.append("expected_behavior 不能为空")

    return errors


def _parse_test_input_by_params(ti: str, param_names: List[str]) -> Optional[Dict[str, str]]:
    """基于已知参数名列表解析 test_input，返回键值对字典。

    与 _parse_test_input_keys 不同：本函数利用参数名先验知识，
    避免 value 内部的键值对结构（如 'a': 'b', 'c': 'd'）被误判为外层键值对，
    导致 validate 阶段出现"多余参数"的误报。
    """
    if not ti or not isinstance(ti, str):
        return None

    result: Dict[str, str] = {}
    remaining = ti

    for i, pname in enumerate(param_names):
        # 跳过前导空白
        remaining = remaining.lstrip()
        # 匹配 '参数名' : 或 '参数名':
        prefix = f"'{pname}' : "
        if remaining.startswith(prefix):
            remaining = remaining[len(prefix):]
        elif remaining.startswith(f"'{pname}':"):
            remaining = remaining[len(f"'{pname}':"):]
        else:
            return None  # 格式不匹配

        # 确定 value 的结束位置
        if i + 1 < len(param_names):
            # 查找下一个参数名的起始位置: , '下个参数名'
            next_prefix = f", '{param_names[i + 1]}'"
            pos = remaining.find(next_prefix)
            if pos >= 0:
                value = remaining[:pos].strip()
                remaining = remaining[pos + 1:]  # 跳过逗号
            else:
                return None
        else:
            # 最后一个参数，剩余全部是值
            value = remaining.strip()
            remaining = ""

        result[pname] = value

    return result if result else None


def _parse_test_input_keys(ti: str) -> Optional[Dict[str, str]]:
    """
    解析 test_input 键值对字符串。
    格式: 'key1' : value1, 'key2' : value2
    返回键值对字典，value 保留原始字符串（含外层引号/括号等）。
    支持 value 内部含有逗号、嵌套引号、各种括号（{}、[]、()）。
    空值（如 'key' : 后无内容）在解析时 value 为空字符串。
    """
    if not ti or not isinstance(ti, str):
        return None

    result: Dict[str, str] = {}
    pos = 0
    n = len(ti)

    while pos < n:
        # 跳过空白
        while pos < n and ti[pos].isspace():
            pos += 1
        if pos >= n:
            break

        # 匹配 key: 'key' :
        m = re.match(r"'([^']+)'\s*:\s*", ti[pos:])
        if not m:
            return result if result else None
        key = m.group(1)
        pos += m.end()

        # 逐字符解析 value，在深度 0 的逗号或串尾处终止
        value_chars: List[str] = []
        depth_brace = 0    # {}
        depth_bracket = 0  # []
        depth_paren = 0    # ()
        quote_char: Optional[str] = None  # ' 或 "

        while pos < n:
            ch = ti[pos]

            if quote_char is not None:
                # 在引号内：转义处理，遇匹配引号退出
                value_chars.append(ch)
                pos += 1
                if ch == '\\':
                    if pos < n:
                        value_chars.append(ti[pos])
                        pos += 1
                elif ch == quote_char:
                    quote_char = None
            elif ch in ("'", '"'):
                # 进入引号字符串
                quote_char = ch
                value_chars.append(ch)
                pos += 1
            elif ch == '{':
                depth_brace += 1
                value_chars.append(ch)
                pos += 1
            elif ch == '}':
                depth_brace = max(0, depth_brace - 1)
                value_chars.append(ch)
                pos += 1
            elif ch == '[':
                depth_bracket += 1
                value_chars.append(ch)
                pos += 1
            elif ch == ']':
                depth_bracket = max(0, depth_bracket - 1)
                value_chars.append(ch)
                pos += 1
            elif ch == '(':
                depth_paren += 1
                value_chars.append(ch)
                pos += 1
            elif ch == ')':
                depth_paren = max(0, depth_paren - 1)
                value_chars.append(ch)
                pos += 1
            elif ch == ',' and depth_brace == 0 and depth_bracket == 0 and depth_paren == 0:
                # 遇到深度 0 的逗号，peek 后续是否为新的键值对起点
                peek_pos = pos + 1
                # 跳过空白
                while peek_pos < n and ti[peek_pos].isspace():
                    peek_pos += 1
                # 检查后续是否为 'key' : 模式
                next_is_kv = bool(
                    peek_pos < n
                    and ti[peek_pos] == "'"
                    and re.match(r"'([^']+)'\s*:\s*", ti[peek_pos:])
                )
                if next_is_kv:
                    # 确认为新键值对分隔符，当前键值对结束
                    pos += 1  # 跳过逗号
                    break
                else:
                    # 逗号是 value 的一部分，继续累积
                    value_chars.append(ch)
                    pos += 1
            else:
                value_chars.append(ch)
                pos += 1

        value = ''.join(value_chars).strip()
        result[key] = value

    return result if result else None


def _check_type_compatibility(value: str, type_hint: str) -> bool:
    """检查值字符串是否与类型提示兼容（简易检查）。"""
    hint_lower = type_hint.lower().strip()
    # 标准化
    hint_lower = re.sub(r"^optional\[(.*)\]$", r"\1", hint_lower)
    hint_lower = re.sub(r"^union\[(.*)\]$", r"\1", hint_lower)

    # 如果 hint 包含 None，总是兼容
    if "none" in hint_lower.split(","):
        if value.strip().lower() == "none":
            return True

    # 基本类型检查
    base_types = [t.strip() for t in hint_lower.split(",")]

    for bt in base_types:
        bt = bt.strip("[]")
        if bt in ("int", "float", "number"):
            # 尝试解析为数字
            v = value.strip()
            if v.lower() == "none":
                continue
            try:
                float(v)
                return True
            except ValueError:
                pass
        elif bt in ("str", "string"):
            return True
        elif bt in ("bool", "boolean"):
            if value.strip().lower() in ("true", "false", "0", "1"):
                return True
        elif bt in ("list", "dict", "tuple", "set"):
            return True
        elif bt in ("path", "pathlike", "purepath"):
            return True
        else:
            # 自定义类型，放宽检查
            return True

    # 默认不兼容
    return False


def validate_all_cases(
        cases: List[Dict[str, Any]],
        funcs_map: Dict[Tuple[str, str], Dict[str, Any]],
        seen_ids: set,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    验证所有测试用例，返回 (有效用例, 错误用例及错误信息)。
    funcs_map: {(target_module, target_function): func_info}
    """
    valid: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for case in cases:
        tm = case.get("target_module", "")
        tf = case.get("target_function", "")
        func_info = funcs_map.get((tm, tf))
        if func_info is None:
            errors.append({
                "original_case": case,
                "errors": [f"找不到对应的被测函数 {tm}.{tf}"],
            })
            continue

        param_names = [p["name"] for p in func_info["params"]]
        type_hints = {p["name"]: p.get("type_hint") for p in func_info["params"]}

        case_errors = _validate_single_case(case, param_names, type_hints, seen_ids)
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
    all_funcs: List[Dict[str, Any]],
) -> str:
    """构建纠错 prompt。通过 all_funcs 查找参数列表和索引。"""
    # 构建 (module, func_name) → 全局索引的映射
    func_index_map: Dict[Tuple[str, str], int] = {}
    for idx, f in enumerate(all_funcs):
        key = (f["target_module"], f["name"])
        func_index_map[key] = idx

    error_descs: List[str] = []
    for ec in error_cases:
        oc = ec["original_case"]
        tf = oc.get("target_function", "?")
        tm = oc.get("target_module", "?")
        fi = func_index_map.get((tm, tf), -1)
        if fi >= 0:
            params = [p["name"] for p in all_funcs[fi]["params"]]
        else:
            params = []
        error_descs.append(
            f"### func_index={fi}: {tf} ({tm})\n"
            f"- 参数列表: {json.dumps(params, ensure_ascii=False)}\n"
            f"- 原 test_input: {oc.get('test_input', '')}\n"
            f"- 原 description: {oc.get('description', '')}\n"
            f"- 原 expected_behavior: {oc.get('expected_behavior', '')}\n"
            f"- 错误: {ec['errors']}"
        )

    prompt = f"""以下测试用例存在格式或类型错误，请修正。

{chr(10).join(error_descs)}

## 修正要求

输出修正后的 JSON 数组，每个元素字段：
- "func_index": 整数，与上面相同的 func_index
- "values": 参数值列表（顺序与参数列表一致，值为字符串）
- "description": 修正后的测试描述
- "expected_behavior": 修正后的期望行为

只输出 JSON 数组，不要额外文字。
"""
    return prompt


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def generate_ids(cases: List[Dict[str, Any]], start_counter: int = 0) -> int:
    """为测试用例生成唯一 ID，返回下一个可用计数器。"""
    counter = start_counter
    for case in cases:
        tm = case.get("target_module", "")
        abbr = _module_abbr(tm)
        cid = f"UT-{abbr}-{counter:03d}"  # noqa
        case["id"] = cid
        counter += 1
    return counter


def _find_project_root(search_root: Path) -> Path:
    """从 search_root 向上查找包含 'src' 目录的项目根目录，用于计算模块路径。
    
    确保模块路径始终包含完整层级（如 src.memory.factory），而非仅文件名（factory）。
    如果找不到包含 src 的目录，返回 search_root 本身。
    """
    current = search_root.resolve()
    # 先检查 search_root 本身
    if (current / "src").is_dir():
        return current
    # 向上查找父目录
    parent = current.parent
    while parent != current:  # 到达盘符根目录则停止
        if (parent / "src").is_dir():
            return parent
        current = parent
        parent = current.parent
    return search_root


def main() -> None:
    from datetime import datetime

    start_time = datetime.now()
    print(f"任务开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 先加载配置以获取默认值
    cfg = _load_global_config()
    args = parse_args()

    # 确定 root 路径
    if args.root is not None:
        root = Path(args.root).resolve()
    elif cfg is not None:
        root = Path(cfg.base_path.project_root).resolve()
    else:
        print("错误: 未指定 --root 且无法加载全局配置获取默认值")
        sys.exit(1)

    # 确定 output 路径（默认输出到 root/tests/ 目录）
    if args.output is not None:
        output_path = Path(args.output).resolve()
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = (root / "tests" / f"unit_test_cases_{timestamp}.json").resolve()

    error_output_path = output_path.with_suffix("")  # 去掉后缀后再加
    error_output_path = Path(str(error_output_path) + "_errors.json")
    if error_output_path == output_path:
        error_output_path = output_path.with_name(
            output_path.stem + "_errors" + output_path.suffix
        )

    if not root.is_dir():
        print(f"错误: 根目录不存在: {root}")
        sys.exit(1)

    # 加载配置（显示信息）
    print("[1/7] 加载配置...")
    if cfg is None:
        print("警告: 无法加载全局配置，将尝试使用环境变量。")
    client, model_name = _get_llm_client(cfg)
    print(f"  使用模型: {model_name}")
    print(f"  扫描 root: {root}")
    print(f"  output: {output_path}")

    # 收集文件（使用用户指定的 root 扫描）
    print("[2/7] 收集 Python 文件...")
    ignore_patterns = load_gitignore(root)
    py_files = collect_py_files(root, ignore_patterns)
    print(f"  找到 {len(py_files)} 个 .py 文件")

    # 确定模块路径计算的根目录（向上查找包含 src 的项目根目录）
    module_root = _find_project_root(root)
    if module_root != root:
        print(f"  模块路径计算根目录已调整为: {module_root}")

    # Jedi 分析（使用 module_root 计算模块路径）
    print("[3/7] 分析函数结构...")
    all_funcs: List[Dict[str, Any]] = []
    for fp in py_files:
        funcs = analyze_file_with_jedi(fp, module_root)
        if funcs:
            print(f"  {fp.relative_to(root)} -> {len(funcs)} 个函数")
        all_funcs.extend(funcs)
    print(f"  共提取 {len(all_funcs)} 个目标函数")

    if not all_funcs:
        print("没有找到可测试的函数，退出。")
        output_path.write_text("[]", encoding="utf-8")
        return

    # 构建 funcs_map 用于校验
    funcs_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for f in all_funcs:
        key = (f["target_module"], f["name"])
        funcs_map[key] = f

    # LLM 批量生成测试条目
    print("[4/7] 调用 LLM 生成测试用例...")
    batch_size = 5  # 每批最多 5 个函数
    all_raw_items: List[Dict[str, Any]] = []
    funcs_with_index: Dict[int, Dict[str, Any]] = {}  # 全局索引→函数信息
    global_func_index = 0
    for i in range(0, len(all_funcs), batch_size):
        batch = all_funcs[i: i + batch_size]  # noqa
        for local_i, f in enumerate(batch):
            funcs_with_index[global_func_index + local_i] = f
        batch_num = i // batch_size + 1
        func_names = [f"{f['target_module']}.{f['name']}" for f in batch]
        print(f"  处理第 {batch_num} 批 ({len(batch)} 个函数):")
        for fn in func_names:
            print(f"    - {fn}")
        prompt = _build_batch_prompt(batch)
        result = _call_llm(client, model_name, prompt, max_tokens=8192)
        if result:
            # LLM 返回的 func_index 是批内索引，映射到全局索引
            for item in result:
                if isinstance(item, dict) and "func_index" in item:
                    item["func_index"] = item["func_index"] + global_func_index
            all_raw_items.extend(result)
            print(f"    LLM 返回 {len(result)} 条")
        else:
            print(f"    本批生成失败，跳过")
        global_func_index += len(batch)

    if not all_raw_items:
        print("LLM 未能生成任何用例，退出。")
        output_path.write_text("[]", encoding="utf-8")
        return

    # 组装完整用例（target_module/target_function/test_input 由代码确定）
    all_raw_cases = _assemble_cases(all_raw_items, all_funcs)
    if not all_raw_cases:
        print("组装后无有效用例，退出。")
        output_path.write_text("[]", encoding="utf-8")
        return
    filtered_count = len(all_raw_items) - len(all_raw_cases)
    if filtered_count > 0:
        print(f"  LLM 返回 {len(all_raw_items)} 条，组装成功 {len(all_raw_cases)} 个用例（过滤 {filtered_count} 条格式不匹配）")
    else:
        print(f"  组装 {len(all_raw_cases)} 个用例")

    # 生成 ID
    print("[5/7] 生成用例 ID...")
    id_counter = generate_ids(all_raw_cases, start_counter=0)

    # 兜底检查与纠错
    print("[6/7] 兜底检查与纠错...")
    seen_ids: set = set()
    max_retries = 3

    valid_cases, error_cases = validate_all_cases(all_raw_cases, funcs_map, seen_ids)

    # 错误用例清理 _func_index 后保存（用集合去重，key 为 (module, function, test_input)）
    def _error_key(err: Dict[str, Any]) -> tuple:
        oc = err["original_case"]
        return (oc.get("target_module", ""), oc.get("target_function", ""), oc.get("test_input", ""))

    seen_error_keys: set = set()
    final_errors: List[Dict[str, Any]] = []
    for ec in error_cases:
        oc = ec["original_case"].copy()
        oc.pop("_func_index", None)
        err_entry = {"original_case": oc, "errors": ec["errors"]}
        ek = _error_key(err_entry)
        if ek not in seen_error_keys:
            seen_error_keys.add(ek)
            final_errors.append(err_entry)

    for retry in range(1, max_retries + 1):
        if not error_cases:
            break
        print(f"  第 {retry} 次纠错重试 ({len(error_cases)} 个错误)...")
        fix_prompt = _build_fix_prompt(error_cases, all_funcs)
        fixed = _call_llm(client, model_name, fix_prompt, max_tokens=8192)
        if not fixed:
            print("    纠错调用失败，保留原始错误")
            break
        # 将 LLM 纠错结果组装并重新验证
        fixed_cases = _assemble_cases(fixed, all_funcs)
        id_counter = generate_ids(fixed_cases, start_counter=id_counter)
        seen_ids_retry: set = seen_ids.copy()
        new_valid, new_errors = validate_all_cases(fixed_cases, funcs_map, seen_ids_retry)
        valid_cases.extend(new_valid)
        for case in new_valid:
            case.pop("_func_index", None)
        error_cases = new_errors
        for ec in error_cases:
            oc = ec["original_case"].copy()
            oc.pop("_func_index", None)
            err_entry = {"original_case": oc, "errors": ec["errors"]}
            ek = _error_key(err_entry)
            if ek not in seen_error_keys:
                seen_error_keys.add(ek)
                final_errors.append(err_entry)
        print(f"    修正后: {len(new_valid)} 个有效, {len(new_errors)} 个仍错误")
    print(f"  最终: {len(valid_cases)} 个有效, {len(final_errors)} 个错误")

    # 清理所有有效用例的 _func_index
    for case in valid_cases:
        case.pop("_func_index", None)

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
