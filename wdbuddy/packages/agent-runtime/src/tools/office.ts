/**
 * office.generate — 文档生成工具（双引擎：pptx / html）
 * pptx：pptxgenjs 原生演示文稿（可编辑，14 版式 + 10 主题）
 * html：单文件 HTML 幻灯片（现代 CSS 表现力上限，浏览器直接放映）
 */
import * as path from 'node:path';
import * as fs from 'node:fs';
import * as os from 'node:os';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import type { AgentTool, ToolContext, ToolResult } from './registry.js';
import { makeArtifact } from './registry.js';
import { getTheme, pickThemeByVibe } from './pptx/themes.js';
import { renderSlidesTo, type SlideSpec } from './pptx/pptx-engine.js';
import { buildHtmlDeck } from './pptx/html-engine.js';
import { mdToSlides } from './pptx/md-to-slides.js';
import { scanTemplate, planClone, executeClone, type ClonePlan } from './pptx/template-clone.js';
import { resolveCloneTemplate, cloneTemplateList } from './pptx/clone-registry.js';

const nodeRequire = createRequire(import.meta.url);

function sha256(data: string | Buffer): string {
  return createHash('sha256').update(data).digest('hex');
}

function argString(args: Record<string, unknown>, key: string): string {
  const v = args[key];
  return typeof v === 'string' ? v : '';
}

function guessMime(p: string): string {
  if (p.toLowerCase().endsWith('.html')) return 'text/html';
  return 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
}

/** 生成幻灯片产物（双引擎共享核心）。
 *  base 不含扩展名的输出基路径；formats 决定产出格式：
 *  - ['html']  仅网页幻灯片；['pptx'] 仅 PowerPoint
 *  - ['html','pptx'] 双出（显式要求时才用）
 *  pptx 失败时降级仅 html（不阻断交付）。
 *  @returns 写盘的产物列表（含 artifact 构造所需的 path/buf）；写盘失败抛错。 */
export async function generateDeckOutputs(
  base: string,
  title: string,
  themeId: string,
  slides: SlideSpec[],
  fsWrite: (p: string, data: Buffer) => void,
  formats: Array<'html' | 'pptx'> = ['html', 'pptx']
): Promise<Array<{ path: string; buf: Buffer; mime: string }>> {
  const theme =
    themeId === 'auto'
      ? pickThemeByVibe(title + ' ' + JSON.stringify(slides).slice(0, 500))
      : getTheme(themeId);

  const outputs: Array<{ path: string; buf: Buffer; mime: string }> = [];

  // HTML 引擎（浏览器放映）
  if (formats.includes('html')) {
    const htmlBuf = Buffer.from(buildHtmlDeck(title, slides, theme), 'utf8');
    const htmlPath = `${base}.html`;
    fsWrite(htmlPath, htmlBuf);
    outputs.push({ path: htmlPath, buf: htmlBuf, mime: 'text/html' });
  }

  // PPTX 引擎（可编辑 PowerPoint；失败降级 html，不阻断交付）
  if (formats.includes('pptx')) {
    try {
      const mod = nodeRequire('pptxgenjs') as { default?: new () => any } & Record<string, any>;
      const PptxGenJS = mod.default ?? (mod as unknown as new () => any);
      const pptx = new PptxGenJS();
      pptx.layout = 'LAYOUT_WIDE'; // 13.33×7.5 英寸标准宽屏（对齐 PowerPoint 默认）
      // 主题级默认字体（双保险：未显式指定 fontFace 的文本也走微软雅黑）
      pptx.theme = { headFontFace: 'Microsoft YaHei', bodyFontFace: 'Microsoft YaHei' };
      pptx.title = title;
      renderSlidesTo(pptx, slides, theme);
      const pptxBuf0 = (await (pptx as any).write({ outputType: 'nodebuffer' })) as Buffer;
      // 字体后处理：补 <a:ea>（中文路径）+ 主题空 ea 填充——修复图表轴
      // 标签等中文字符回退等线的结构性缺陷
      const pptxBuf = await postprocessPptxFonts(pptxBuf0);
      const pptxPath = `${base}.pptx`;
      fsWrite(pptxPath, pptxBuf);
      outputs.push({
        path: pptxPath,
        buf: pptxBuf,
        mime: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      });
    } catch {
      // pptxgenjs 异常不阻断；若 html 也未要求则补产出 html 保底
      if (!formats.includes('html')) {
        const htmlBuf = Buffer.from(buildHtmlDeck(title, slides, theme), 'utf8');
        fsWrite(`${base}.html`, htmlBuf);
        outputs.push({ path: `${base}.html`, buf: htmlBuf, mime: 'text/html' });
      }
    }
  }
  return outputs;
}

