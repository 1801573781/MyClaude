/**
 * office.generate — 文档生成工具（双引擎：pptx / html）
 * pptx：pptxgenjs 原生演示文稿（可编辑，14 版式 + 10 主题）
 * html：单文件 HTML 幻灯片（现代 CSS 表现力上限，浏览器直接放映）
 */
import * as path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import type { AgentTool, ToolContext, ToolResult } from './registry.js';
import { makeArtifact } from './registry.js';
import { getTheme, pickThemeByVibe } from './pptx/themes.js';
import { renderSlidesTo, type SlideSpec } from './pptx/pptx-engine.js';
import { buildHtmlDeck } from './pptx/html-engine.js';

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

export const officeGenerateTool: AgentTool = {
  name: 'office.generate',
  description:
    '生成演示文稿：.pptx（可编辑 PowerPoint）或 .html（浏览器放映，视觉更现代）。支持 10 主题与 14 版式',
  sensitivity: 'write',
  async execute(args, ctx: ToolContext): Promise<ToolResult> {
    const p = argString(args, 'path');
    const title = argString(args, 'title') || 'WDBuddy 演示文稿';
    const themeId = argString(args, 'theme') || 'auto';
    const slides = Array.isArray(args['slides']) ? (args['slides'] as SlideSpec[]) : [];

    if (!p) return { ok: false, output: '缺少 path 参数（输出路径，.pptx 或 .html）', artifacts: [] };
    const isHtml = p.toLowerCase().endsWith('.html');
    const isPptx = p.toLowerCase().endsWith('.pptx');
    if (!isHtml && !isPptx) {
      return { ok: false, output: 'path 必须以 .pptx 或 .html 结尾（html=网页幻灯片，pptx=PowerPoint）', artifacts: [] };
    }
    if (slides.length === 0) {
      return { ok: false, output: '缺少 slides 参数（幻灯片数组）', artifacts: [] };
    }

    const theme = themeId === 'auto' ? pickThemeByVibe(title + ' ' + JSON.stringify(slides).slice(0, 500)) : getTheme(themeId);

    let buf: Buffer;
    if (isPptx) {
      const mod = nodeRequire('pptxgenjs') as { default?: new () => any } & Record<string, any>;
      const PptxGenJS = mod.default ?? (mod as unknown as new () => any);
      const pptx = new PptxGenJS();
      pptx.layout = 'LAYOUT_16x9';
      pptx.title = title;
      renderSlidesTo(pptx, slides, theme);
      buf = (await (pptx as any).write({ outputType: 'nodebuffer' })) as Buffer;
    } else {
      buf = Buffer.from(buildHtmlDeck(title, slides, theme), 'utf8');
    }

    try {
      ctx.fsGuard.writeFile(p, buf);
    } catch (err) {
      return { ok: false, output: `写入 ${p} 失败：${String(err)}`, artifacts: [] };
    }

    const art = makeArtifact(ctx.taskId, 'file', p, guessMime(p), sha256(buf));
    ctx.onArtifact?.(art);
    return {
      ok: true,
      output: `已生成${isHtml ? ' HTML 幻灯片' : ' PPT'}：${p}（${slides.length} 页，主题：${theme.name}，${buf.length} 字节）${isHtml ? '——双击用浏览器打开，←→ 键翻页，F 全屏' : ''}`,
      artifacts: [art],
    };
  },
};
