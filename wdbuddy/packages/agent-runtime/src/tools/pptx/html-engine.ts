/**
 * HTML 幻灯片引擎（第二阶核心）—— 单文件 HTML、CSS 变量主题、
 * 键盘/滚轮翻页、入场动画、玻璃拟态、进度条。
 * 写 HTML/CSS 是 LLM 最强的能力域，视觉上限远超 pptx 模板。
 */
import type { SlideTheme } from './themes.js';
import type { SlideSpec } from './pptx-engine.js';

function esc(t: string): string {
  return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** 主题 → CSS 变量（键名与 themes 字段一一映射，加 # 前缀）。
 *  字体链微软雅黑置顶（用户指定：其他字体渲染效果不佳），主题原字体仅作回退。
 *  dark 主题：--bg-light/--text-dark 等浅色变量重映射为深色系，内容页与封面全篇统一 */
function themeCssVars(th: SlideTheme): string {
  if (th.dark) {
    const mix = (a: string, b: string, r: number): string => {
      const n = (h: string): [number, number, number] => {
        const s = h.replace('#', '');
        const f = s.length === 3 ? s.split('').map((c) => c + c).join('') : s;
        return [parseInt(f.slice(0, 2), 16), parseInt(f.slice(2, 4), 16), parseInt(f.slice(4, 6), 16)];
      };
      const [r1, g1, b1] = n(a);
      const [r2, g2, b2] = n(b);
      return [r1 + (r2 - r1) * r, g1 + (g2 - g1) * r, b1 + (b2 - b1) * r]
        .map((c) => Math.round(c).toString(16).padStart(2, '0'))
        .join('');
    };
    const lift = (h: string): string => {
      const s = h.replace('#', '');
      const f = s.length === 3 ? s.split('').map((c) => c + c).join('') : s;
      const r = parseInt(f.slice(0, 2), 16);
      const g = parseInt(f.slice(2, 4), 16);
      const b = parseInt(f.slice(4, 6), 16);
      return 0.299 * r + 0.587 * g + 0.114 * b < 100 ? mix(h, 'FFFFFF', 0.45) : h;
    };
    return `:root{
  --primary:#${lift(th.primary)};--secondary:#${th.secondary};--accent:#${th.accent};
  --bg-dark:#${th.bgDark};--bg-light:#${th.bgDark};
  --text-dark:#${th.textLight};--text-light:#${th.textLight};
  --text-muted:#${th.textMuted};--card-bg:#${mix(th.bgDark, 'FFFFFF', 0.07)};
  --glass-bg:rgba(255,255,255,.08);--glass-line:rgba(255,255,255,.22);
  --font-title:"Microsoft YaHei",${th.fontTitle};--font-body:"Microsoft YaHei",${th.fontBody};
}`;
  }
  return `:root{
  --primary:#${th.primary};--secondary:#${th.secondary};--accent:#${th.accent};
  --bg-dark:#${th.bgDark};--bg-light:#${th.bgLight};
  --text-dark:#${th.textDark};--text-light:#${th.textLight};
  --text-muted:#${th.textMuted};--card-bg:#${th.cardBg};
  --font-title:"Microsoft YaHei",${th.fontTitle};--font-body:"Microsoft YaHei",${th.fontBody};
}`;
}

/* ───────────── 单页 HTML 渲染 ───────────── */

/** 通用 N 列网格（grid-3x2 六卡 / grid-4x2 八卡） */
function gridNHtml(s: SlideSpec, cols: number, max: number): string {
  const cards = (s.cards ?? []).slice(0, max);
  return `
      <h2 class="page-title">${esc(s.title ?? '')}</h2><div class="title-underline"></div>
      <div class="grid-n" style="grid-template-columns:repeat(${cols},1fr)">${cards
        .map(
          (c, i) => `
        <div class="grid-item"><span class="grid-badge">${c.icon ? esc(c.icon) : i + 1}</span>
          <div class="grid-title">${esc(c.title)}</div>
          ${c.desc ? `<div class="grid-desc">${esc(c.desc)}</div>` : ''}</div>`
        )
        .join('')}</div>`;
}

function slideHtml(s: SlideSpec, idx: number, total: number, th: SlideTheme): string {
  const layout = s.layout ?? 'content';
  const inner: Record<string, () => string> = {
    cover: () => `
      <div class="hero-deco deco-1"></div><div class="hero-deco deco-2"></div><div class="hero-deco deco-3"></div>
      <h1 class="hero-title">${esc(s.title ?? '')}</h1>
      ${s.subtitle ?? s.bullets?.length ? `<p class="hero-sub">${esc(s.subtitle ?? s.bullets!.join('　'))}</p>` : ''}
      <div class="hero-line"></div>`,
    toc: () => `
      <div class="side-bar"></div>
      <h2 class="page-title">${esc(s.title ?? '目录')}</h2>
      <ol class="toc-list">
        ${(((s.items ?? s.bullets?.map((b) => ({ label: b }))) as Array<{ label: string; desc?: string }>) ?? []).slice(0, 8).map((it, i) => `
          <li><span class="toc-num">${String(i + 1).padStart(2, '0')}</span>
              <div><div class="toc-label">${esc(it.label)}</div>${it.desc ? `<div class="toc-desc">${esc(it.desc)}</div>` : ''}</div></li>`).join('')}
      </ol>`,
    section: () => `
      <div class="section-num">${esc(s.subtitle ?? '')}</div>
      <h2 class="section-title">${esc(s.title ?? '')}</h2>
      <div class="section-line"></div>
      ${s.bullets?.length ? `<p class="section-sub">${esc(s.bullets.join('　'))}</p>` : ''}`,
    content: () => `
      <h2 class="page-title">${esc(s.title ?? '')}</h2><div class="title-underline"></div>
      <ul class="bullet-list">${(s.bullets ?? []).map((b) => `<li>${esc(b)}</li>`).join('') || (s.content ? `<li>${esc(s.content)}</li>` : '')}</ul>`,
    'two-col': () => `
      <h2 class="page-title">${esc(s.title ?? '')}</h2><div class="title-underline"></div>
      <div class="two-col">
        <ul class="bullet-list">${(s.bullets ?? []).map((b) => `<li>${esc(b)}</li>`).join('')}</ul>
        <div class="col-divider"></div>
        <div class="stack">${(s.items ?? []).slice(0, 4).map((it) => `
          <div class="mini-card"><div class="mini-title">${esc(it.label)}</div>${it.desc ? `<div class="mini-desc">${esc(it.desc)}</div>` : ''}</div>`).join('')}</div>
      </div>`,
    cards: () => `
      <h2 class="page-title">${esc(s.title ?? '')}</h2><div class="title-underline"></div>
      <div class="cards-row">${(s.cards ?? []).slice(0, 3).map((c, i) => `
        <div class="glass-card ${i === 1 ? 'card-accent' : ''}">
          <div class="card-icon">${esc(c.icon ?? String(i + 1))}</div>
          <div class="card-title">${esc(c.title)}</div>
          ${c.desc ? `<div class="card-desc">${esc(c.desc)}</div>` : ''}
        </div>`).join('')}</div>`,
    timeline: () => `
      <h2 class="page-title">${esc(s.title ?? '')}</h2><div class="title-underline"></div>
      <div class="timeline">
        ${(((s.steps ?? s.items) ?? []) as Array<{ label: string; desc?: string }>).slice(0, 5).map((st) => `
          <div class="tl-node"><div class="tl-dot"></div><div class="tl-label">${esc(st.label)}</div>${st.desc ? `<div class="tl-desc">${esc(st.desc)}</div>` : ''}</div>`).join('')}
      </div>`,
    'grid-2x2': () => `
      <h2 class="page-title">${esc(s.title ?? '')}</h2><div class="title-underline"></div>
      <div class="grid-2x2">${((s.cards ?? s.items?.map((it) => ({ title: it.label, desc: it.desc }))) as Array<{ title: string; desc?: string }>).slice(0, 4).map((c, i) => `
        <div class="grid-item"><span class="grid-badge">${i + 1}</span>
          <div class="grid-title">${esc(c.title)}</div>
          ${c.desc ? `<div class="grid-desc">${esc(c.desc)}</div>` : ''}</div>`).join('')}</div>`,
    quote: () => `
      <div class="quote-mark">“</div>
      <blockquote class="quote-text">${esc(s.quote?.text ?? s.content ?? s.title ?? '')}</blockquote>
      ${s.quote?.author ? `<div class="quote-line"></div><div class="quote-author">—— ${esc(s.quote.author)}</div>` : ''}`,
    compare: () => `
      <h2 class="page-title">${esc(s.title ?? '')}</h2><div class="title-underline"></div>
      <div class="compare">
        <div class="cmp-panel"><div class="cmp-head head-left">${esc(s.compare?.leftLabel ?? '')}</div>
          <ul class="bullet-list">${(s.compare?.leftPoints ?? []).map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>
        <div class="cmp-vs">VS</div>
        <div class="cmp-panel"><div class="cmp-head head-right">${esc(s.compare?.rightLabel ?? '')}</div>
          <ul class="bullet-list">${(s.compare?.rightPoints ?? []).map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>
      </div>`,
    'big-number': () => `
      <div class="hero-deco deco-1"></div><div class="hero-deco deco-2"></div>
      <h2 class="page-title on-dark">${esc(s.title ?? '')}</h2>
      <div class="metrics-row">${(s.metrics ?? []).slice(0, 3).map((m) => `
        <div class="metric"><div class="metric-value">${esc(m.value)}</div>
          <div class="metric-label">${esc(m.label)}</div>${m.desc ? `<div class="metric-desc">${esc(m.desc)}</div>` : ''}</div>`).join('')}</div>`,
    chart: () => `
      <h2 class="page-title">${esc(s.title ?? '数据图表')}</h2><div class="title-underline"></div>
      <div class="chart-placeholder">
        <div class="chart-bars">${(s.chart?.series?.[0]?.labels ?? []).map((lb, i) => {
          const vals = s.chart?.series?.[0]?.values ?? [];
          const max = Math.max(...vals, 1);
          const h = Math.round(((vals[i] ?? 0) / max) * 100);
          return `<div class="bar-col"><div class="bar" style="height:${h}%"><span class="bar-val">${vals[i] ?? ''}</span></div><span class="bar-label">${esc(lb)}</span></div>`;
        }).join('')}</div>
        ${s.chart?.type !== 'bar' ? `<div class="chart-note">（${s.chart?.type === 'pie' ? '饼图' : '折线图'}：请生成 .pptx 版本查看原生可编辑图表，或在浏览器中补充）</div>` : ''}
      </div>
      ${s.bullets?.length ? `<ul class="chart-points">${s.bullets.map((b) => `<li>${esc(b)}</li>`).join('')}</ul>` : ''}`,
    'image-text': () => `
      <h2 class="page-title">${esc(s.title ?? '')}</h2><div class="title-underline"></div>
      <div class="image-text ${s.image?.side === 'right' ? 'img-right' : ''}">
        <div class="img-frame"><span class="img-emoji">🖼</span>${s.image?.caption ? `<span class="img-caption">${esc(s.image.caption)}</span>` : ''}</div>
        <div class="img-text"><ul class="bullet-list">${(s.bullets ?? []).map((b) => `<li>${esc(b)}</li>`).join('') || (s.content ? `<li>${esc(s.content)}</li>` : '')}</ul></div>
      </div>`,
    closing: () => `
      <div class="hero-deco deco-1"></div><div class="hero-deco deco-2"></div><div class="hero-deco deco-3"></div>
      <h1 class="hero-title">${esc(s.title ?? '谢谢观看')}</h1>
      ${s.subtitle ?? s.bullets?.length ? `<p class="hero-sub">${esc(s.subtitle ?? s.bullets!.join('　'))}</p>` : ''}
      <div class="hero-line"></div>`,
  };
  const dark = ['cover', 'closing', 'big-number'].includes(layout);
  return `  <section class="slide ${dark ? 'slide-dark' : 'slide-light'}" data-idx="${idx}">
    <div class="slide-inner">${(inner[layout] ?? inner['content']!)()}</div>
    <div class="progress"><div class="progress-fill" style="width:${((idx + 1) / total) * 100}%"></div></div>
    <div class="page-num">${idx + 1} / ${total}</div>
  </section>`;
}

/* ───────────── 完整 HTML 文档 ───────────── */

export function buildHtmlDeck(title: string, slides: SlideSpec[], th: SlideTheme): string {
  const total = slides.length;
  const sections = slides.map((s, i) => slideHtml(s, i, total, th)).join('\n');
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${esc(title)}</title>
<style>
${themeCssVars(th)}
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;background:#111;font-family:var(--font-body);overflow:hidden}
.deck{height:100vh;display:flex;flex-direction:column}
.slide{flex:1;display:none;position:relative;overflow:hidden}
.slide.active{display:block;animation:slideIn .45s cubic-bezier(.22,.68,.35,1.04)}
@keyframes slideIn{from{opacity:0;transform:translateY(18px) scale(.985)}to{opacity:1;transform:none}}
.slide-inner{position:absolute;inset:0;padding:56px 72px 64px}
.slide-light{background:var(--bg-light)}
.slide-dark{background:linear-gradient(135deg,var(--bg-dark) 0%,color-mix(in srgb,var(--bg-dark) 80%,var(--secondary)) 100%)}
/* 通用页头 */
.page-title{font-family:var(--font-title);font-size:34px;font-weight:700;color:var(--primary);margin-bottom:6px}
.page-title.on-dark{color:var(--text-light)}
.title-underline{width:88px;height:5px;background:linear-gradient(90deg,var(--primary) 0 60%,var(--accent) 60% 100%);border-radius:3px;margin-bottom:26px}
/* 要点 */
.bullet-list{list-style:none;display:flex;flex-direction:column;gap:14px}
.bullet-list li{position:relative;padding-left:26px;font-size:19px;color:var(--text-dark);line-height:1.55}
.bullet-list li::before{content:'';position:absolute;left:2px;top:11px;width:9px;height:9px;border-radius:50%;background:var(--accent)}
/* 目录 */
.side-bar{position:absolute;left:0;top:0;bottom:0;width:10px;background:linear-gradient(180deg,var(--primary),var(--secondary))}
.toc-list{list-style:none;display:flex;flex-direction:column;gap:20px;margin-top:26px;max-width:800px}
.toc-list li{display:flex;align-items:center;gap:20px;padding-bottom:16px;border-bottom:1px solid rgba(0,0,0,.07)}
.toc-num{font-family:var(--font-title);font-size:30px;font-weight:800;color:var(--accent);min-width:52px}
.toc-label{font-size:21px;font-weight:600;color:var(--text-dark)}
.toc-desc{font-size:13px;color:var(--text-muted);margin-top:3px}
/* 章节页 */
.section-num{font-family:var(--font-title);font-size:110px;font-weight:800;color:var(--accent);opacity:.9;line-height:1}
.section-title{font-family:var(--font-title);font-size:42px;font-weight:700;color:var(--primary);margin-top:8px}
.section-line{width:70px;height:6px;background:var(--primary);border-radius:3px;margin:22px 0 14px}
.section-sub{font-size:15px;color:var(--text-muted);letter-spacing:1px}
/* 双栏 */
.two-col{display:grid;grid-template-columns:1fr 1px 1.05fr;gap:32px;height:70%}
.col-divider{background:rgba(0,0,0,.09)}
.stack{display:flex;flex-direction:column;gap:14px}
.mini-card{background:var(--card-bg);border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:14px 18px;box-shadow:0 2px 10px rgba(0,0,0,.05)}
.mini-title{font-size:16px;font-weight:700;color:var(--primary)}
.mini-desc{font-size:12.5px;color:var(--text-muted);margin-top:4px}
/* 卡片行 */
.cards-row{display:grid;grid-template-columns:repeat(3,1fr);gap:28px;height:68%}
.glass-card{background:var(--glass-bg,rgba(255,255,255,.6));backdrop-filter:blur(14px);border:1px solid var(--glass-line,rgba(0,0,0,.08));border-radius:18px;padding:34px 26px;display:flex;flex-direction:column;align-items:center;gap:14px;box-shadow:0 12px 34px rgba(0,0,0,.09);transition:transform .25s,box-shadow .25s}
.glass-card:hover{transform:translateY(-6px);box-shadow:0 18px 44px rgba(0,0,0,.14)}
.card-accent{background:linear-gradient(160deg,rgba(255,255,255,.75),color-mix(in srgb,var(--accent) 16%,white));border-top:4px solid var(--accent)}
.card-icon{font-size:44px}
.card-title{font-family:var(--font-title);font-size:20px;font-weight:700;color:var(--primary)}
.card-desc{font-size:13.5px;color:var(--text-muted);line-height:1.6;text-align:center}
/* 时间线 */
.timeline{display:flex;justify-content:space-between;position:relative;margin-top:60px;padding:0 30px}
.timeline::before{content:'';position:absolute;left:30px;right:30px;top:7px;height:3px;background:rgba(0,0,0,.12);border-radius:2px}
.tl-node{display:flex;flex-direction:column;align-items:center;width:150px;gap:10px}
.tl-dot{width:17px;height:17px;border-radius:50%;background:#fff;border:3px solid var(--primary);position:relative;z-index:1}
.tl-dot::after{content:'';position:absolute;inset:4px;border-radius:50%;background:var(--accent)}
.tl-label{font-size:15.5px;font-weight:700;color:var(--primary)}
.tl-desc{font-size:11.5px;color:var(--text-muted);text-align:center;line-height:1.45}
/* 网格 */
.grid-2x2{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:22px;height:68%}
.grid-n{display:grid;grid-template-rows:auto;gap:16px;height:68%;align-items:stretch}
.grid-n .grid-item{flex-direction:column;gap:10px;padding:18px}
.grid-n .grid-badge{font-size:22px;width:auto;height:auto;background:none;border-radius:0;padding:0}
.grid-n .grid-title{font-size:15.5px}
.grid-n .grid-desc{font-size:12px}
.grid-item{background:var(--card-bg);border:1px solid rgba(0,0,0,.07);border-radius:14px;padding:20px 22px;display:flex;gap:16px;box-shadow:0 3px 14px rgba(0,0,0,.05)}
.grid-badge{flex:none;width:34px;height:34px;border-radius:50%;background:var(--primary);color:#fff;font-weight:800;font-size:16px;display:flex;align-items:center;justify-content:center}
.grid-title{font-size:16.5px;font-weight:700;color:var(--primary)}
.grid-desc{font-size:12.5px;color:var(--text-muted);margin-top:5px;line-height:1.5}
/* 金句 */
.quote-mark{font-family:Georgia,serif;font-size:170px;color:var(--accent);opacity:.4;line-height:.6;margin:70px 0 0 90px}
.quote-text{font-family:var(--font-title);font-size:31px;font-style:italic;color:var(--primary);text-align:center;line-height:1.7;max-width:820px;margin:0 auto;padding:0 40px}
.quote-line{width:52px;height:3px;background:var(--accent);margin:28px auto 12px}
.quote-author{font-size:15px;color:var(--text-muted);text-align:center}
/* 对比 */
.compare{display:grid;grid-template-columns:1fr 70px 1fr;align-items:stretch;gap:0;height:68%}
.cmp-panel{background:var(--card-bg);border:1px solid rgba(0,0,0,.08);border-radius:14px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,.06)}
.cmp-head{padding:13px;font-size:16px;font-weight:700;color:#fff;text-align:center}
.head-left{background:var(--primary)}
.head-right{background:var(--accent)}
.cmp-panel .bullet-list{padding:20px 24px;gap:11px}
.cmp-panel .bullet-list li{font-size:14.5px}
.cmp-vs{align-self:center;justify-self:center;width:54px;height:54px;border-radius:50%;background:var(--bg-dark);color:#fff;font-weight:800;font-size:15px;display:flex;align-items:center;justify-content:center;border:2px solid var(--accent);z-index:1}
/* 数据大屏 */
.metrics-row{display:flex;justify-content:center;gap:70px;margin-top:70px}
.metric{text-align:center}
.metric-value{font-family:var(--font-title);font-size:76px;font-weight:800;color:var(--accent);line-height:1.1;text-shadow:0 0 40px color-mix(in srgb,var(--accent) 45%,transparent)}
.metric-label{font-size:19px;font-weight:700;color:var(--text-light);margin-top:10px}
.metric-desc{font-size:12.5px;color:var(--text-muted);margin-top:6px;max-width:220px}
/* 图表（纯 CSS 柱状） */
.chart-placeholder{height:58%;display:flex;flex-direction:column;align-items:center;justify-content:flex-end}
.chart-bars{display:flex;align-items:flex-end;gap:38px;height:85%;width:78%;border-bottom:2px solid rgba(0,0,0,.14);padding:0 20px}
.bar-col{display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;flex:1}
.bar{width:70%;max-width:86px;background:linear-gradient(180deg,var(--accent),var(--primary));border-radius:6px 6px 0 0;position:relative;animation:barGrow .8s ease-out backwards}
.bar:nth-child(2){animation-delay:.1s}.bar:nth-child(3){animation-delay:.2s}.bar:nth-child(4){animation-delay:.3s}
@keyframes barGrow{from{height:0 !important}}
.bar-val{position:absolute;top:-24px;left:50%;transform:translateX(-50%);font-size:13px;font-weight:700;color:var(--primary)}
.bar-label{margin-top:9px;font-size:13px;color:var(--text-muted)}
.chart-note{font-size:12px;color:var(--text-muted);margin-top:14px}
.chart-points{margin-top:16px;list-style:none;display:flex;gap:26px}
.chart-points li{font-size:12px;color:var(--text-muted)}
.chart-points li::before{content:'●';color:var(--accent);margin-right:6px;font-size:8px}
/* 图文 */
.image-text{display:grid;grid-template-columns:1fr 1.1fr;gap:34px;height:68%}
.image-text.img-right{grid-template-columns:1.1fr 1fr}
.image-text.img-right .img-frame{order:2}
.img-frame{border-radius:16px;background:linear-gradient(150deg,color-mix(in srgb,var(--secondary) 32%,transparent),color-mix(in srgb,var(--primary) 22%,transparent));border:1.5px solid color-mix(in srgb,var(--primary) 45%,transparent);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px}
.img-emoji{font-size:56px}
.img-caption{font-size:12.5px;color:var(--text-muted)}
/* 封面/结尾 */
.hero-title{font-family:var(--font-title);font-size:54px;font-weight:800;color:#fff;text-align:center;margin-top:15vh;text-shadow:0 4px 30px rgba(0,0,0,.3)}
.hero-sub{font-size:19px;color:var(--text-light);opacity:.85;text-align:center;margin-top:18px}
.hero-line{width:64px;height:5px;background:var(--accent);border-radius:3px;margin:30px auto 0}
.hero-deco{position:absolute;border-radius:50%;filter:blur(2px)}
.deco-1{width:340px;height:340px;right:-90px;top:-110px;background:var(--secondary);opacity:.35}
.deco-2{width:230px;height:230px;left:-70px;bottom:-60px;background:var(--accent);opacity:.22}
.deco-3{width:120px;height:120px;right:110px;bottom:70px;background:var(--primary);opacity:.4;border-radius:22px !important}
/* 进度条与页码 */
.progress{position:absolute;left:0;right:0;bottom:0;height:4px;background:rgba(0,0,0,.08)}
.slide-dark .progress{background:rgba(255,255,255,.1)}
.progress-fill{height:100%;background:var(--accent);transition:width .4s}
.page-num{position:absolute;right:22px;bottom:14px;font-size:11.5px;color:var(--text-muted)}
/* 底部提示 */
.hint{position:fixed;left:0;right:0;bottom:0;padding:6px;text-align:center;font-size:11px;color:rgba(255,255,255,.35);pointer-events:none;font-family:monospace}
@media (max-width:900px){.slide-inner{padding:34px 26px 50px}.page-title{font-size:26px}.hero-title{font-size:38px;margin-top:10vh}}
</style>
</head>
<body>
<div class="deck">
${sections}
</div>
<div class="hint">← → / Space / PgUp PgDn 翻页 · Home/End 首末页 · F 全屏</div>
<script>
(function(){
  var slides=[].slice.call(document.querySelectorAll('.slide'));
  var cur=0;
  function show(i){
    cur=Math.max(0,Math.min(slides.length-1,i));
    slides.forEach(function(s,j){s.classList.toggle('active',j===cur)});
    location.hash=''+(cur+1);
  }
  document.addEventListener('keydown',function(e){
    if(['ArrowRight','ArrowDown','PageDown',' ','Enter'].indexOf(e.key)>=0){e.preventDefault();show(cur+1)}
    else if(['ArrowLeft','ArrowUp','PageUp'].indexOf(e.key)>=0){e.preventDefault();show(cur-1)}
    else if(e.key==='Home'){show(0)}
    else if(e.key==='End'){show(slides.length-1)}
    else if(e.key==='f'||e.key==='F'){
      if(document.fullscreenElement){document.exitFullscreen()}else{document.documentElement.requestFullscreen()}
    }
  });
  document.addEventListener('wheel',function(e){
    if(Math.abs(e.deltaY)<24)return;
    show(cur+(e.deltaY>0?1:-1));
  },{passive:true});
  var sx=0;
  document.addEventListener('touchstart',function(e){sx=e.touches[0].clientX},{passive:true});
  document.addEventListener('touchend',function(e){
    var dx=e.changedTouches[0].clientX-sx;
    if(Math.abs(dx)>60)show(cur+(dx<0?1:-1));
  },{passive:true});
  var h=parseInt(location.hash.replace('#',''),10);
  show(isNaN(h)?0:h-1);
})();
</script>
</body>
</html>`;
}

