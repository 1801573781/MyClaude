/**
 * office.generate — 文档生成工具（pptxgenjs 纯 JS 实现，无需 Python）
 * 支持通过结构化 slides JSON 直接生成 .pptx（毫秒级，替代 shell + python-pptx 方案）
 */
import * as path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import type { AgentTool, ToolContext, ToolResult } from './registry.js';
import { makeArtifact } from './registry.js';

/**
 * pptxgenjs 是 CJS 包。生产环境 Runtime 由 Electron 以 ELECTRON_RUN_AS_NODE 模式
 * 拉起，该模式下动态 import() 解析裸包名会报
 * "Cannot use import statement outside a module"（纯 node 正常、Electron 异常）。
 * 改用 createRequire 走 CJS require 链加载，两种环境均稳定。
 */
const nodeRequire = createRequire(import.meta.url);

function sha256(data: string | Buffer): string {
  return createHash('sha256').update(data).digest('hex');
}

function argString(args: Record<string, unknown>, key: string): string {
  const v = args[key];
  return typeof v === 'string' ? v : '';
}

interface SlideSpec {
  layout?: string;
  title?: string;
  content?: string[] | string;
  bullets?: string[];
  notes?: string;
  background?: string;
  /** 图表页：layout 设为 "chart" 时启用 */
  chart?: {
    type: 'bar' | 'pie' | 'line';
    /** 图表标题（默认取页 title） */
    chartTitle?: string;
    /** 数据系列：[{name, labels, values}] */
    series?: Array<{ name?: string; labels: string[]; values: number[] }>;
  };
}

