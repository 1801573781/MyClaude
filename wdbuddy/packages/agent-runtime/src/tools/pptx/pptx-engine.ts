/**
 * PPTX 生成引擎（第一阶核心）—— 14 种版式 + 设计装饰 + 主题应用
 * 画布：LAYOUT_16x9（10 × 5.625 英寸）
 * 版式：cover / toc / section / content / two-col / cards / timeline /
 *       grid-2x2 / quote / compare / big-number / chart / image-text / closing
 */
import type { SlideTheme } from './themes.js';

/* ───────────── 数据结构 ───────────── */

export interface CardSpec {
  icon?: string;
  title: string;
  desc?: string;
}
export interface StepSpec {
  label: string;
  desc?: string;
}
export interface MetricSpec {
  value: string;
  label: string;
  desc?: string;
}

export interface SlideSpec {
  layout: string;
  title?: string;
  subtitle?: string;
  bullets?: string[];
  content?: string;
  notes?: string;
  background?: string;
  /** cards / grid-2x2：卡片组 */
  cards?: CardSpec[];
  /** timeline：流程步骤 */
  steps?: StepSpec[];
  /** toc / two-col：条目组 */
  items?: StepSpec[];
  /** quote：金句 */
  quote?: { text: string; author?: string };
  /** compare：左右对比 */
  compare?: {
    leftLabel: string;
    leftPoints: string[];
    rightLabel: string;
    rightPoints: string[];
  };
  /** big-number：数据大屏 */
  metrics?: MetricSpec[];
  /** chart：图表（bar/pie/line） */
  chart?: {
    type: 'bar' | 'pie' | 'line';
    chartTitle?: string;
    series: Array<{ name?: string; labels: string[]; values: number[] }>;
  };
  /** image-text：图文页（图片占位框 + 说明） */
  image?: { side?: 'left' | 'right'; caption?: string; desc?: string };
}

/* ───────────── 工具函数 ───────────── */

const W = 10;
const H = 5.625;

function tx(slide: any, text: string, opt: any): void {
  slide.addText(text, opt);
}

/** 底部进度条 + 页码（内容类页面装饰） */
function decorateProgress(slide: any, page: number, total: number, th: SlideTheme): void {
  // 底部细进度条：全宽灰轨 + 比例高亮
  slide.addShape('rect', { x: 0, y: H - 0.055, w: W, h: 0.055, fill: { color: 'E5E7EB' } });
  slide.addShape('rect', { x: 0, y: H - 0.055, w: (W * page) / Math.max(total, 1), h: 0.055, fill: { color: th.accent } });
  // 右下页码
  slide.addText(`${page} / ${total}`, {
    x: W - 1.0, y: H - 0.42, w: 0.85, h: 0.3, fontSize: 9, color: th.textMuted, align: 'right',
  });
}

/** 标题 + 渐变双段装饰线（内容类页头） */
function decorateTitle(slide: any, title: string, th: SlideTheme): void {
  tx(slide, title, {
    x: 0.55, y: 0.3, w: 8.9, h: 0.65, fontSize: 23, bold: true, color: th.primary,
  });
  // 双段下划线：主色长段 + 强调色短段
  slide.addShape('rect', { x: 0.55, y: 0.98, w: 1.5, h: 0.045, fill: { color: th.primary } });
  slide.addShape('rect', { x: 2.08, y: 0.98, w: 0.35, h: 0.045, fill: { color: th.accent } });
}

/** 深色页的几何装饰（封面/结尾/数据大屏） */
function decorateHero(slide: any, th: SlideTheme): void {
  slide.addShape('ellipse', {
    x: 7.6, y: -1.3, w: 3.6, h: 3.6, fill: { color: th.secondary, transparency: 68 },
  });
  slide.addShape('ellipse', {
    x: -1.2, y: 3.9, w: 2.9, h: 2.9, fill: { color: th.accent, transparency: 76 },
  });
  slide.addShape('rect', {
    x: 8.9, y: 4.3, w: 1.6, h: 1.6, fill: { color: th.primary, transparency: 55 },
  });
}

/** 卡片（圆角矩形 + 边框） */
function drawCard(slide: any, x: number, y: number, w: number, h: number, th: SlideTheme): void {
  slide.addShape('roundRect', {
    x, y, w, h, rectRadius: 0.08,
    fill: { color: th.cardBg },
    line: { color: th.secondary, width: 0.75 },
    shadow: { type: 'outer', blur: 5, offset: 2, angle: 90, color: '000000', transparency: 86 },
  });
}

/* ───────────── 版式渲染器 ───────────── */