/** 路径 → 双格式输出基路径（去扩展名） */
function deckBase(p: string): string {
  return p.replace(/\.(html?|pptx)$/i, '');
}

/** XML 级字体补丁（导出供测试）：给 latin 字体标签补 East-Asian 定义。
 *  根因：pptxgenjs 的 fontFace 只写 latin 标签，中文字符按 ea 标签查找，
 *  缺失时 PowerPoint 回退主题默认（等线/DengXian）——图表轴标签
 *  显示等线的元凶。已有 ea 标签的不重复注入。
 *  实现用 split-join 而非正则：标签串含引号与斜杠，正则转义易踩坑 */
export function injectEaFont(xml: string, font = 'Microsoft YaHei'): string {
  const selfClose = '<a:latin typeface="' + font + '"/>';
  const eaTag = '<a:ea typeface="' + font + '"/>';
  const pair = selfClose + eaTag;
  return xml
    .split(pair)
    .join('\u0000PAIR\u0000') // 已有 latin+ea 完整对：先保护
    .split(selfClose)
    .map((seg, i) => (i === 0 ? seg : pair + seg))
    .join('')
    .split('\u0000PAIR\u0000')
    .join(pair);
}

/** theme XML 补丁：Office 默认主题 minor 与 major 的 ea 字体为空串时，
 *  中文一律回退系统等线。填充为指定字体（全局兜底，含所有主题变体） */
export function patchThemeEa(xml: string, font = 'Microsoft YaHei'): string {
  const emptyEa = '<a:ea typeface=""/>';
  const filledEa = '<a:ea typeface="' + font + '"/>';
  return xml.split(emptyEa).join(filledEa);
}

/** pptx 二进制后处理：解压 → 全部 slide/chart/theme XML 注入 ea 字体 → 重打包。
 *  pptxgenjs 依赖树内含 jszip，从其模块路径解析加载；失败原样返回（降级不阻断） */
