/**
 * PPTX 生成引擎（v2 全面重写）—— 对标 workbuddy 原版视觉水准
 * 画布：LAYOUT_WIDE（13.33 × 7.5 英寸，16:9 全幅）
 * 每页结构（对齐原版每页 20-59 形状的丰富度）：
 *   - 内容页统一页眉：左上编号徽章（01 · 章节名）+ 大标题 + 顶部/底部
 *     装饰圆（出血圆）+ 每卡片独立图标位（emoji 字符大号渲染）
 *   - 深色页（封面/结尾/数据大屏）：渐变感多层装饰 + 网格点缀
 *   - 每页支持讲者备注（notes），原版 10/10 页有备注
 * 版式：cover / toc / section / content / two-col / cards / timeline /
 *       grid-2x2 / quote / compare / big-number / chart / image-text / closing
 */
import type { SlideTheme } from './themes.js';
import { mixHex, liftOnDark } from './themes.js';

/* ───────────── 数据结构 ───────────── */

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
  /** 页眉章节徽章（如 "01 · 产品定位"）；未提供则按页序自动生成 */
  badge?: string;
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

/* ───────────── 画布与工具函数 ───────────── */

const W = 13.333;
const H = 7.5;

/** 深浅自适应调色：dark 主题下内容页的背景/标题/卡片/边框/正文/次要色全部
 *  从浅色系切换为深色系（与封面/结尾全篇统一，消除"深浅交替"风格割裂） */
function pal(th: SlideTheme): {
  bg: string;
  title: string;
  body: string;
  muted: string;
  card: string;
  cardLine: string;
  divider: string;
  badgeFill: string;
} {
  if (th.dark) {
    return {
      bg: th.bgDark,
      title: liftOnDark(th.primary),
      body: th.textLight,
      muted: th.textMuted,
      card: mixHex(th.bgDark, 'FFFFFF', 0.07), // 深底上的微亮卡片
      cardLine: mixHex(th.bgDark, 'FFFFFF', 0.22),
      divider: mixHex(th.bgDark, 'FFFFFF', 0.18),
      badgeFill: mixHex(th.bgDark, th.accent, 0.14),
    };
  }
  return {
    bg: th.bgLight,
    title: th.primary,
    body: th.textDark,
    muted: th.textMuted,
    card: th.cardBg,
    cardLine: lighten(th.primary, 0.75),
    divider: 'D8DEE8',
    badgeFill: lighten(th.primary, 0.88),
  };
}

/** 全局字体：微软雅黑（用户指定：其他字体渲染效果不佳）。
 *  pptxgenjs 不传 fontFace 时默认 Calibri/Arial，中文渲染发虚难看 */
const FONT = 'Microsoft YaHei';

function tx(slide: any, text: string, opt: any): void {
  slide.addText(text, { fontFace: FONT, ...opt });
}

/** 颜色减淡：16进制色 → 与白混合（深色卡片用），ratio 0~1 */
function lighten(hex: string, ratio: number): string {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  const mix = (c: number): number => Math.round(c + (255 - c) * ratio);
  return [mix(r), mix(g), mix(b)].map((c) => c.toString(16).padStart(2, '0')).join('').toUpperCase();
}

/** 主题色适配深色底：偏暗的主题色在深底上提亮 */
function onDark(hex: string): string {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  const lum = 0.299 * r + 0.587 * g + 0.114 * b;
  return lum < 100 ? lighten(h, 0.45) : hex;
}

/** 文本宽度估算（英寸）：汉字/全角符号 = 1.0em，半角（数字/字母/标点）≈ 0.55em。
 *  微软雅黑实测近似值，用于大字号文本的换行预判 */
export function measureText(text: string, fontSizePt: number): number {
  const em = fontSizePt / 72;
  let w = 0;
  for (const ch of text) {
    w += /[\u2E80-\u9FFF\uF900-\uFAFF\uFF01-\uFF60\u3000-\u303F]/.test(ch) ? em : em * 0.55;
  }
  return w;
}

/** 字号自适应：文本在可用宽度内不换行的最大字号（下限 minPt）。
 *  治本方案——任何长度的数字/文本都自动适配，杜绝"12.6亿元换行、486家不换行"的
 *  排版不一致（固定字号永远会有超宽的长度变体）。
 *  超过 minPt 仍放不下（极端长值）：返回 minPt，由调用方决定换行/缩略 */
export function fitFontSize(text: string, maxW: number, basePt: number, minPt = 30): number {
  const est = measureText(text, basePt);
  if (est <= maxW) return basePt;
  const fitted = Math.floor(basePt * (maxW / est));
  return Math.max(minPt, fitted);
}

/** 度量页基准字号：按栏数递减（3 栏 76pt 起步空间大；4 栏及以上 60pt 起步） */
export function metricBasePt(nCols: number): number {
  return nCols <= 3 ? 76 : 60;
}

/** 底部进度条 + 页码（内容类页面装饰） */
function decorateProgress(slide: any, page: number, total: number, th: SlideTheme): void {
  const p = pal(th);
  slide.addShape('rect', { x: 0, y: H - 0.06, w: W, h: 0.06, fill: { color: p.divider } });
  slide.addShape('rect', { x: 0, y: H - 0.06, w: (W * page) / Math.max(total, 1), h: 0.06, fill: { color: th.accent } });
  tx(slide, `${page} / ${total}`, {
    x: W - 1.1, y: H - 0.48, w: 0.9, h: 0.32, fontSize: 10, color: p.muted, align: 'right',
  });
}

/** 内容页页眉：左上编号徽章（01 · 章节）+ 大标题 + 双段装饰线。
 *  对齐原版：徽章小字（0.9,0.4,9×0.3）+ 大标题（0.6,0.8,12.1×0.7） */
