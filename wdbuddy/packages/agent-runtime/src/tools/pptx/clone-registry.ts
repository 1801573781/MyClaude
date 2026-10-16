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
  /** 绝对路径（用户模板：直接指向 pptx，绕过 templates/ 相对解析） */
  absPath?: string;
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
  {
    id: 'deep-space-blue',
    name: '深空蓝调·科技数据汇报',
    vibe: '深藏青蓝底 + 粉紫/青双色点缀，圆环与竖条装饰，含封面/目录/章节/三卡/四卡/时间线/两栏/列表/结尾 9 版式',
    file: 'deep-space-blue.pptx',
    tags: ['科技', '数据汇报', '深蓝', '商务', '年终总结', '项目汇报'],
  },
];

/** templates/ 目录绝对路径（src 运行态与 dist 运行态通用） */
export function templatesDir(): string {
  // 本文件位于 src/tools/pptx/（运行态为 dist/tools/pptx/）→ 上溯三级到包根 templates/
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(here, '../../../templates');
}

/** 用户自定义模板目录（template.from_image 产物注册位置）。
 *  独立于包内 templates/（随安装分发、只读）——用户模板在用户数据目录，
 *  应用升级不丢失、多任务共享、模版库动态可见 */
export function userTemplatesDir(): string {
  const base =
    process.env['APPDATA'] ??
    process.env['XDG_CONFIG_HOME'] ??
    path.join(process.env['USERPROFILE'] ?? process.env['HOME'] ?? process.cwd(), '.config');
  return path.join(base, 'wdbuddy', 'templates');
}

/** 注册用户模板：pptx 写入用户目录 + sidecar 元数据（name/vibe/tags） */
export function registerUserTemplate(opts: {
  pptxPath: string;
  /** 模板名（同时生成文件 id） */
  name: string;
  vibe: string;
  tags: string[];
  /** 目标 .pptx 内容（已渲染的 Buffer；与 pptxPath 二选一） */
  buffer?: Buffer;
}): { ok: boolean; id: string; file: string; error?: string } {
  const dir = userTemplatesDir();
  const id = opts.name
    .replace(/[<>:"/\\|?*·]/g, '-')
    .replace(/\s+/g, '-')
    .toLowerCase()
    .slice(0, 40) || 'user-template';
  const file = path.join(dir, `${id}.pptx`);
  try {
    fs.mkdirSync(dir, { recursive: true });
    if (opts.buffer) {
      fs.writeFileSync(file, opts.buffer);
    } else {
      fs.copyFileSync(opts.pptxPath, file);
    }
    fs.writeFileSync(
      path.join(dir, `${id}.json`),
      JSON.stringify({ name: opts.name, vibe: opts.vibe, tags: opts.tags }, null, 2),
      'utf8'
    );
    return { ok: true, id: `user-${id}`, file };
  } catch (err) {
    return { ok: false, id: `user-${id}`, file, error: String(err) };
  }
}

/** 扫描用户模板目录 → CloneTemplateDef[]（sidecar 元数据优先，mtime 降序） */
export function scanUserTemplates(): CloneTemplateDef[] {
  const dir = userTemplatesDir();
  if (!fs.existsSync(dir)) return [];
  const out: CloneTemplateDef[] = [];
  const seen = new Set<string>();
  for (const f of fs.readdirSync(dir)) {
    if (!f.toLowerCase().endsWith('.pptx')) continue;
    const p = path.join(dir, f);
    let st: fs.Stats;
    try {
      st = fs.statSync(p);
    } catch {
      continue;
    }
    if (!st.isFile() || st.size < 1024) continue;
    const baseId = f.replace(/\.pptx$/i, '');
    const id = `user-${baseId.toLowerCase()}`;
    if (seen.has(id)) continue;
    seen.add(id);
    // sidecar 元数据（注册时写入的展示名/风格/标签）
    let meta: { name?: string; vibe?: string; tags?: string[] } = {};
    try {
      meta = JSON.parse(fs.readFileSync(path.join(dir, `${baseId}.json`), 'utf8')) as typeof meta;
    } catch {
      /* 无 sidecar 用文件名兜底 */
    }
    out.push({
      id,
      name: meta.name ?? baseId.replace(/[-_]/g, ' '),
      vibe: meta.vibe ?? '用户图片生成的自定义模板',
      file: f,
      tags: meta.tags?.length ? meta.tags : ['自定义', '图片生成'],
      absPath: p,
    });
  }
  out.sort((a, b) => {
    const ma = fs.statSync(a.absPath!).mtimeMs;
    const mb = fs.statSync(b.absPath!).mtimeMs;
    return mb - ma;
  });
  return out;
}

/** 全部模板：内置 + 用户（模版库数据源） */
export function allTemplates(): CloneTemplateDef[] {
  return [...CLONE_TEMPLATES, ...scanUserTemplates()];
}

/** 模板定义 → 绝对路径（文件不存在返回 null） */
export function resolveCloneTemplate(idOrNameOrPath: string): {
  /** 命中的模板（内置/用户注册项；路径引用时为 null） */
  def: CloneTemplateDef | null;
  /** 模板 pptx 绝对路径；解析失败为 null */
  path: string | null;
} {
  const raw = idOrNameOrPath.trim();
  if (!raw) return { def: null, path: null };
  const pool = allTemplates();

  // 1) id / 展示名精确匹配（含用户模板）
  const hit = pool.find(
    (t) => t.id === raw.toLowerCase() || t.name === raw
  );
  if (hit) {
    const p = hit.absPath ?? path.join(templatesDir(), hit.file);
    return { def: hit, path: fs.existsSync(p) ? p : null };
  }
  // 2) 展示名/标签模糊包含（"红蓝" 命中 "红蓝微立体·2026年终总结"）
  const fuzzy = pool.find(
    (t) => t.name.includes(raw) || t.tags.some((tag) => tag.includes(raw))
  );
  if (fuzzy) {
    const p = fuzzy.absPath ?? path.join(templatesDir(), fuzzy.file);
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
  return allTemplates().map((t) => ({
    id: t.id,
    name: t.name,
    vibe: t.vibe,
    tags: t.tags,
    file: t.absPath ?? path.join(templatesDir(), t.file),
    exists: fs.existsSync(t.absPath ?? path.join(templatesDir(), t.file)),
  }));
}
