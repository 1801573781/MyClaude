"""Skill Loader - 渐进式加载 Skill 的三层实现 (L1/L2/L3)"""

import re
from pathlib import Path
from typing import Dict, List, Optional


class SkillLoader:
    """Skill 加载器，支持三层渐进式加载与缓存。

    L1 - 元数据层：扫描所有 Skill 的 name + description
    L2 - 完整指令层：加载指定 Skill 的完整操作手册
    L3 - 资源层：按需读取 Skill 目录下的资源文件
    """


    def __init__(self, skill_root: Path):
        self._skill_root = Path(skill_root)
        self._metadata_cache: Optional[List[Dict[str, str]]] = None
        self._full_content_cache: Dict[str, Optional[str]] = {}


    # ========== L1: 元数据扫描 ==========


    def _scan(self) -> List[Dict[str, str]]:
        """扫描 skill/ 目录，提取每个 SKILL.md 的 frontmatter 元数据，并缓存结果。"""
        if self._metadata_cache is not None:
            return self._metadata_cache

        result: List[Dict[str, str]] = []
        if not self._skill_root.exists() or not self._skill_root.is_dir():
            self._metadata_cache = result
            return result

        for skill_dir in sorted(self._skill_root.iterdir()):
            if not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists() or not skill_md.is_file():
                continue

            try:
                content = skill_md.read_text(encoding="utf-8")
                metadata = self._parse_frontmatter(content)
                if metadata and "name" in metadata:
                    result.append({
                        "name": metadata["name"],
                        "description": metadata.get("description") or f"Skill: {metadata['name']}"
                    })
            except Exception:
                continue

        self._metadata_cache = result
        return result


    @staticmethod
    def _parse_frontmatter(content: str) -> Optional[Dict[str, str]]:
        """解析 YAML frontmatter (--- ... ---)，返回键值对字典。

        不使用 pyyaml，仅支持简单的 key: value 格式。
        纯函数，无 I/O 副作用，便于单元测试。
        """
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if not match:
            return None

        yaml_text = match.group(1)
        result: Dict[str, str] = {}
        for line in yaml_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            # 精确匹配 key: value，兼容行内注释和引号
            kv_match = re.match(
                r'^(\w[\w\s]*?)\s*:\s*(.+?)(?:\s*#.*)?$', line
            )
            if kv_match:
                key = kv_match.group(1).strip()
                value = kv_match.group(2).strip()
                # 去除引号
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                result[key] = value

        return result if result else None


    # ========== L2: 完整技能正文加载 ==========


    def load_full_skill(self, skill_name: str) -> Optional[str]:
        """L2: 加载指定 Skill 的完整 SKILL.md 内容（去掉 frontmatter），并缓存。

        失败或不存在时返回 None。
        """
        if skill_name in self._full_content_cache:
            return self._full_content_cache[skill_name]

        skill_md = self._skill_root / skill_name / "SKILL.md"
        if not skill_md.exists() or not skill_md.is_file():
            self._full_content_cache[skill_name] = None
            return None

        try:
            content = skill_md.read_text(encoding="utf-8")
            body = re.sub(
                r'^---\s*\n.*?\n---\s*\n', '', content, count=1, flags=re.DOTALL
            )
            self._full_content_cache[skill_name] = body.strip()
        except Exception:
            self._full_content_cache[skill_name] = None

        return self._full_content_cache[skill_name]


    # ========== L3: 脚本/模板内容按需加载 ==========


    def load_resource(self, skill_name: str, relative_path: str) -> Optional[str]:
        """L3: 读取 Skill 目录下的资源文件，返回 UTF-8 文本内容。

        文件不存在、越权访问或解码失败时返回 None。
        """
        try:
            skill_dir = (self._skill_root / skill_name).resolve()
            resource_path = (skill_dir / relative_path).resolve()
            # 安全检查：防止路径遍历攻击（Python 3.9+）
            if not resource_path.is_relative_to(skill_dir):
                return None
        except Exception:
            return None

        if not resource_path.exists() or not resource_path.is_file():
            return None

        try:
            return resource_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None


    # ========== 公开接口 & 辅助 ==========


    def get_metadata(self) -> List[Dict[str, str]]:
        """L1: 返回所有 Skill 的元数据列表 [{"name": ..., "description": ...}, ...]"""
        return self._scan()


    def format_skills_prompt(self) -> str:
        """基于已扫描的元数据，生成系统提示词用的 Markdown 清单。

        无 Skill 时返回空字符串。
        """
        metadata_list = self._scan()
        if not metadata_list:
            return ""

        lines = [
            "## Installed Skills (L1 Metadata)",
            """当用户请求匹配以下任一技能时，你**必须首先调用 `<use_skill name="技能名"/>` 加载完整指令，
            然后严格按照返回的内容执行。禁止凭记忆或自行发挥。
            如果技能加载失败（系统返回包含 `[CRITICAL ERROR]` 的错误信息），你必须立即输出 `<done>` 并停止，不得以任何方式继续处理。""",
        ]
        for meta in metadata_list:
            lines.append(f"- **{meta['name']}**: {meta['description']}")
        lines.append("")
        lines.append("*(完整技能指令在匹配后自动加载)*")

        return "\n".join(lines)


# --- 单例 ---

_skill_loader: Optional[SkillLoader] = None


def get_skill_loader() -> SkillLoader:
    """全局获取 SkillLoader 单例，确保整个进程共用一个实例。"""
    global _skill_loader
    if _skill_loader is None:
        from .config_loader import global_cfg

        skill_root = Path(global_cfg.base_path.project_root) / "skill"
        _skill_loader = SkillLoader(skill_root)
    return _skill_loader