type RenderCtx = { page: number; total: number };

function renderCover(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgDark };
  decorateHero(slide, th);
  tx(slide, s.title ?? '', {
    x: 0.6, y: 1.95, w: 8.8, h: 1.15, fontSize: 40, bold: true, color: 'FFFFFF', align: 'center',
  });
  if (s.subtitle ?? s.bullets?.length) {
    tx(slide, s.subtitle ?? s.bullets!.join('  ·  '), {
      x: 0.6, y: 3.15, w: 8.8, h: 0.6, fontSize: 17, color: th.textLight, align: 'center',
    });
  }
  // 底部装饰短线
  slide.addShape('rect', { x: W / 2 - 0.5, y: 3.95, w: 1.0, h: 0.05, fill: { color: th.accent } });
}

function renderToc(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  // 左侧竖色条
  slide.addShape('rect', { x: 0, y: 0, w: 0.16, h: H, fill: { color: th.primary } });
  tx(slide, s.title ?? '目录', {
    x: 0.7, y: 0.35, w: 4, h: 0.6, fontSize: 24, bold: true, color: th.primary,
  });
  const items: StepSpec[] = s.items ?? (s.bullets?.map((b) => ({ label: b })) as StepSpec[]) ?? [];
  const rowH = Math.min(0.72, (H - 1.4) / Math.max(items.length, 1));
  items.slice(0, 8).forEach((it, i) => {
    const y = 1.25 + i * rowH;
    tx(slide, String(i + 1).padStart(2, '0'), {
      x: 0.75, y, w: 0.75, h: 0.5, fontSize: 22, bold: true, color: th.accent,
    });
    tx(slide, it.label, {
      x: 1.6, y: y + 0.02, w: 6.5, h: 0.45, fontSize: 16, color: th.textDark,
    });
    if (it.desc) {
      tx(slide, it.desc, { x: 1.6, y: y + 0.36, w: 6.5, h: 0.3, fontSize: 10, color: th.textMuted });
    }
    if (i < items.length - 1) {
      slide.addShape('rect', { x: 1.6, y: y + rowH - 0.06, w: 7.4, h: 0.012, fill: { color: 'E5E7EB' } });
    }
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderSection(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  // 左上大编号
  tx(slide, s.subtitle ?? '', {
    x: 0.55, y: 0.85, w: 3, h: 1.5, fontSize: 72, bold: true, color: th.accent,
  });
  tx(slide, s.title ?? '', {
    x: 0.6, y: 2.6, w: 8.8, h: 0.85, fontSize: 30, bold: true, color: th.primary,
  });
  if (s.bullets?.length) {
    tx(slide, s.bullets.join('　'), { x: 0.62, y: 3.55, w: 8.6, h: 0.4, fontSize: 13, color: th.textMuted });
  }
  slide.addShape('rect', { x: 0.6, y: 2.45, w: 1.2, h: 0.05, fill: { color: th.primary } });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderContent(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  decorateTitle(slide, s.title ?? '', th);
  const body = Array.isArray(s.bullets)
    ? s.bullets.map((b) => ({ text: b, options: { bullet: true } }))
    : (s.content ?? '');
  if (Array.isArray(body) && body.length > 0) {
    slide.addText(body, {
      x: 0.85, y: 1.35, w: 8.3, h: 3.7, fontSize: 15, color: th.textDark,
      lineSpacingMultiple: 1.45, valign: 'top',
    });
  } else if (typeof body === 'string' && body) {
    slide.addText(body, {
      x: 0.85, y: 1.35, w: 8.3, h: 3.7, fontSize: 15, color: th.textDark,
      lineSpacingMultiple: 1.45, valign: 'top',
    });
  }
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderTwoCol(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  decorateTitle(slide, s.title ?? '', th);
  // 左栏：要点
  if (s.bullets?.length) {
    slide.addText(
      s.bullets.map((b) => ({ text: b, options: { bullet: true } })),
      { x: 0.55, y: 1.4, w: 4.2, h: 3.6, fontSize: 14, color: th.textDark, lineSpacingMultiple: 1.4, valign: 'top' }
    );
  }
  // 中缝分隔线
  slide.addShape('rect', { x: 5.0, y: 1.5, w: 0.018, h: 3.3, fill: { color: 'D8DEE8' } });
  // 右栏：条目卡片
  const items = s.items ?? [];
  const rowH = Math.min(0.85, 3.5 / Math.max(items.length, 1));
  items.slice(0, 4).forEach((it, i) => {
    const y = 1.4 + i * rowH;
    drawCard(slide, 5.25, y, 4.2, rowH - 0.12, th);
    tx(slide, it.label, { x: 5.5, y: y + 0.05, w: 3.8, h: 0.32, fontSize: 13, bold: true, color: th.primary });
    if (it.desc) {
      tx(slide, it.desc, { x: 5.5, y: y + 0.38, w: 3.8, h: rowH - 0.5, fontSize: 10.5, color: th.textMuted });
    }
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderCards(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  decorateTitle(slide, s.title ?? '', th);
  const cards = (s.cards ?? []).slice(0, 3);
  const cw = 2.85;
  const gap = 0.25;
  const totalW = cards.length * cw + (cards.length - 1) * gap;
  const x0 = (W - totalW) / 2;
  cards.forEach((c, i) => {
    const x = x0 + i * (cw + gap);
    drawCard(slide, x, 1.35, cw, 3.5, th);
    // 卡片顶部色条
    slide.addShape('rect', { x, y: 1.35, w: cw, h: 0.09, fill: { color: i === 1 ? th.accent : th.primary } });
    tx(slide, c.icon ?? String(i + 1), { x: x + 0.2, y: 1.6, w: cw - 0.4, h: 0.6, fontSize: 28, align: 'center' });
    tx(slide, c.title, { x: x + 0.15, y: 2.3, w: cw - 0.3, h: 0.45, fontSize: 15, bold: true, color: th.primary, align: 'center' });
    if (c.desc) {
      tx(slide, c.desc, { x: x + 0.22, y: 2.8, w: cw - 0.44, h: 1.85, fontSize: 11, color: th.textMuted, align: 'center', lineSpacingMultiple: 1.3, valign: 'top' });
    }
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderTimeline(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  decorateTitle(slide, s.title ?? '', th);
  const steps: StepSpec[] = (s.steps ?? s.items ?? []).slice(0, 5);
  const n = Math.max(steps.length, 1);
  const lineY = 2.9;
  const x0 = 1.0;
  const x1 = W - 1.0;
  const dx = (x1 - x0) / Math.max(n - 1, 1);
  // 主线
  slide.addShape('rect', { x: x0, y: lineY - 0.02, w: x1 - x0, h: 0.04, fill: { color: 'D8DEE8' } });
  steps.forEach((st, i) => {
    const cx = n === 1 ? (x0 + x1) / 2 : x0 + i * dx;
    // 节点圆（实心 accent + 外圈白）
    slide.addShape('ellipse', { x: cx - 0.17, y: lineY - 0.17, w: 0.34, h: 0.34, fill: { color: 'FFFFFF' }, line: { color: th.primary, width: 1.5 } });
    slide.addShape('ellipse', { x: cx - 0.09, y: lineY - 0.09, w: 0.18, h: 0.18, fill: { color: th.accent } });
    const above = i % 2 === 0;
    tx(slide, st.label, {
      x: cx - 0.85, y: above ? lineY - 1.05 : lineY + 0.32, w: 1.7, h: 0.4,
      fontSize: 12.5, bold: true, color: th.primary, align: 'center',
    });
    if (st.desc) {
      tx(slide, st.desc, {
        x: cx - 0.85, y: above ? lineY - 0.65 : lineY + 0.72, w: 1.7, h: 0.55,
        fontSize: 9.5, color: th.textMuted, align: 'center', lineSpacingMultiple: 1.15,
      });
    }
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderGrid2x2(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  decorateTitle(slide, s.title ?? '', th);
  const cards = ((s.cards ?? s.items?.map((it) => ({ title: it.label, desc: it.desc }))) as Array<{ title: string; desc?: string; icon?: string }>).slice(0, 4);
  const cw = 4.25;
  const ch = 1.85;
  const gx = 0.55;
  const gy = 1.3;
  const gap = 0.3;
  cards.forEach((c, i) => {
    const x = gx + (i % 2) * (cw + gap);
    const y = gy + Math.floor(i / 2) * (ch + gap);
    drawCard(slide, x, y, cw, ch, th);
    // 编号徽章
    slide.addShape('ellipse', { x: x + 0.22, y: y + 0.22, w: 0.5, h: 0.5, fill: { color: th.primary } });
    tx(slide, String(i + 1), { x: x + 0.22, y: y + 0.24, w: 0.5, h: 0.46, fontSize: 16, bold: true, color: 'FFFFFF', align: 'center' });
    tx(slide, c.title, { x: x + 0.9, y: y + 0.18, w: cw - 1.1, h: 0.4, fontSize: 13.5, bold: true, color: th.primary });
    if (c.desc) {
      tx(slide, c.desc, { x: x + 0.9, y: y + 0.62, w: cw - 1.15, h: ch - 0.8, fontSize: 10.5, color: th.textMuted, lineSpacingMultiple: 1.25, valign: 'top' });
    }
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderQuote(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  // 超大装饰引号
  tx(slide, '“', { x: 0.35, y: 0.25, w: 2.2, h: 2.2, fontSize: 130, bold: true, color: th.accent, transparency: 45 });
  const q = s.quote ?? { text: s.content ?? s.title ?? '' };
  tx(slide, q.text, {
    x: 1.5, y: 1.85, w: 7.0, h: 1.9, fontSize: 24, italic: true, color: th.primary,
    align: 'center', lineSpacingMultiple: 1.5, valign: 'middle',
  });
  if (q.author) {
    slide.addShape('rect', { x: W / 2 - 0.4, y: 3.95, w: 0.8, h: 0.035, fill: { color: th.accent } });
    tx(slide, `—— ${q.author}`, { x: 2, y: 4.1, w: 6, h: 0.4, fontSize: 13, color: th.textMuted, align: 'center' });
  }
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderCompare(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  decorateTitle(slide, s.title ?? '', th);
  const cmp = s.compare ?? { leftLabel: '', leftPoints: [], rightLabel: '', rightPoints: [] };
  // 左面板
  drawCard(slide, 0.55, 1.3, 4.15, 3.65, th);
  slide.addShape('rect', { x: 0.55, y: 1.3, w: 4.15, h: 0.55, fill: { color: th.primary } });
  tx(slide, cmp.leftLabel, { x: 0.55, y: 1.35, w: 4.15, h: 0.45, fontSize: 14, bold: true, color: 'FFFFFF', align: 'center' });
  if (cmp.leftPoints.length) {
    slide.addText(
      cmp.leftPoints.map((p) => ({ text: p, options: { bullet: true } })),
      { x: 0.85, y: 2.0, w: 3.6, h: 2.8, fontSize: 12, color: th.textDark, lineSpacingMultiple: 1.4, valign: 'top' }
    );
  }
  // 右面板
  drawCard(slide, 5.3, 1.3, 4.15, 3.65, th);
  slide.addShape('rect', { x: 5.3, y: 1.3, w: 4.15, h: 0.55, fill: { color: th.accent } });
  tx(slide, cmp.rightLabel, { x: 5.3, y: 1.35, w: 4.15, h: 0.45, fontSize: 14, bold: true, color: 'FFFFFF', align: 'center' });
  if (cmp.rightPoints.length) {
    slide.addText(
      cmp.rightPoints.map((p) => ({ text: p, options: { bullet: true } })),
      { x: 5.6, y: 2.0, w: 3.6, h: 2.8, fontSize: 12, color: th.textDark, lineSpacingMultiple: 1.4, valign: 'top' }
    );
  }
  // 中央 VS 徽章
  slide.addShape('ellipse', { x: 4.6, y: 2.7, w: 0.8, h: 0.8, fill: { color: th.bgDark }, line: { color: th.accent, width: 1.5 } });
  tx(slide, 'VS', { x: 4.6, y: 2.75, w: 0.8, h: 0.7, fontSize: 16, bold: true, color: 'FFFFFF', align: 'center' });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderBigNumber(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgDark };
  decorateHero(slide, th);
  tx(slide, s.title ?? '', { x: 0.6, y: 0.55, w: 8.8, h: 0.6, fontSize: 20, bold: true, color: th.textLight, align: 'center' });
  const metrics = (s.metrics ?? []).slice(0, 3);
  const mw = 8.8 / Math.max(metrics.length, 1);
  metrics.forEach((m, i) => {
    const x = 0.6 + i * mw;
    tx(slide, m.value, {
      x, y: 1.55, w: mw, h: 1.5, fontSize: 60, bold: true, color: th.accent, align: 'center',
    });
    tx(slide, m.label, { x, y: 3.15, w: mw, h: 0.45, fontSize: 15, bold: true, color: 'FFFFFF', align: 'center' });
    if (m.desc) {
      tx(slide, m.desc, { x: x + 0.3, y: 3.65, w: mw - 0.6, h: 0.75, fontSize: 11, color: th.textLight, align: 'center', lineSpacingMultiple: 1.25 });
    }
    if (i < metrics.length - 1) {
      slide.addShape('rect', { x: x + mw - 0.01, y: 1.8, w: 0.015, h: 1.8, fill: { color: th.textMuted } });
    }
  });
}

function renderChart(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  decorateTitle(slide, s.title ?? '数据图表', th);
  const chart = s.chart;
  if (!chart || !Array.isArray(chart.series) || chart.series.length === 0) return;
  const typeMap: Record<string, 'bar' | 'pie' | 'line'> = { bar: 'bar', pie: 'pie', line: 'line' };
  const ctype = typeMap[chart.type] ?? 'bar';
  const data = chart.series.map((se) => ({ name: se.name ?? '系列', labels: se.labels, values: se.values }));
  (slide as any).addChart(ctype, data, {
    x: 0.8, y: 1.25, w: 8.4, h: 3.6,
    chartColors: [th.primary, th.accent, th.secondary, '70AD47', 'FFC000', 'ED7D31', 'A5A5A5'],
    showLegend: data.length > 1 || ctype === 'pie',
    legendPos: 'b',
    showValue: ctype !== 'line',
    title: chart.chartTitle ?? '',
    titleFontSize: 11,
  });
  if (s.bullets?.length) {
    slide.addText(
      s.bullets.map((b) => ({ text: b, options: { bullet: true } })),
      { x: 0.85, y: 4.9, w: 8.3, h: 0.5, fontSize: 10, color: th.textMuted, lineSpacingMultiple: 1.15 }
    );
  }
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderImageText(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgLight };
  decorateTitle(slide, s.title ?? '', th);
  const imgLeft = (s.image?.side ?? 'left') === 'left';
  const imgX = imgLeft ? 0.55 : 6.0;
  const txtX = imgLeft ? 5.1 : 0.55;
  // 图片占位框（渐变感的色块 + 图标）
  slide.addShape('roundRect', {
    x: imgX, y: 1.3, w: 3.9, h: 3.6, rectRadius: 0.08,
    fill: { color: th.secondary, transparency: 30 },
    line: { color: th.primary, width: 1 },
  });
  tx(slide, '🖼', { x: imgX + 0.5, y: 2.2, w: 2.9, h: 1.0, fontSize: 40, align: 'center' });
  if (s.image?.caption) {
    tx(slide, s.image.caption, { x: imgX + 0.3, y: 3.4, w: 3.3, h: 0.4, fontSize: 11, color: th.textMuted, align: 'center' });
  }
  // 文字区
  if (s.bullets?.length) {
    slide.addText(
      s.bullets.map((b) => ({ text: b, options: { bullet: true } })),
      { x: txtX, y: 1.4, w: 4.3, h: 3.5, fontSize: 13.5, color: th.textDark, lineSpacingMultiple: 1.45, valign: 'top' }
    );
  } else if (s.content) {
    tx(slide, s.content, { x: txtX, y: 1.4, w: 4.3, h: 3.5, fontSize: 13.5, color: th.textDark, lineSpacingMultiple: 1.45, valign: 'top' });
  }
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderClosing(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? th.bgDark };
  decorateHero(slide, th);
  tx(slide, s.title ?? '谢谢观看', {
    x: 0.6, y: 2.1, w: 8.8, h: 1.0, fontSize: 38, bold: true, color: 'FFFFFF', align: 'center',
  });
  if (s.subtitle ?? s.bullets?.length) {
    tx(slide, s.subtitle ?? s.bullets!.join('　'), {
      x: 0.6, y: 3.2, w: 8.8, h: 0.5, fontSize: 15, color: th.textLight, align: 'center',
    });
  }
  slide.addShape('rect', { x: W / 2 - 0.5, y: 3.95, w: 1.0, h: 0.05, fill: { color: th.accent } });
}

/* ───────────── 主入口 ───────────── */

const RENDERERS: Record<string, (slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx) => void> = {
  cover: renderCover,
  toc: renderToc,
  section: renderSection,
  content: renderContent,
  'two-col': renderTwoCol,
  cards: renderCards,
  timeline: renderTimeline,
  'grid-2x2': renderGrid2x2,
  quote: renderQuote,
  compare: renderCompare,
  'big-number': renderBigNumber,
  chart: renderChart,
  'image-text': renderImageText,
  closing: renderClosing,
};

/** 渲染全部幻灯片到 pptx 实例（不落盘，由调用方写文件） */
export function renderSlidesTo(pptx: any, slides: SlideSpec[], theme: SlideTheme): void {
  const total = slides.length;
  slides.forEach((s, i) => {
    const slide = pptx.addSlide();
    const layout = s.layout ?? 'content';
    const renderer = RENDERERS[layout] ?? renderContent;
    renderer(slide, s, theme, { page: i + 1, total });
    if (s.notes) slide.addNotes(s.notes);
  });
}