function decorateTitle(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  // 页眉徽章已移除（用户反馈：页序号+标题小字冗余占版面）——标题直接上移
  tx(slide, s.title ?? '', {
    x: 0.62, y: 0.42, w: 12.1, h: 0.72, fontSize: 28, bold: true, color: p.title,
  });
  slide.addShape('rect', { x: 0.62, y: 1.2, w: 1.6, h: 0.05, fill: { color: p.title } });
  slide.addShape('rect', { x: 2.26, y: 1.2, w: 0.38, h: 0.05, fill: { color: th.accent } });
}

/** 出血装饰圆（原版每页右上/左下角两个大圆）；深色主题下降低透明度更沉浸 */
function decorateCorners(slide: any, th: SlideTheme): void {
  slide.addShape('ellipse', {
    x: 12.2, y: -1.4, w: 3.1, h: 3.1,
    fill: { color: th.secondary, transparency: th.dark ? 55 : 72 },
  });
  slide.addShape('ellipse', {
    x: -1.5, y: 5.9, w: 3.3, h: 3.3,
    fill: { color: th.accent, transparency: th.dark ? 68 : 80 },
  });
}

/** 深色页的几何装饰（封面/结尾/数据大屏）：多层出血圆 + 圆角矩形点缀 + 网格。
 *  浅色主题（th.dark=false）下全部使用主题浅色系（secondary/accent 半透明
 *  叠浅底），全篇配色统一；深色主题保持原有霓虹效果 */
function decorateHero(slide: any, th: SlideTheme): void {
  const decoColor2 = th.dark ? th.secondary : lighten(th.secondary, 0.55);
  const decoAccent = th.dark ? th.accent : lighten(th.accent, 0.62);
  const decoPrimary = th.dark ? onDark(th.primary) : lighten(th.primary, 0.45);
  const gridColor = th.dark ? th.textLight : th.primary;
  slide.addShape('ellipse', {
    x: 9.2, y: -2.9, w: 6.4, h: 6.4, fill: { color: decoColor2, transparency: 62 },
  });
  slide.addShape('ellipse', {
    x: 10.6, y: -1.3, w: 3.4, h: 3.4, fill: { color: decoColor2, transparency: 40 },
  });
  slide.addShape('ellipse', {
    x: -2.0, y: 4.8, w: 5.0, h: 5.0, fill: { color: decoAccent, transparency: 76 },
  });
  slide.addShape('rect', {
    x: 11.6, y: 5.7, w: 1.9, h: 1.9, fill: { color: decoPrimary, transparency: 62 },
  });
  // 网格点缀（右上区域）
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 5; c++) {
      slide.addShape('ellipse', {
        x: 10.9 + c * 0.34, y: 5.9 + r * 0.34, w: 0.07, h: 0.07,
        fill: { color: gridColor, transparency: 72 },
      });
    }
  }
}

/** 卡片（圆角矩形 + 边框 + 阴影）；深色模式用深底微亮卡片 */
function drawCard(slide: any, x: number, y: number, w: number, h: number, th: SlideTheme): void {
  const p = pal(th);
  slide.addShape('roundRect', {
    x, y, w, h, rectRadius: 0.09,
    fill: { color: p.card },
    line: { color: p.cardLine, width: 0.75 },
    shadow: { type: 'outer', blur: 6, offset: 2, angle: 90, color: '000000', transparency: th.dark ? 70 : 86 },
  });
}

/** 图标圆（彩色圆底 + emoji/字符图标），原版「彩色圆圈中的 Lucide 图标」母题。
 *  深色模式：半透明 accent 圆底 + accent 描边。
 *  根因修复（像素级验证 + XML 码点比对结论）：
 *  1) emoji 显式 Segoe UI Emoji 字体（雅黑无 emoji 字形，回退渲染位置失控）
 *  2) 统一剥离 VS16 变体选择符（U+FE0F）：带 VS16 的 emoji（如 🛠️）触发
 *     PowerPoint 彩色渲染管线，垂直位置与黑白文本管线不同（实测偏 12px）；
 *     剥离后所有图标统一走黑白文本管线，行为一致
 *  3) 显式颜色：深色模式白色、浅色模式深灰（默认黑色在深底上不可见）
 *  4) 零垂直补偿（像素标定：下沉0.02→+2.8px，上提0.025→-5.2px，零点居中） */
function drawIconCircle(slide: any, cx: number, cy: number, icon: string, th: SlideTheme, variant = 0): void {
  const colors = [th.primary, th.secondary, th.accent];
  const color = th.dark ? th.accent : onDark(colors[variant % 3]!);
  const fill = th.dark
    ? { color: color, transparency: 78 }
    : { color: lighten(color, 0.86) };
  slide.addShape('ellipse', {
    x: cx, y: cy, w: 0.62, h: 0.62, fill, line: { color: color, width: 1 },
  });
  const emojiColor = th.dark ? 'FFFFFF' : th.textDark;
  tx(slide, icon.replace(/\uFE0F/g, ''), {
    x: cx, y: cy, w: 0.62, h: 0.62, fontSize: 19,
    fontFace: 'Segoe UI Emoji', color: emojiColor,
    align: 'center', valign: 'middle', margin: 0,
  });
}

/* ───────────── 版式渲染器 ───────────── */

type RenderCtx = { page: number; total: number };

/** 结构化版式空数据兜底：专属字段（cards/steps/metrics…）缺失时按 content
 *  形态渲染 bullets/content——结构化解析可失败，但页面永不空（根治疗法） */