/** 生成 PPTX：args = { path, title, slides: [{layout, title, bullets/content, notes}] } */
export const officeGenerateTool: AgentTool = {
  name: 'office.generate',
  description:
    '生成 Office 文档（当前支持 .pptx）。传入结构化 slides 数组直接产出 PowerPoint 文件，无需 Python 环境',
  sensitivity: 'write',
  async execute(args, ctx: ToolContext): Promise<ToolResult> {
    const p = argString(args, 'path');
    const title = argString(args, 'title') || 'WDBuddy 演示文稿';
    const slides = Array.isArray(args['slides']) ? (args['slides'] as SlideSpec[]) : [];
    if (!p) return { ok: false, output: '缺少 path 参数（.pptx 输出路径）', artifacts: [] };
    if (!p.toLowerCase().endsWith('.pptx')) {
      return { ok: false, output: 'path 必须以 .pptx 结尾', artifacts: [] };
    }
    if (slides.length === 0) {
      return { ok: false, output: '缺少 slides 参数（幻灯片数组，每项 {title, bullets}）', artifacts: [] };
    }

    // CJS require 加载（Electron RUN_AS_NODE 下动态 import 不兼容，见文件头注释）
    const mod = nodeRequire('pptxgenjs') as { default?: new () => any } & Record<string, any>;
    const PptxGenJS = mod.default ?? (mod as unknown as new () => any);
    const pptx = new PptxGenJS();
    pptx.layout = 'LAYOUT_16x9';
    pptx.title = title;

    // 主题色（可被 task 偏好覆盖，暂用商务蓝）
    const THEME = { primary: '2F5496', accent: '4472C4', text: '333333', light: 'F2F6FC' };

    for (let i = 0; i < slides.length; i++) {
      const spec = slides[i] ?? {};
      const slide = pptx.addSlide();
      // 背景色（封面等特殊页可指定）
      if (spec.background) {
        slide.background = { color: spec.background.replace('#', '') };
      }
      const layout = spec.layout ?? 'content';
      if (layout === 'cover') {
        // 封面：大标题居中
        slide.addText(spec.title ?? title, {
          x: 0.5, y: 2.2, w: 9, h: 1.2,
          fontSize: 40, bold: true, color: 'FFFFFF', align: 'center',
        });
        if (spec.bullets?.length) {
          slide.addText(spec.bullets.join('\n'), {
            x: 0.5, y: 3.6, w: 9, h: 0.8,
            fontSize: 18, color: 'D9E2F3', align: 'center',
          });
        }
        slide.background = { color: THEME.primary };
      } else if (layout === 'section') {
        // 章节页
        slide.addText(String(i), {
          x: 0.5, y: 1.6, w: 9, h: 1.6,
          fontSize: 60, bold: true, color: THEME.accent, align: 'center',
        });
        slide.addText(spec.title ?? '', {
          x: 0.5, y: 3.2, w: 9, h: 0.9,
          fontSize: 28, bold: true, color: THEME.text, align: 'center',
        });
      } else if (layout === 'chart' && spec.chart && Array.isArray(spec.chart.series) && spec.chart.series.length > 0) {
        // 图表页：pptxgenjs 原生 addChart（柱/饼/折线，可编辑的 Office 图表对象）
        slide.addText(spec.title ?? '数据图表', {
          x: 0.5, y: 0.35, w: 9, h: 0.7,
          fontSize: 24, bold: true, color: THEME.primary,
        });
        slide.addShape('rect', {
          x: 0.5, y: 1.05, w: 9, h: 0.03, fill: { color: THEME.accent },
        });
        const chartTypeMap: Record<string, 'bar' | 'pie' | 'line'> = {
          bar: 'bar', pie: 'pie', line: 'line',
        };
        const ctype = chartTypeMap[spec.chart.type] ?? 'bar';
        const data = spec.chart.series.map((se) => ({
          name: se.name ?? '系列',
          labels: se.labels,
          values: se.values,
        }));
        (slide as any).addChart(ctype, data, {
          x: 0.8, y: 1.3, w: 8.4, h: 3.9,
          chartColors: [THEME.primary, THEME.accent, '70AD47', 'FFC000', 'ED7D31', 'A5A5A5'],
          showLegend: data.length > 1 || ctype === 'pie',
          legendPos: 'b',
          showValue: ctype === 'bar' || ctype === 'pie',
          title: spec.chart.chartTitle ?? spec.title ?? '',
          titleFontSize: 12,
        });
        // 图表页若同时提供 bullets 则置于图表上方紧凑区
        if (spec.bullets?.length) {
          slide.addText(spec.bullets.map((b) => ({ text: b, options: { bullet: true } })), {
            x: 0.8, y: 5.15, w: 8.4, h: 0.5,
            fontSize: 11, color: THEME.text,
          });
        }
      } else if (layout === 'closing') {
        // 结尾页
        slide.addText(spec.title ?? '谢谢观看', {
          x: 0.5, y: 2.4, w: 9, h: 1,
          fontSize: 36, bold: true, color: 'FFFFFF', align: 'center',
        });
        slide.background = { color: THEME.primary };
      } else {
        // 内容页：标题条 + 要点列表
        slide.addText(spec.title ?? `第 ${i + 1} 页`, {
          x: 0.5, y: 0.35, w: 9, h: 0.7,
          fontSize: 24, bold: true, color: THEME.primary,
        });
        slide.addShape('rect', {
          x: 0.5, y: 1.05, w: 9, h: 0.03, fill: { color: THEME.accent },
        });
        const body = Array.isArray(spec.bullets)
          ? spec.bullets.map((b) => ({ text: b, options: { bullet: true } }))
          : typeof spec.content === 'string'
            ? spec.content
            : [];
        if (Array.isArray(body) && body.length > 0) {
          slide.addText(body, {
            x: 0.8, y: 1.4, w: 8.4, h: 3.8,
            fontSize: 16, color: THEME.text, lineSpacingMultiple: 1.4,
          });
        } else if (typeof body === 'string' && body) {
          slide.addText(body, {
            x: 0.8, y: 1.4, w: 8.4, h: 3.8,
            fontSize: 16, color: THEME.text, lineSpacingMultiple: 1.4,
          });
        }
      }
      if (spec.notes) {
        slide.addNotes(spec.notes);
      }
    }

    // 写文件（经 fs-guard：自动创建父目录；workspace 外路径已由 policy 确认并
    // grantDirectory 授权，fs-guard 放行；二进制以 Buffer 传入）
    const buf: Buffer = (await (pptx as any).write({ outputType: 'nodebuffer' })) as Buffer;
    try {
      ctx.fsGuard.writeFile(p, buf);
    } catch (err) {
      return { ok: false, output: `写入 ${p} 失败：${String(err)}`, artifacts: [] };
    }

    const art = makeArtifact(ctx.taskId, 'file', p, 'application/vnd.openxmlformats-officedocument.presentationml.presentation', sha256(buf));
    ctx.onArtifact?.(art);
    return {
      ok: true,
      output: `已生成 PPT：${p}（${slides.length} 页，${buf.length} 字节）`,
      artifacts: [art],
    };
  },
};