async function postprocessPptxFonts(pptxBuf: Buffer): Promise<Buffer> {
  try {
    const { createRequire: cr } = await import('node:module');
    const reqFromPptx = cr(nodeRequire.resolve('pptxgenjs'));
    const JSZip = reqFromPptx('jszip') as { loadAsync: (b: Buffer) => Promise<any> };
    const zip = await JSZip.loadAsync(pptxBuf);
    let patched = 0;
    for (const name of Object.keys(zip.files)) {
      const isXml =
        /^ppt\/(slides|slideMasters|notesSlides|charts)\/[^/]+\.xml$/.test(name) ||
        /^ppt\/theme\/theme\d+\.xml$/.test(name);
      if (!isXml) continue;
      let xml: string = await zip.files[name]!.async('string');
      const before = xml;
      xml = injectEaFont(xml);
      if (/^ppt\/theme\//.test(name)) xml = patchThemeEa(xml);
      if (xml !== before) {
        zip.file(name, xml);
        patched++;
      }
    }
    if (patched === 0) return pptxBuf;
    return (await zip.generateAsync({ type: 'nodebuffer' })) as Buffer;
  } catch {
    return pptxBuf;
  }
}

export const officeGenerateTool: AgentTool = {
  name: 'office.generate',
  description:
    '生成演示文稿：默认按 path 扩展名输出单一格式（.pptx=可编辑 PowerPoint 13.33×7.5 宽屏 / .html=浏览器放映）；传 formats:"html,pptx" 可双格式。10 主题 16 版式，每页自动讲者备注；传 template 进入克隆模式——以模板页为画布视觉 100% 复刻、仅替换文字：template 可传内置模板 id（如 red-blue-2026，内置' + cloneTemplateList().length + '套）或 .pptx 绝对路径（内容经 fromMarkdown 或 slides 提供，输出必须 .pptx）',
  sensitivity: 'write',
  async execute(args, ctx: ToolContext): Promise<ToolResult> {
    const p = argString(args, 'path');
    const title = argString(args, 'title') || 'WDBuddy 演示文稿';
    const themeId = argString(args, 'theme') || 'auto';
    // fromMarkdown：内容稿 md 路径（此前已落盘的逐页文案）→ 程序化转 slides。
    // 反截断治本方案：LLM 只需输出 ~100 字符的短 JSON，物理上不可能被输出上限
    // 截断；长内容由已写好的 md 承载，转换由确定性代码完成
    const fromMd = argString(args, 'fromMarkdown');

    if (!p) return { ok: false, output: '缺少 path 参数（输出路径，.html 或 .pptx）', artifacts: [] };
    const isHtml = p.toLowerCase().endsWith('.html');
    const isPptx = p.toLowerCase().endsWith('.pptx');
    if (!isHtml && !isPptx) {
      return { ok: false, output: 'path 必须以 .html 或 .pptx 结尾（会自动同时生成两种格式）', artifacts: [] };
    }

    let slides: SlideSpec[];
    if (fromMd) {
      let md = '';
      try {
        md = ctx.fsGuard.readFile(fromMd);
      } catch (err) {
        return { ok: false, output: `读取内容稿 ${fromMd} 失败：${String(err)}`, artifacts: [] };
      }
      slides = mdToSlides(md, title);
    } else {
      slides = Array.isArray(args['slides']) ? (args['slides'] as SlideSpec[]) : [];
      if (slides.length === 0) {
        return { ok: false, output: '缺少 slides 参数（幻灯片数组）；或改用 fromMarkdown 传内容稿路径', artifacts: [] };
      }
    }

    // ── 模板克隆模式（路线B：第三方模板视觉 100% 复刻，仅替换文字）──
    const templateArg = argString(args, 'template');
    if (templateArg) {
      if (!isPptx) {
        return { ok: false, output: 'template 克隆模式仅支持输出 .pptx（模板视觉无法复刻为 html），请把 path 改为 .pptx 结尾', artifacts: [] };
      }
      // template 三态解析：内置 id / 内置名 / 绝对路径
      const resolved = resolveCloneTemplate(templateArg);
      if (!resolved.path) {
        const list = cloneTemplateList()
          .map((t) => `  - ${t.id}（${t.name}）`)
          .join('\n');
        return {
          ok: false,
          output: `模板「${templateArg}」未找到。可传内置模板 id（${templateArg === 'red-blue-2026' ? '已注册但文件缺失' : '见下'}）、模板名或 .pptx 绝对路径。内置模板：\n${list}`,
          artifacts: [],
        };
      }
      const templatePath = resolved.path;
      const scan = scanTemplate(templatePath);
      if (!scan.ok || scan.pages.length === 0) {
        return { ok: false, output: `模板扫描失败（${templatePath}）：${scan.error ?? '模板内无可读文本页'}。请确认是有效 .pptx，且本机 python + python-pptx 可用`, artifacts: [] };
      }
      const usedPages = new Set<number>();
      const plans: Array<ClonePlan> = [];
      for (const s of slides) {
        const plan = planClone(s, scan.pages, usedPages);
        if (plan) plans.push(plan);
      }
      if (plans.length === 0) {
        return { ok: false, output: '克隆计划生成失败：内容稿无可映射到模板页的幻灯片', artifacts: [] };
      }
      const tmpOut = path.join(os.tmpdir(), `wd_clone_out_${Date.now()}.pptx`);
      const res = executeClone(templatePath, tmpOut, plans);
      if (!res.ok) {
        return { ok: false, output: `模板克隆执行失败（python 侧）：${res.output}`, artifacts: [] };
      }
      let buf: Buffer;
      try {
        buf = fs.readFileSync(tmpOut);
      } finally {
        try { fs.unlinkSync(tmpOut); } catch { /* */ }
      }
      const outPath = `${deckBase(p)}.pptx`;
      try {
        ctx.fsGuard.writeFile(outPath, buf);
      } catch (err) {
        return { ok: false, output: `写入 ${outPath} 失败：${String(err)}`, artifacts: [] };
      }
      const art = makeArtifact(ctx.taskId, 'file', outPath, 'application/vnd.openxmlformats-officedocument.presentationml.presentation', sha256(buf));
      ctx.onArtifact?.(art);
      const kinds = [...new Set(scan.pages.filter((pg) => usedPages.has(pg.page)).map((pg) => pg.kind))];
      return {
        ok: true,
        output: [
          `已按模板克隆生成 ${plans.length} 页幻灯片（模板 ${scan.pages.length} 页 → 输出 ${plans.length} 页，视觉 100% 继承模板${resolved.def ? `「${resolved.def.name}」` : ''}）：`,
          `  📄 ${outPath}（${buf.length} 字节，可用 PowerPoint/WPS 打开编辑）`,
          `  使用模板页：${[...usedPages].sort((a, b) => a - b).join(', ')}；版式：${kinds.join(' / ')}`,
        ].join('\n'),
        artifacts: [art],
      };
    }

    // formats：默认按 path 扩展名单格式输出（.pptx → 仅 pptx；.html → 仅 html）；
    // 显式传 formats 数组（"html,pptx" / ["html","pptx"]）才双出
    let formats: Array<'html' | 'pptx'> = p.toLowerCase().endsWith('.pptx')
      ? ['pptx']
      : ['html'];
    if (args['formats']) {
      const raw = String(args['formats']);
      const want = raw
        .split(/[,，\s]+/)
        .map((s) => s.trim().toLowerCase())
        .filter((s): s is 'html' | 'pptx' => s === 'html' || s === 'pptx');
      if (want.length > 0) formats = want;
    }

    const base = deckBase(p);
    let outputs: Array<{ path: string; buf: Buffer; mime: string }>;
    try {
      outputs = await generateDeckOutputs(base, title, themeId, slides, (fp, data) =>
        ctx.fsGuard.writeFile(fp, data)
      , formats);
    } catch (err) {
      return { ok: false, output: `写入 ${base}.${formats.join('/')} 失败：${String(err)}`, artifacts: [] };
    }

    const arts = outputs.map((o) =>
      makeArtifact(ctx.taskId, 'file', o.path, o.mime, sha256(o.buf))
    );
    for (const a of arts) ctx.onArtifact?.(a);
    const pptxOut = outputs.find((o) => o.path.endsWith('.pptx'));
    const lines = [
      `已生成 ${slides.length} 页幻灯片（主题：${themeId === 'auto' ? '自动匹配' : themeId}${fromMd ? `，内容来自 ${fromMd}` : ''}，格式：${formats.join(' + ')}）：`,
      ...outputs.map((o) => `  📄 ${o.path}（${o.buf.length} 字节${o.path.endsWith('.html') ? '，双击浏览器放映，←→ 翻页，F 全屏' : '，可用 PowerPoint/WPS 打开编辑'}）`),
    ];
    if (!pptxOut && formats.includes('pptx')) {
      lines.push('  ⚠ .pptx 生成失败（引擎异常），已交付 .html 版本');
    }
    return { ok: true, output: lines.join('\n'), artifacts: arts };
  },
};