function renderBulletsFallback(
  slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx
): boolean {
  const p = pal(th);
  const bl = s.bullets ?? [];
  if (bl.length === 0 && !s.content) return false; // 无任何数据，交由调用方
  if (bl.length === 0 && s.content) {
    tx(slide, s.content, { x: 0.9, y: 2.0, w: 11.5, h: 4.4, fontSize: 16, color: p.body, lineSpacingMultiple: 1.5, valign: 'top' });
  } else {
    const yBase = s.subtitle ? 2.55 : 2.15;
    bl.slice(0, 5).forEach((b, i) => {
      const y = yBase + i * 0.85;
      const bi = bulletIcon(b, i);
      drawIconCircle(slide, 0.95, y, bi.icon, th, i);
      tx(slide, bi.text, { x: 1.8, y: y + 0.08, w: 10.6, h: 0.5, fontSize: 15.5, color: p.body, valign: 'middle' });
    });
  }
  decorateProgress(slide, ctx.page, ctx.total, th);
  return true;
}

/** 默认图标池（无 icon 指定时按序取用；一律无 VS16，统一黑白文本渲染管线） */
const ICON_POOL = ['🚀', '💡', '🛠', '🎯', '📊', '🔍', '⚙', '🌟', '📦', '💬', '⏱', '🔔']

/** 要点行首 emoji 剥离："🚀 智能规划" → { icon:'🚀', text:'智能规划' }。
 *  返回 icon 一律剥离 VS16（与 drawIconCircle 的统一管线一致） */
function bulletIcon(text: string, fallbackIdx: number): { icon: string; text: string } {
  const m =
    /^([\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F000}-\u{1F02F}✓✕★☆✔✖⚙])\uFE0F?\s+([\s\S]+)$/u.exec(
      text
    );
  return m
    ? { icon: m[1]!, text: m[2]!.trim() }
    : { icon: ICON_POOL[fallbackIdx % ICON_POOL.length]!.replace(/\uFE0F/g, ''), text };
}

function renderCover(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateHero(slide, th);
  // 顶部徽章条（浅色主题：主色浅底深字）
  if (s.subtitle) {
    const bw = Math.min(0.4 + s.subtitle.length * 0.185, 7.5);
    slide.addShape('roundRect', {
      x: (W - bw) / 2, y: 1.55, w: bw, h: 0.5, rectRadius: 0.25,
      fill: { color: th.dark ? onDark(th.primary) : lighten(th.primary, 0.9) },
      line: { color: th.accent, width: 1 },
    });
    tx(slide, s.subtitle, {
      x: (W - bw) / 2, y: 1.55, w: bw, h: 0.5, fontSize: 13,
      color: th.dark ? 'FFFFFF' : th.primary,
      align: 'center', valign: 'middle', margin: 0,
    });
  }
  tx(slide, s.title ?? '', {
    x: 0.7, y: 2.5, w: 11.9, h: 1.4, fontSize: 48, bold: true,
    color: th.dark ? 'FFFFFF' : p.title, align: 'center',
  });
  if (s.bullets?.length) {
    tx(slide, s.bullets.join('  ·  '), {
      x: 1.2, y: 4.1, w: 10.9, h: 0.55, fontSize: 16, color: p.muted, align: 'center',
    });
  }
  slide.addShape('rect', { x: W / 2 - 0.6, y: 4.85, w: 1.2, h: 0.055, fill: { color: th.accent } });
}

