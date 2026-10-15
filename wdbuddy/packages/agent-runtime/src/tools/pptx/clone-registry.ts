/**
 * 内置克隆模板注册表（模版库"克隆模板"分区数据源）
 *
 * 模板 pptx 存放于 packages/agent-runtime/templates/（随包分发）。
 * office.generate 的 template 参数支持三种取值：
 *   1. 内置模板 id（如 "red-blue-2026"）→ 免路径引用，模版库一键使用
 *   2. 内置模板名（如 "红蓝微立体·2026年终总结"）→ LLM 自然语言可直接命中
 *   3. 绝对路径（.pptx）→ 用户工作区里的任意第三方模板（原有能力）
 */
import * as path from 'node:path';
import * as fs from 'node:fs';
import { fileURLToPath } from 'node:url';

export interface CloneTemplateDef {
  /** 模板 id（template 参数直接引用） */
  id: string;
  /** 展示名（模版库卡片标题 / template 参数按名匹配） */
  name: string;
  /** 一句话风格描述 */
  vibe: string;
  /** 模板文件名（templates/ 目录下） */
  file: string;
  /** 适配场景标签 */
  tags: string[];
}

/** 内置模板清单（新增模板：pptx 入 templates/ + 在此登记即生效） */
export const CLONE_TEMPLATES: CloneTemplateDef[] = [
  {
    id: 'red-blue-2026',
    name: '红蓝微立体·2026年终总结',
    vibe: '红蓝双色微立体质感，含封面/目录/章节/三卡/四卡/时间线等 39 页版式',
    file: 'red-blue-2026.pptx',
    tags: ['年终总结', '商务汇报', '红蓝', '微立体'],
  },
];

/** templates/ 目录绝对路径（src 运行态与 dist 运行态通用） */
export function templatesDir(): string {
  // 本文件位于 src/tools/pptx/（运行态为 dist/tools/pptx/）→ 上溯三级到包根 templates/
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(here, '../../../templates');
}

/** 模板定义 → 绝对路径（文件不存在返回 null） */
export function resolveCloneTemplate(idOrNameOrPath: string): {
  /** 命中的模板（内置注册项；路径引用时为 null） */
  def: CloneTemplateDef | null;
  /** 模板 pptx 绝对路径；解析失败为 null */
  path: string | null;
} {
  const raw = idOrNameOrPath.trim();
  if (!raw) return { def: null, path: null };

  // 1) 内置 id / 展示名精确匹配
  const hit = CLONE_TEMPLATES.find(
    (t) => t.id === raw.toLowerCase() || t.name === raw
  );
  if (hit) {
    const p = path.join(templatesDir(), hit.file);
    return { def: hit, path: fs.existsSync(p) ? p : null };
  }
  // 2) 展示名模糊包含（"红蓝" 命中 "红蓝微立体·2026年终总结"）
  const fuzzy = CLONE_TEMPLATES.find(
    (t) => t.name.includes(raw) || t.tags.some((tag) => tag.includes(raw))
  );
  if (fuzzy) {
    const p = path.join(templatesDir(), fuzzy.file);
    return { def: fuzzy, path: fs.existsSync(p) ? p : null };
  }
  // 3) 绝对路径直接透传
  if (fs.existsSync(raw)) return { def: null, path: raw };
  return { def: null, path: null };
}

/** 模版库预览元数据（不含 HTML，预览由 templates-preview 单独构建） */
export function cloneTemplateList(): Array<{
  id: string;
  name: string;
  vibe: string;
  tags: string[];
  file: string;
  exists: boolean;
}> {
  return CLONE_TEMPLATES.map((t) => ({
    ...t,
    exists: fs.existsSync(path.join(templatesDir(), t.file)),
  }));
}