function renderToc(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  slide.background = { color: s.background ?? pal(th).bg };
  decorateCorners(slide, th);
  // 左侧竖色条
  slide.addShape('rect', { x: 0, y: 0, w: 0.16, h: H, fill: { color: pal(th).title } });
  decorateTitle(slide, s, th, ctx);
  const items: StepSpec[] = s.items ?? (s.bullets?.map((b) => ({ label: b })) as StepSpec[]) ?? [];
  const twoCol = items.length > 5;
  const shown = items.slice(0, 8);
  const colN = twoCol ? Math.ceil(shown.length / 2) : shown.length;
  const rowH = Math.min(0.88, (H - 2.2) / Math.max(colN, 1));
  shown.forEach((it, i) => {
    const col = twoCol ? Math.floor(i / colN) : 0;
    const row = twoCol ? i % colN : i;
    const x = 0.85 + col * 6.2;
    const y = 2.15 + row * rowH;
    // 编号圆（文本框与圆完全重合，margin:0 消除默认内边距）
    slide.addShape('ellipse', { x, y: y + 0.04, w: 0.52, h: 0.52, fill: { color: i % 2 === 0 ? th.primary : th.accent } });
    tx(slide, String(i + 1).padStart(2, '0'), {
      x, y: y + 0.04, w: 0.52, h: 0.52, fontSize: 14, bold: true, color: 'FFFFFF',
      align: 'center', valign: 'middle', margin: 0,
    });
    tx(slide, it.label, {
      x: x + 0.72, y: y + 0.02, w: 5.2, h: 0.42, fontSize: 17, color: pal(th).body,
    });
    if (it.desc) {
      tx(slide, it.desc, { x: x + 0.72, y: y + 0.44, w: 5.2, h: 0.3, fontSize: 11, color: pal(th).muted });
    }
    if (row < colN - 1) {
      slide.addShape('rect', { x: x + 0.72, y: y + rowH - 0.08, w: 4.9, h: 0.014, fill: { color: pal(th).divider } });
    }
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderSection(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  // 章节编号（副标题）：短编号（"01"）用大字号；长副标题（"目标更清晰…"）
  // 是章节导语而非编号，用小字——修复 96pt 长文本溢出画布
  const sub = s.subtitle ?? '';
  const isShortBadge = sub.length <= 6 && !/[，。；、！？]/.test(sub);
  if (sub) {
    if (isShortBadge) {
      tx(slide, sub, { x: 0.7, y: 1.0, w: 4, h: 1.9, fontSize: 96, bold: true, color: th.accent });
    } else {
      slide.addShape('roundRect', {
        x: 0.75, y: 1.1, w: Math.min(0.4 + sub.length * 0.22, 10.5), h: 0.55, rectRadius: 0.27,
        fill: { color: p.badgeFill }, line: { color: th.accent, width: 0.75 },
      });
      tx(slide, sub, {
        x: 0.75, y: 1.1, w: Math.min(0.4 + sub.length * 0.22, 10.5), h: 0.55,
        fontSize: 15, bold: true, color: th.dark ? th.accent : th.primary,
        align: 'center', valign: 'middle', margin: 0,
      });
    }
  }
  tx(slide, s.title ?? '', {
    x: 0.75, y: 3.35, w: 11.8, h: 1.0, fontSize: 36, bold: true, color: p.title,
  });
  if (s.bullets?.length) {
    tx(slide, s.bullets.join('　'), { x: 0.78, y: 4.55, w: 11.6, h: 0.45, fontSize: 14, color: p.muted });
  }
  slide.addShape('rect', { x: 0.75, y: 3.2, w: 1.3, h: 0.055, fill: { color: p.title } });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderContent(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  decorateTitle(slide, s, th, ctx);
  const bl = s.bullets ?? [];
  if (bl.length === 0 && s.content) {
    tx(slide, s.content, {
      x: 0.9, y: 2.0, w: 11.5, h: 4.4, fontSize: 16, color: p.body,
      lineSpacingMultiple: 1.5, valign: 'top',
    });
  } else if (bl.length > 0) {
    // 要点页引导语（subtitle：如公式"好提示词 = 目标 + 输入 + 输出 + 约束"，
    // md 解析时首段落行进入 subtitle，content 版式此前不渲染导致内容丢失）
    if (s.subtitle) {
      const subY = 1.88;
      slide.addShape('roundRect', {
        x: 0.62, y: subY, w: Math.min(0.4 + s.subtitle.length * 0.17, 11.0), h: 0.46, rectRadius: 0.06,
        fill: { color: p.badgeFill },
      });
      tx(slide, s.subtitle, {
        x: 0.62, y: subY, w: Math.min(0.4 + s.subtitle.length * 0.17, 11.0), h: 0.46,
        fontSize: 13, bold: true, color: th.dark ? th.accent : th.primary,
        align: 'center', valign: 'middle', margin: 0,
      });
    }
    // 要点 + 底部 content 补充区
    if (s.content) {
      const box = bl.length >= 5
        ? { x: 0.9, y: 5.95, w: 11.5, h: 0.85, fs: 12.5 }   // 两栏要点时底部窄条
        : { x: 0.9, y: 2.6 + bl.length * 0.85 + 0.1, w: 11.5, h: 0.75, fs: 14 };
      slide.addShape('roundRect', {
        x: box.x, y: box.y, w: box.w, h: box.h, rectRadius: 0.06,
        fill: { color: p.badgeFill },
      });
      tx(slide, s.content, {
        x: box.x + 0.25, y: box.y + 0.06, w: box.w - 0.5, h: box.h - 0.12,
        fontSize: box.fs, bold: true, color: p.title, align: 'center', valign: 'middle', margin: 0,
      });
    }
    // ≥5 条要点升级为两栏排布（避免拥挤）
    if (bl.length >= 5) {
      const colN = Math.ceil(bl.length / 2);
      [bl.slice(0, colN), bl.slice(colN)].forEach((col, ci) => {
        col.forEach((b, i) => {
          const x = 0.9 + ci * 6.0;
          const y = 2.1 + i * 0.82;
          drawIconCircle(slide, x, y, ICON_POOL[(ci * colN + i) % ICON_POOL.length]!, th, ci * colN + i);
          tx(slide, b, {
            x: x + 0.82, y: y + 0.08, w: 4.9, h: 0.5, fontSize: 15, color: th.textDark, valign: 'middle',
          });
        });
      });
    } else {
      // subtitle 存在时要点区下移（引导语占位 1.88~2.34）
      const yBase = s.subtitle ? 2.55 : 2.15;
      bl.forEach((b, i) => {
        const y = yBase + i * 0.85;
        const bi = bulletIcon(b, i);
        drawIconCircle(slide, 0.95, y, bi.icon, th, i);
        tx(slide, bi.text, {
          x: 1.8, y: y + 0.08, w: 10.6, h: 0.5, fontSize: 15.5, color: p.body, valign: 'middle',
        });
      });
    }
  }
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderTwoCol(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  decorateTitle(slide, s, th, ctx);
  // 左栏：要点
  if (s.bullets?.length) {
    s.bullets.slice(0, 5).forEach((b, i) => {
      const y = 2.1 + i * 0.85;
      const bi = bulletIcon(b, i);
      drawIconCircle(slide, 0.7, y, bi.icon, th, i);
      tx(slide, bi.text, { x: 1.52, y: y + 0.08, w: 4.4, h: 0.5, fontSize: 14.5, color: p.body, valign: 'middle' });
    });
  }
  // 中缝分隔线
  slide.addShape('rect', { x: 6.55, y: 2.1, w: 0.02, h: 4.3, fill: { color: p.divider } });
  // 右栏：条目卡片
  const items = (s.items ?? []).slice(0, 4);
  const rowH = Math.min(1.12, 4.35 / Math.max(items.length, 1));
  items.forEach((it, i) => {
    const y = 2.05 + i * rowH;
    drawCard(slide, 6.85, y, 5.75, rowH - 0.15, th);
    slide.addShape('rect', { x: 6.85, y, w: 0.07, h: rowH - 0.15, fill: { color: i % 2 === 0 ? p.title : th.accent } });
    tx(slide, it.label, { x: 7.15, y: y + 0.07, w: 5.2, h: 0.36, fontSize: 14, bold: true, color: p.title });
    if (it.desc) {
      tx(slide, it.desc, { x: 7.15, y: y + 0.45, w: 5.2, h: rowH - 0.6, fontSize: 11, color: p.muted, valign: 'top' });
    }
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderCards(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  decorateTitle(slide, s, th, ctx);
  const cards = (s.cards ?? []).slice(0, 3);
  if (cards.length === 0 && renderBulletsFallback(slide, s, th, ctx)) return;
  const cw = 3.85;
  const gap = 0.35;
  const totalW = cards.length * cw + (cards.length - 1) * gap;
  const x0 = (W - totalW) / 2;
  cards.forEach((c, i) => {
    const x = x0 + i * (cw + gap);
    drawCard(slide, x, 2.0, cw, 4.45, th);
    // 卡片顶部色条
    slide.addShape('rect', { x, y: 2.0, w: cw, h: 0.1, fill: { color: i === 1 ? th.accent : p.title } });
    drawIconCircle(slide, x + cw / 2 - 0.44, 2.4, c.icon ?? ICON_POOL[i % ICON_POOL.length]!, th, i);
    tx(slide, c.title, { x: x + 0.2, y: 3.25, w: cw - 0.4, h: 0.5, fontSize: 17, bold: true, color: p.title, align: 'center' });
    if (c.desc) {
      tx(slide, c.desc, { x: x + 0.3, y: 3.85, w: cw - 0.6, h: 2.3, fontSize: 12, color: p.muted, align: 'center', lineSpacingMultiple: 1.35, valign: 'top' });
    }
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderTimeline(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  decorateTitle(slide, s, th, ctx);
  const steps: StepSpec[] = (s.steps ?? s.items ?? []).slice(0, 5);
  // 兜底：steps 为空时按 content 页渲染 bullets（页面永不空）
  if (steps.length === 0) {
    const bl = s.bullets ?? [];
    if (bl.length === 0 && s.content) {
      tx(slide, s.content, { x: 0.9, y: 2.0, w: 11.5, h: 4.4, fontSize: 16, color: p.body, lineSpacingMultiple: 1.5, valign: 'top' });
    } else if (bl.length > 0) {
      const yBase = s.subtitle ? 2.55 : 2.15;
      bl.slice(0, 5).forEach((b, i) => {
        const y = yBase + i * 0.85;
        const bi = bulletIcon(b, i);
        drawIconCircle(slide, 0.95, y, bi.icon, th, i);
        tx(slide, bi.text, { x: 1.8, y: y + 0.08, w: 10.6, h: 0.5, fontSize: 15.5, color: p.body, valign: 'middle' });
      });
    }
    decorateProgress(slide, ctx.page, ctx.total, th);
    return;
  }
  const n = Math.max(steps.length, 1);
  const lineY = 4.15;
  const x0 = 1.3;
  const x1 = W - 1.3;
  const dx = (x1 - x0) / Math.max(n - 1, 1);
  // 主线
  slide.addShape('rect', { x: x0, y: lineY - 0.025, w: x1 - x0, h: 0.05, fill: { color: p.divider } });
  steps.forEach((st, i) => {
    const cx = n === 1 ? (x0 + x1) / 2 : x0 + i * dx;
    // 步骤卡片（上方）
    const cw = 2.15;
    drawCard(slide, cx - cw / 2, 2.05, cw, 1.7, th);
    slide.addShape('rect', { x: cx - cw / 2, y: 2.05, w: cw, h: 0.07, fill: { color: i % 2 === 0 ? p.title : th.accent } });
    tx(slide, st.label, {
      x: cx - cw / 2 + 0.1, y: 2.2, w: cw - 0.2, h: 0.4, fontSize: 13.5, bold: true, color: p.title, align: 'center',
    });
    if (st.desc) {
      tx(slide, st.desc, {
        x: cx - cw / 2 + 0.12, y: 2.62, w: cw - 0.24, h: 1.05, fontSize: 10.5, color: p.muted, align: 'center', lineSpacingMultiple: 1.2, valign: 'top',
      });
    }
    // 节点圆（实心 accent + 外圈底色）
    slide.addShape('ellipse', { x: cx - 0.22, y: lineY - 0.22, w: 0.44, h: 0.44, fill: { color: p.card }, line: { color: p.title, width: 1.75 } });
    slide.addShape('ellipse', { x: cx - 0.11, y: lineY - 0.11, w: 0.22, h: 0.22, fill: { color: th.accent } });
    // 序号
    tx(slide, String(i + 1), {
      x: cx - 0.25, y: lineY + 0.32, w: 0.5, h: 0.35, fontSize: 13, bold: true, color: p.muted, align: 'center',
    });
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderGrid2x2(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  decorateTitle(slide, s, th, ctx);
  const cards = ((s.cards ?? s.items?.map((it) => ({ title: it.label, desc: it.desc }))) as Array<CardSpec>).slice(0, 4);
  if (cards.length === 0 && renderBulletsFallback(slide, s, th, ctx)) return;
  const cw = 5.85;
  const ch = 2.28;
  const gx = 0.7;
  const gy = 2.05;
  const gap = 0.32;
  cards.forEach((c, i) => {
    const x = gx + (i % 2) * (cw + gap);
    const y = gy + Math.floor(i / 2) * (ch + gap);
    drawCard(slide, x, y, cw, ch, th);
    drawIconCircle(slide, x + 0.3, y + 0.3, c.icon ?? ICON_POOL[i % ICON_POOL.length]!, th, i);
    tx(slide, c.title, { x: x + 1.15, y: y + 0.28, w: cw - 1.35, h: 0.45, fontSize: 16, bold: true, color: p.title });
    if (c.desc) {
      tx(slide, c.desc, { x: x + 1.15, y: y + 0.82, w: cw - 1.5, h: ch - 1.0, fontSize: 12, color: p.muted, lineSpacingMultiple: 1.3, valign: 'top' });
    }
    // 右下角编号水印
    tx(slide, String(i + 1), {
      x: x + cw - 0.75, y: y + ch - 0.75, w: 0.55, h: 0.55, fontSize: 26, bold: true,
      color: th.dark ? mixHex(th.bgDark, 'FFFFFF', 0.16) : lighten(th.primary, 0.82),
      align: 'center', valign: 'middle',
    });
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

/** 通用 N 列网格（grid-3x2 六卡 / grid-4x2 八卡，对齐原版多卡片场景页） */
function renderGridN(
  slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx, cols: number, max: number
): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  decorateTitle(slide, s, th, ctx);
  const cards = (s.cards ?? []).slice(0, max);
  if (cards.length === 0 && renderBulletsFallback(slide, s, th, ctx)) return;
  const rows = Math.ceil(max / cols);
  const gx = 0.7;
  const gy = 2.0;
  const gap = 0.26;
  const cw = (W - 2 * gx - (cols - 1) * gap) / cols;
  const ch = (H - gy - 0.65 - (rows - 1) * gap) / rows;
  cards.forEach((c, i) => {
    const x = gx + (i % cols) * (cw + gap);
    const y = gy + Math.floor(i / cols) * (ch + gap);
    drawCard(slide, x, y, cw, ch, th);
    drawIconCircle(slide, x + 0.24, y + 0.2, c.icon ?? ICON_POOL[i % ICON_POOL.length]!, th, i);
    tx(slide, c.title, {
      x: x + 1.0, y: y + 0.22, w: cw - 1.1, h: 0.4,
      fontSize: cols >= 4 ? 12.5 : 14, bold: true, color: p.title,
    });
    if (c.desc) {
      // y+0.95：图标圆底部（y+0.82）之下留 0.13 间隙，修复文字与圆圈重叠
      tx(slide, c.desc, {
        x: x + 0.28, y: y + 0.95, w: cw - 0.5, h: ch - 1.15,
        fontSize: cols >= 4 ? 10 : 10.5, color: p.muted,
        lineSpacingMultiple: 1.22, valign: 'top',
      });
    }
  });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderQuote(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  tx(slide, '“', { x: 0.5, y: 0.55, w: 1.6, h: 1.6, fontSize: 100, bold: true, color: th.accent, transparency: 45 });
  const q = s.quote ?? { text: s.content ?? s.title ?? '' };
  tx(slide, q.text, {
    x: 2.0, y: 2.4, w: 9.3, h: 2.4, fontSize: 28, italic: true, color: p.title,
    align: 'center', lineSpacingMultiple: 1.5, valign: 'middle',
  });
  if (q.author) {
    slide.addShape('rect', { x: W / 2 - 0.45, y: 5.15, w: 0.9, h: 0.04, fill: { color: th.accent } });
    tx(slide, `—— ${q.author}`, { x: 3, y: 5.35, w: 7.3, h: 0.45, fontSize: 14, color: th.textMuted, align: 'center' });
  }
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderCompare(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  decorateTitle(slide, s, th, ctx);
  const cmp = s.compare ?? { leftLabel: '', leftPoints: [], rightLabel: '', rightPoints: [] };
  // 兜底：compare 数据缺失时按 content 页渲染 bullets（页面永不空）
  if (!s.compare || (cmp.leftPoints.length === 0 && cmp.rightPoints.length === 0)) {
    const bl = s.bullets ?? [];
    if (bl.length === 0 && s.content) {
      tx(slide, s.content, { x: 0.9, y: 2.0, w: 11.5, h: 4.4, fontSize: 16, color: p.body, lineSpacingMultiple: 1.5, valign: 'top' });
    } else if (bl.length > 0) {
      const yBase = s.subtitle ? 2.55 : 2.15;
      bl.slice(0, 5).forEach((b, i) => {
        const y = yBase + i * 0.85;
        const bi = bulletIcon(b, i);
        drawIconCircle(slide, 0.95, y, bi.icon, th, i);
        tx(slide, bi.text, { x: 1.8, y: y + 0.08, w: 10.6, h: 0.5, fontSize: 15.5, color: p.body, valign: 'middle' });
      });
    }
    decorateProgress(slide, ctx.page, ctx.total, th);
    return;
  }
  // 左面板
  drawCard(slide, 0.65, 2.0, 5.5, 4.55, th);
  slide.addShape('rect', { x: 0.65, y: 2.0, w: 5.5, h: 0.62, fill: { color: p.title } });
  tx(slide, cmp.leftLabel, { x: 0.65, y: 2.05, w: 5.5, h: 0.52, fontSize: 15, bold: true, color: 'FFFFFF', align: 'center' });
  cmp.leftPoints.slice(0, 5).forEach((pt, i) => {
    const y = 2.85 + i * 0.72;
    tx(slide, '✕', { x: 1.0, y: y + 0.02, w: 0.35, h: 0.4, fontSize: 13, bold: true, color: th.dark ? 'FF8A80' : 'C0392B', align: 'center' });
    tx(slide, pt, { x: 1.45, y, w: 4.5, h: 0.55, fontSize: 12.5, color: p.body, valign: 'middle' });
  });
  // 右面板
  drawCard(slide, 7.2, 2.0, 5.5, 4.55, th);
  slide.addShape('rect', { x: 7.2, y: 2.0, w: 5.5, h: 0.62, fill: { color: th.accent } });
  tx(slide, cmp.rightLabel, { x: 7.2, y: 2.05, w: 5.5, h: 0.52, fontSize: 15, bold: true, color: 'FFFFFF', align: 'center' });
  cmp.rightPoints.slice(0, 5).forEach((pt, i) => {
    const y = 2.85 + i * 0.72;
    tx(slide, '✓', { x: 7.55, y: y + 0.02, w: 0.35, h: 0.4, fontSize: 13, bold: true, color: th.dark ? '69F0AE' : '27AE60', align: 'center' });
    tx(slide, pt, { x: 8.0, y, w: 4.5, h: 0.55, fontSize: 12.5, color: p.body, valign: 'middle' });
  });
  // 中央 VS 徽章（文本框与圆完全重合）
  slide.addShape('ellipse', { x: 6.19, y: 3.75, w: 0.95, h: 0.95, fill: { color: th.bgDark }, line: { color: th.accent, width: 1.75 } });
  tx(slide, 'VS', { x: 6.19, y: 3.75, w: 0.95, h: 0.95, fontSize: 18, bold: true, color: 'FFFFFF', align: 'center', valign: 'middle', margin: 0 });
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderBigNumber(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateHero(slide, th);
  tx(slide, s.title ?? '', { x: 0.7, y: 0.7, w: 11.9, h: 0.7, fontSize: 24, bold: true, color: p.title, align: 'center' });
  const metrics = (s.metrics ?? []).slice(0, 3);
  // 兜底：metrics 为空时渲染 bullets/content（页面永不空）
  if (metrics.length === 0) {
    const bl = s.bullets ?? [];
    if (bl.length === 0 && s.content) {
      tx(slide, s.content, { x: 0.9, y: 2.0, w: 11.5, h: 4.4, fontSize: 16, color: p.body, lineSpacingMultiple: 1.5, valign: 'top' });
    } else if (bl.length > 0) {
      bl.slice(0, 5).forEach((b, i) => {
        const y = 2.1 + i * 0.85;
        const bi = bulletIcon(b, i);
        drawIconCircle(slide, 0.95, y, bi.icon, th, i);
        tx(slide, bi.text, { x: 1.8, y: y + 0.08, w: 10.6, h: 0.5, fontSize: 15.5, color: p.body, valign: 'middle' });
      });
    }
    return;
  }
  const mw = 11.9 / Math.max(metrics.length, 1);
  const basePt = metricBasePt(metrics.length);
  metrics.forEach((m, i) => {
    const x = 0.7 + i * mw;
    // 字号自适应：基准按栏数取（3栏76/4栏60），超宽自动缩；
    // 极端长值缩到下限仍超 → 去掉 wrap:false 允许两行（高度足够 1.9 英寸）
    const fs = fitFontSize(m.value, mw - 0.25, basePt);
    const tooLong = measureText(m.value, fs) > mw - 0.25;
    tx(slide, m.value, {
      x, y: 2.0, w: mw, h: 1.9, fontSize: fs, bold: true, color: th.accent, align: 'center',
      wrap: tooLong ? true : false,
    });
    tx(slide, m.label, { x, y: 4.05, w: mw, h: 0.5, fontSize: 17, bold: true, color: p.title, align: 'center' });
    if (m.desc) {
      tx(slide, m.desc, { x: x + 0.4, y: 4.65, w: mw - 0.8, h: 0.85, fontSize: 12, color: p.muted, align: 'center', lineSpacingMultiple: 1.3 });
    }
    if (i < metrics.length - 1) {
      slide.addShape('rect', { x: x + mw - 0.012, y: 2.3, w: 0.02, h: 2.3, fill: { color: p.divider } });
    }
  });
}

function renderChart(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  decorateTitle(slide, s, th, ctx);
  const chart = s.chart;
  // 兜底：chart 数据缺失时按 content 页渲染 bullets（页面永不空）
  if (!chart || !Array.isArray(chart.series) || chart.series.length === 0) {
    const bl = s.bullets ?? [];
    if (bl.length === 0 && s.content) {
      tx(slide, s.content, { x: 0.9, y: 2.0, w: 11.5, h: 4.4, fontSize: 16, color: p.body, lineSpacingMultiple: 1.5, valign: 'top' });
    } else if (bl.length > 0) {
      const yBase = s.subtitle ? 2.55 : 2.15;
      bl.slice(0, 5).forEach((b, i) => {
        const y = yBase + i * 0.85;
        const bi = bulletIcon(b, i);
        drawIconCircle(slide, 0.95, y, bi.icon, th, i);
        tx(slide, bi.text, { x: 1.8, y: y + 0.08, w: 10.6, h: 0.5, fontSize: 15.5, color: p.body, valign: 'middle' });
      });
    }
    decorateProgress(slide, ctx.page, ctx.total, th);
    return;
  }
  const typeMap: Record<string, 'bar' | 'pie' | 'line'> = { bar: 'bar', pie: 'pie', line: 'line' };
  const ctype = typeMap[chart.type] ?? 'bar';
  const data = chart.series.map((se) => ({ name: se.name ?? '系列', labels: se.labels, values: se.values }));
  (slide as any).addChart(ctype, data, {
    x: 1.0, y: 2.0, w: 11.3, h: 4.3,
    chartColors: [th.accent, liftOnDark(th.primary), th.secondary, '70AD47', 'FFC000', 'ED7D31', 'A5A5A5'],
    showLegend: data.length > 1 || ctype === 'pie',
    legendPos: 'b',
    showValue: ctype !== 'line',
    title: chart.chartTitle ?? '',
    titleFontSize: 11,
    // 字体全覆盖：pptxgenjs 图表不继承全局 theme，必须逐项显式指定，
    // 否则坐标轴/图例/数据标签（"一季度"等）全部回退默认字体
    titleFontFace: FONT,
    legendFontFace: FONT,
    catAxisLabelFontFace: FONT,
    valAxisLabelFontFace: FONT,
    dataLabelFontFace: FONT,
    catAxisLabelFontSize: 12,
    valAxisLabelFontSize: 11,
    dataLabelFontSize: 11,
  });
  if (s.bullets?.length) {
    slide.addText(
      s.bullets.map((b) => ({ text: b, options: { bullet: true } })),
      { fontFace: FONT, x: 1.0, y: 6.45, w: 11.3, h: 0.55, fontSize: 11, color: p.muted, lineSpacingMultiple: 1.15 }
    );
  }
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderImageText(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateCorners(slide, th);
  decorateTitle(slide, s, th, ctx);
  const imgLeft = (s.image?.side ?? 'left') === 'left';
  const imgX = imgLeft ? 0.7 : 7.9;
  const txtX = imgLeft ? 6.8 : 0.7;
  // 图片占位框（深浅自适应色块 + 图标）
  const frameFill = th.dark
    ? { color: mixHex(th.bgDark, th.accent, 0.1), transparency: 20 }
    : { color: lighten(th.secondary, 0.75), transparency: 25 };
  slide.addShape('roundRect', {
    x: imgX, y: 2.0, w: 5.5, h: 4.5, rectRadius: 0.09,
    fill: frameFill,
    line: { color: p.title, width: 1 },
  });
  tx(slide, '🖼', { x: imgX + 1.0, y: 3.1, w: 3.5, h: 1.3, fontSize: 52, align: 'center' });
  if (s.image?.caption) {
    tx(slide, s.image.caption, { x: imgX + 0.4, y: 4.7, w: 4.7, h: 0.45, fontSize: 12, color: p.muted, align: 'center' });
  }
  // 文字区
  if (s.bullets?.length) {
    s.bullets.slice(0, 5).forEach((b, i) => {
      const y = 2.1 + i * 0.88;
      const bi = bulletIcon(b, i);
      drawIconCircle(slide, txtX, y, bi.icon, th, i);
      tx(slide, bi.text, { x: txtX + 0.82, y: y + 0.08, w: 5.0, h: 0.5, fontSize: 14, color: p.body, valign: 'middle' });
    });
  } else if (s.content) {
    tx(slide, s.content, { x: txtX, y: 2.1, w: 5.8, h: 4.3, fontSize: 14, color: p.body, lineSpacingMultiple: 1.5, valign: 'top' });
  }
  decorateProgress(slide, ctx.page, ctx.total, th);
}

function renderClosing(slide: any, s: SlideSpec, th: SlideTheme, ctx: RenderCtx): void {
  const p = pal(th);
  slide.background = { color: s.background ?? p.bg };
  decorateHero(slide, th);
  tx(slide, s.title ?? '谢谢观看', {
    x: 0.7, y: 2.7, w: 11.9, h: 1.2, fontSize: 46, bold: true,
    color: th.dark ? 'FFFFFF' : p.title, align: 'center',
  });
  if (s.subtitle ?? s.bullets?.length) {
    tx(slide, s.subtitle ?? s.bullets!.join('　'), {
      x: 0.7, y: 4.1, w: 11.9, h: 0.55, fontSize: 16, color: p.muted, align: 'center',
    });
  }
  slide.addShape('rect', { x: W / 2 - 0.6, y: 4.95, w: 1.2, h: 0.055, fill: { color: th.accent } });
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
  'grid-3x2': (slide, s, th, ctx) => renderGridN(slide, s, th, ctx, 3, 6),
  'grid-4x2': (slide, s, th, ctx) => renderGridN(slide, s, th, ctx, 4, 8),
  quote: renderQuote,
  compare: renderCompare,
  'big-number': renderBigNumber,
  chart: renderChart,
  'image-text': renderImageText,
  closing: renderClosing,
};

/** 自动讲者备注：LLM 未写 notes 时按页面结构生成一句话讲稿提示，
 *  保证 100% 页面带备注（对齐原版 workbuddy 10/10 页有备注）。 */
function autoNotes(s: SlideSpec): string {
  if (s.notes) return s.notes;
  const t = s.title ?? '本页';
  if (s.compare) {
    return `对比讲解「${t}」：${s.compare.leftLabel} vs ${s.compare.rightLabel}，逐条对比突出差异，可举例说明。`;
  }
  if (s.metrics?.length) {
    return `用数据说话：${s.metrics.map((m) => `${m.label}${m.value}`).join('、')}，逐个解读数字背后的含义。`;
  }
  if (s.cards?.length) {
    return `重点讲解「${t}」：${s.cards.map((c) => c.title).join('、')}。每项展开一句具体例子。`;
  }
  if (s.steps?.length) {
    return `按顺序讲解「${t}」：${s.steps.map((x) => x.label).join(' → ')}，边讲边演示。`;
  }
  if (s.layout === 'toc' && s.items?.length) {
    return `带读目录：${s.items.map((x) => x.label).join('、')}，说明课程脉络与时长安排。`;
  }
  if (s.bullets?.length) {
    return `重点强调「${t}」：${s.bullets.slice(0, 3).join('；')}。`;
  }
  if (s.layout === 'cover') return `开场页：介绍「${t}」，点明课程目标与受众，1 分钟内完成。`;
  if (s.layout === 'closing') return `收尾页：回顾三要点，布置练习，感谢聆听并进入答疑。`;
  return `讲解「${t}」。`;
}

/** 渲染全部幻灯片到 pptx 实例（不落盘，由调用方写文件）。
 *  画布 13.333×7.5（与原版 workbuddy 一致的标准 16:9 全幅）。 */
export function renderSlidesTo(pptx: any, slides: SlideSpec[], theme: SlideTheme): void {
  const total = slides.length;
  slides.forEach((s, i) => {
    const slide = pptx.addSlide();
    const layout = s.layout ?? 'content';
    const renderer = RENDERERS[layout] ?? renderContent;
    renderer(slide, s, theme, { page: i + 1, total });
    slide.addNotes(autoNotes(s));
  });
}
