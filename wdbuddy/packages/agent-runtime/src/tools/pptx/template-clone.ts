/**
 * template-clone — 模板克隆引擎（路线B：完整复刻第三方 PPT 模板）
 *
 * 原理：不重建视觉，直接以模板页为画布——保留全部形状/配色/微立体效果，
 * 仅替换文字内容。三步：
 *  1. scanTemplatePages：解剖模板每页文本框 → 按几何/文本特征识别"槽位角色"
 *     （标题/副题/条目卡/正文/编号/页脚等）
 *  2. matchTemplatePage：LLM 内容稿的 SlideSpec → 匹配最佳模板页 + 生成替换计划
 *  3. applyClone：python-pptx 风格的 XML 级文本替换（保格式：只换 <a:t> 内容，
 *     字体/颜色/字号全部继承模板原值）
 *
 * 依赖 python（pptx 库）通过 shell 调用：Node 生态无成熟的 pptx 克隆写库，
 * python-pptx 是唯一工业级方案（跨进程一次完成全部替换，避免逐页多次调用）
 */
import type { SlideSpec } from './pptx-engine.js';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { execFileSync } from 'node:child_process';

/* ───────────── 1. 模板页扫描（Node 侧预扫描，供匹配） ───────────── */

export interface TemplateSlot {
  /** 文本框序号（python-pptx 顺序） */
  idx: number;
  x: number; y: number; w: number; h: number;
  paras: string[];
  role: string; // title | subtitle | item-title | item-body | number | footer | decor
}

export interface TemplatePage {
  page: number;
  kind: string; // cover | toc | section | content-3col | content-4col | closing
  slots: TemplateSlot[];
}

/** 模板页分类 + 槽位角色标注（纯几何/文本启发式，零 LLM） */
export function classifyTemplatePage(page: number, boxes: Array<{ idx: number; x: number; y: number; w: number; h?: number; paras: string[] }>): TemplatePage {
  const slots: TemplateSlot[] = boxes.map((b) => ({
    idx: b.idx, x: b.x, y: b.y, w: b.w, h: b.h ?? 0,
    paras: b.paras,
    role: guessRole(b),
  }));
  const kind = guessKind(page, slots);
  return { page, kind, slots };
}

function guessRole(b: { x: number; y: number; w: number; paras: string[] }): string {
  const first = b.paras[0] ?? '';
  const isLong = (b.paras[0] ?? '').length > 15;
  if (/^\d{2}$/.test(first)) return 'number';
  if (/^N\.\d+/.test(first)) return 'decor';
  if (/^(主讲人|汇报时间|时间)/.test(first)) return 'footer';
  if (/^(PART|THANKS)/i.test(first)) return 'decor';
  if (/^目\s*录$/.test(first.trim())) return 'title';
  if (/^CONTENTS$/i.test(first.trim())) return 'decor';
  if (/^\d{1,2}$/.test(first)) return 'number';
  // 日期大字槽（T7 大事件页 202X-03）：若当 body 填正文会产生 36pt 巨型日期
  // 淹没卡片文字；识别为 date 装饰，正文分流到卡片槽
  if (/^\d{2,4}X?[-/年]\d{1,2}([-/月]\d{1,2}日?)?$/.test(first.trim())) return 'date';
  if (/添加.*标题|点击.*标题/.test(first)) return 'item-title';
  if (/在这里输入|输入您的|此处输入/.test(first)) return 'item-body';
  if (/输入您的标题内容|标题文本标题/.test(first)) return isLong ? 'item-body' : 'item-title';
  if (/上市公司|关键词/.test(first)) return 'item-body';
  if (b.y < 1.2 && b.w > 3) return 'title';
  if (b.y < 1.2) return 'title';
  if (b.y < 2.0 && b.w > 5) return 'title';
  return 'body';
}

function guessKind(page: number, slots: TemplateSlot[]): string {
  const texts = slots.flatMap((s) => s.paras).join(' ');
  if (/THANKS/i.test(texts)) return 'closing';
  if (/目\s*录/.test(texts)) return 'toc';
  if (/PART ONE|PART TWO/i.test(texts) || slots.some((s) => s.paras[0] === '01' && s.y < 1)) return 'section';
  if (page <= 2) return 'cover';
  const itemTitles = slots.filter((s) => s.role === 'item-title').length;
  // 章节页：无标题槽/条目卡、含编号且槽位少（P4/P11/P18/P28："01/年度工作概述/JOB DESCRIPTION"）
  if (itemTitles === 0 && !slots.some((s) => s.role === 'title')) {
    const nNums = slots.filter((s) => s.role === 'number').length;
    if (nNums >= 1 && 6 >= slots.length) return 'section';
  }
  const numbers = slots.filter((s) => s.role === 'number').length;
  if (numbers >= 4) return 'content-4col';
  if (itemTitles >= 3) return 'content-3col';
  return 'content';
}

/* ───────────── 2. 内容 → 模板页匹配 ───────────── */

/** SlideSpec.layout → 模板页 kind 优先级映射 */
const LAYOUT_TO_KIND: Record<string, string[]> = {
  cover: ['cover'],
  closing: ['closing'],
  toc: ['toc'],
  section: ['section'],
  'grid-2x2': ['content-4col', 'content-3col', 'content'],
  'grid-3x2': ['content-3col', 'content-4col', 'content'],
  'grid-4x2': ['content-4col', 'content-3col', 'content'],
  cards: ['content-3col', 'content', 'content-4col'],
  timeline: ['content-3col', 'content-4col', 'content'],
  compare: ['content-4col', 'content-3col', 'content'],
  'big-number': ['content-4col', 'content-3col', 'content'],
  chart: ['content', 'content-3col'],
  'two-col': ['content-3col', 'content'],
  content: ['content', 'content-3col', 'content-4col'],
  quote: ['content', 'content-3col'],
  'image-text': ['content'],
};

export interface ClonePlan {
  templatePage: number;
  /** slotIdx → 新文本（多段用 \n 连接） */
  replacements: Record<number, string>;
  /** 需删除的槽位 idx（如 toc 多余条目组）：python 侧删除其所属顶层组合形状 */
  deletions?: number[];
  /** 槽位所属顶层形状的整体垂直平移（EMU）：条目组少于模板时重排居中 */
  shifts?: Array<{ idx: number; dy: number }>;
}

/** 生成克隆计划：按版式匹配模板页 + 槽位填充 */
export function planClone(spec: SlideSpec, pool: TemplatePage[], usedPages: Set<number>): ClonePlan | null {
  // 版式偏好动态修正：big-number 度量≤3 优先 3 列卡页；条目>3 优先 4 列卡页
  const baseWanted = LAYOUT_TO_KIND[spec.layout] ?? ['content'];
  // itemCount 纳入 bullets/metrics：3-4 条 bullets 的内容页同样需要卡槽
  // 数匹配（P3 事故：4 bullets 落 3 卡页 → 第 4 条被 slice 丢弃）
  const itemCount =
    spec.cards?.length ?? spec.steps?.length ?? spec.items?.length ??
    spec.metrics?.length ?? spec.bullets?.length ?? 0;
  let wanted = baseWanted;
  if (spec.layout === 'compare' && !spec.compare) {
    // compare 声明了版式但无左右组数据（只有普通 bullets）→ 走 content 类
    // 优先（P7 事故：2 条 bullets 落 4 卡页 → 下排半空）
    wanted = ['content', 'content-3col', 'content-4col'];
  } else if (spec.layout === 'big-number' && 3 >= (spec.metrics?.length ?? 0)) {
    wanted = ['content-3col', 'content-4col', 'content'];
  } else if (itemCount > 3 && baseWanted.includes('content-3col')) {
    wanted = ['content-4col', 'content-3col', 'content'];
  }
  // 按 wanted 优先级取候选：第一个有货的 kind 锁定，其内取页号最小的。
  // 历史缺陷：filter 含全部 kind 后取 [0]（页号序）——cards 想要 content-3col
  // 卡槽页却拿到页号更小的 content 页（无 item-title 槽）→ 条目内容整体丢失
  let candidates: TemplatePage[] = [];
  for (const k of wanted) {
    const c = pool.filter((p) => p.kind === k && !usedPages.has(p.page));
    if (c.length > 0) {
      candidates = c;
      break;
    }
  }
  if (candidates.length === 0) candidates = pool.filter((p) => !usedPages.has(p.page));
  if (candidates.length === 0) candidates = pool; // 全用完则允许复用
  // 槽数匹配优选：条目/卡片数明确时，跨 wanted 全部 kinds 找卡槽数最
  // 接近的页（P7 半空事故：2 条 compare 落 4 卡页；P3 丢条事故：4 bullets
  // 落 3 卡页）。排序键：槽数差 → 槽不足排后（宁空 1 卡不丢 1 条）→ 页号
  if (itemCount >= 2) {
    const allCand = wanted
      .flatMap((k) => pool.filter((p) => p.kind === k && !usedPages.has(p.page)))
      .filter((p, i, arr) => arr.findIndex((q) => q.page === p.page) === i);
    const scored = allCand
      .map((p) => ({
        p,
        n: p.slots.filter((s) => s.role === 'item-title' && s.w >= 1.5).length,
      }))
      .filter((s) => s.n > 0)
      .sort(
        (a, b) =>
          Math.abs(a.n - itemCount) - Math.abs(b.n - itemCount) ||
          (a.n < itemCount ? 1 : 0) - (b.n < itemCount ? 1 : 0) ||
          a.p.page - b.p.page
      );
    if (scored.length > 0) {
      candidates = [scored[0]!.p];
    }
  }
  // 金句页优选：无条目数据时，跨全部未用页找可用宽槽最宽的页（含 title
  // 槽——前言页 7.09in 正文框/章节页 7.92in 标题框都适合金句；卡片页 1.7in
  // 小框金句会被压到 11pt 难读）。封面/结尾/目录页除外（结构特殊）
  if (spec.quote?.text && itemCount < 2) {
    const scored = pool
      .filter((p) => !usedPages.has(p.page) && !['cover', 'closing', 'toc'].includes(p.kind))
      .map((p) => ({
        p,
        maxW: Math.max(
          0,
          ...p.slots
            .filter((s) => s.w >= 3 && !/^\d{4}$/.test(s.paras[0] ?? '') && (s.role === 'body' || s.role === 'item-body' || s.role === 'title'))
            .map((s) => s.w),
        ),
      }))
      .sort((a, b) => b.maxW - a.maxW || a.p.page - b.p.page);
    if (scored.length > 0 && scored[0]!.maxW >= 3) {
      candidates = [scored[0]!.p];
    }
  }
  const tpl = candidates[0]!;
  usedPages.add(tpl.page);

  const reps: Record<number, string> = {};
  const deletions: number[] = [];
  let shifts: Array<{ idx: number; dy: number }> | undefined;
  const cjk = (s: string): number => (s.match(/[\u4e00-\u9fa5]/g) ?? []).length;
  const byGeo = (a: TemplateSlot, b: TemplateSlot): number => a.y - b.y || a.x - b.x;
  const bodySlots = tpl.slots.filter((s) => s.role === 'body');
  const titleSlots = tpl.slots.filter((s) => s.role === 'title');
  const cjkBodies = bodySlots
    .filter((s) => cjk(s.paras.join('')) > 0)
    .sort((a, b) => cjk(b.paras.join('')) - cjk(a.paras.join('')));
  const fillTitle = (): void => {
    if (!spec.title) return;
    // 标题进面积最大的标题槽；窄于 2in 的小框（编号/年份装饰被误判 title）
    // 排除（P1 事故：主标题塞进"2026"大字槽缩到 48pt 且溢出）
    const pool2 = titleSlots.filter((s) => s.w >= 2);
    const slot =
      pool2.slice().sort((a, b) => b.w * (b.h || 1) - a.w * (a.h || 1))[0] ?? cjkBodies[0];
    if (slot) reps[slot.idx] = spec.title;
  };
  const fillCover = (): void => {
    // 封面：主标题进 CJK 最多的正文槽；最下方英文槽承接副标题。
    // titleSlots 若只含"2026"类年份装饰（y<1.2 被误判 title），跳过——
    // 主标题塞进大字槽会被缩号且溢出（P1 事故）
    const titlePool = titleSlots.filter((s) => !/^\d{4}$/.test(s.paras[0] ?? ''));
    const main = cjkBodies[0] ?? titlePool[0];
    if (spec.title && main) reps[main.idx] = spec.title;
    const enBodies = bodySlots.filter(
      (s) => cjk(s.paras.join('')) === 0 && !/^\d{4}$/.test(s.paras[0] ?? '')
    );
    const strap = enBodies.slice().sort((a, b) => b.y - a.y)[0];
    if (strap) reps[strap.idx] = spec.subtitle ?? '';
    const restEn = enBodies.filter((s) => s.idx !== strap?.idx);
    if (spec.bullets?.length && restEn.length > 0) {
      reps[restEn[0]!.idx] = spec.bullets.join(' · ').slice(0, 80);
    }
  };
  const fillSection = (): void => {
    fillTitle();
    const num = tpl.slots.filter((s) => s.role === 'number').sort(byGeo)[0];
    if (num && spec.subtitle && 4 >= spec.subtitle.trim().length) {
      reps[num.idx] = spec.subtitle.trim();
    }
  };
  const fillClosing = (): void => {
    const thanks = tpl.slots.find((s) => /THANKS/i.test(s.paras[0] ?? ''));
    if (thanks && spec.title && !/thanks/i.test(spec.title)) reps[thanks.idx] = spec.title;
    if (spec.subtitle && cjkBodies[0]) reps[cjkBodies[0]!.idx] = spec.subtitle;
  };
  const fillQuote = (): void => {
    // 金句页层级：quote 正文进最大可用槽（body/item-body/title 全集，视觉
    // 主体），标题进剩余大槽，署名进底部小槽；无合适署名槽时并入金句末行
    //（P9 空页事故 + 窄卡槽 11pt 难读事故的双重修复）
    const qt = spec.quote?.text ?? '';
    const author = spec.quote?.author ?? '';
    const allBodies = [
      ...bodySlots,
      ...tpl.slots.filter((s) => s.role === 'item-body'),
      ...titleSlots.filter((s) => !/^\d{4}$/.test(s.paras[0] ?? '')),
    ];
    const byArea = allBodies.slice().sort((a, b) => b.w * (b.h || 1) - a.w * (a.h || 1));
    const main = qt && byArea.length > 0 ? byArea[0] : undefined;
    if (spec.title) {
      const tslot = byArea.find((s) => s !== main && s.w >= 2);
      if (tslot) reps[tslot.idx] = spec.title;
    }
    if (qt && main) {
      // 金句按标点切段并聚合为 <=12 字的段（窄卡槽更保守，避免 11pt 仍溢出）
      const parts = qt.match(/[^，。！？；,!?]*[，。！？；,!?]?/g)?.filter((s) => s.length > 0) ?? [qt];
      // 段宽上限按主槽宽度自适应：宽主槽（金句视觉主体框）允许整句单行
      // ——硬切 12 字碎段在 8in 宽槽里毫无必要且难读（P9 金句被切成三段
      // 事故）；窄卡槽维持 12 字下限。按 18pt（金句可读字号）估算容量：
      // 18pt 内放得下保持整句单行，更长按标点分段（大字号两行优于小字
      // 号一行）；缩号兜底由 fill_tf 单行模式保证（≥11pt）
      const segLimit = Math.max(12, Math.floor((main.w * 72 * 0.92) / 18));
      const segs: string[] = [];
      let cur = '';
      for (const p of parts) {
        if (cur && (cur + p).length > segLimit) {
          segs.push(cur);
          cur = p;
        } else {
          cur += p;
        }
      }
      if (cur) segs.push(cur);
      // 主槽容纳校验：估算行高（基准字号 ×1.22）× 行数 ≤ 槽高（含 20% 裕量）。
      // 超出则按行数比例缩段（多分段降行高）或继续缩号由 python 兜底；
      // 行数超 5 行时截断防极端（长金句在窄槽的天花板）
      const estH = segs.length * 12 * 1.22 * (72 / 96); // 12pt 基准估算（pt）
      if (main.h > 0 && estH > main.h * 72 * 1.2 && segs.length > 1) {
        // 主槽太矮装不下 → 取前 2 段保主体，其余并入末段（python 侧继续自适应）
        const head = segs.slice(0, 2);
        const tail = segs.slice(2).join('');
        reps[main.idx] = tail ? head.concat([tail]).join('\n') : head.join('\n');
      } else {
        reps[main.idx] = segs.join('\n');
      }
    }
    if (author) {
      const rest = byArea.filter((s) => s !== main && !(s.idx in reps));
      const bottom = rest.slice().sort((a, b) => b.y - a.y)[0];
      if (bottom && bottom.w >= 1.5) {
        reps[bottom.idx] = author;
      } else if (main) {
        reps[main.idx] = reps[main.idx] ? `${reps[main.idx]}\n—— ${author}` : author;
      }
    }
  };
  const fillToc = (): void => {
    fillTitle();
    const tocItems: Array<{ label: string; desc?: string }> = spec.items ?? [];
    const itemTitlesT = tpl.slots.filter((s) => s.role === 'item-title').sort(byGeo);
    const labelSlots =
      itemTitlesT.length > 0
        ? itemTitlesT
        : bodySlots.filter((s) => !(s.idx in reps)).sort(byGeo);
    tocItems.slice(0, labelSlots.length).forEach((it, i) => {
      reps[labelSlots[i]!.idx] = it.label.replace(/^\d{1,2}\s*/, '');
      const body = tpl.slots.filter((s) => s.role === 'item-body').sort(byGeo)[i];
      if (body && it.desc) reps[body.idx] = it.desc;
    });
    // 多余条目组整体删除（P2 空框事故：4 条目录 vs 模板 5 组 → 第 5 组
    // 圆角矩形+编号+图标全空 → 视觉残留"空框"）。deletions 由 python 侧
    // 删除该槽位所属的顶层组合形状（先填空再删，兼容纯清空断言）。
    // 不依赖 item-title 角色：目录条目槽在真实模板中多为 body 角色
    if (tocItems.length < labelSlots.length) {
      const extra = labelSlots.slice(tocItems.length);
      for (const s of extra) {
        deletions.push(s.idx);
        reps[s.idx] = '';
      }
      // 剩余条目组整体平移：保持与"原全量条目跨度中心"一致。模板 N 条与
      // 装饰主体（大圆等）居中设计 → 删尾部条目后剩余组若原地不动则整体
      // 偏上（P2 目录不居中事故：模板 5 条删 1 条，整体偏上约 1cm）。以
      // 文本槽跨度估算（条目组内偏移首尾一致，误差 <0.3mm），python 侧
      // 平移各槽位所属顶层形状（组合整组移动，编号/图标/圆角矩形同步）
      const kept = labelSlots.slice(0, tocItems.length);
      if (kept.length >= 2) {
        const allTop = Math.min(...labelSlots.map((s) => s.y));
        const allBot = Math.max(...labelSlots.map((s) => s.y + (s.h || 0)));
        const keptTop = Math.min(...kept.map((s) => s.y));
        const keptBot = Math.max(...kept.map((s) => s.y + (s.h || 0)));
        const dyIn = (allTop + allBot) / 2 - (keptTop + keptBot) / 2;
        if (Math.abs(dyIn) > 0.02) {
          shifts = kept.map((s) => ({ idx: s.idx, dy: Math.round(dyIn * 914400) }));
        }
      }
    }
    tpl.slots
      .filter((s) => s.role === 'number')
      .sort(byGeo)
      .slice(tocItems.length)
      .forEach((n) => {
        reps[n.idx] = '';
      });
  };
  if (tpl.kind === 'cover') fillCover();
  else if (tpl.kind === 'closing') fillClosing();
  else if (tpl.kind === 'toc') fillToc();
  else if (spec.quote?.text) fillQuote();
  else if (tpl.kind === 'section') fillSection();
  else fillTitle();

  const footers = tpl.slots.filter((s) => s.role === 'footer');
  if (spec.subtitle && footers.length > 0) {
    reps[footers[0]!.idx] = spec.subtitle;
  }

  const itemTitles = tpl.slots.filter((s) => s.role === 'item-title').sort(byGeo);
  const itemBodies = tpl.slots.filter((s) => s.role === 'item-body').sort(byGeo);
  // 统一 items 为 {title, desc}（CardSpec/StepSpec/items 归一；metrics 走下方专属逻辑避免双写）
  const items: Array<{ title: string; desc?: string }> = spec.cards?.length
    ? spec.cards.map((c) => ({ title: c.title, desc: c.desc }))
    : spec.steps?.length
      ? spec.steps.map((st) => ({ title: st.label, desc: st.desc }))
      : spec.items?.length
        ? spec.items.map((it) => ({ title: it.label, desc: it.desc }))
        : [];
  const bullets = spec.bullets ?? [];

  const isContentKind =
    tpl.kind !== 'cover' && tpl.kind !== 'section' && tpl.kind !== 'closing' && tpl.kind !== 'toc';
  // 卡槽宽度过滤：太窄的"人名/日期"占位槽（如 T6 的 1.14in"代用名"）放不下
  // 整句要点 → 排除出 itemTitles，让内容走宽的 itemBodies（P4 排版事故）
  const titleMinW = 1.5;
  const usableItemTitles = itemTitles.filter((s) => s.w >= titleMinW);
  const usableItemBodies = itemBodies.filter((s) => s.w >= titleMinW);
  if (isContentKind && spec.metrics?.length && usableItemTitles.length > 0) {
    // 数据页：大数值进卡片标题位（视觉最突出），标签+说明进正文位
    spec.metrics.slice(0, usableItemTitles.length).forEach((m, i) => {
      reps[usableItemTitles[i]!.idx] = m.value;
      const body = usableItemBodies[i];
      if (body) reps[body.idx] = [m.label, m.desc].filter(Boolean).join('：');
    });
  } else if (isContentKind && items.length > 0 && usableItemTitles.length > 0) {
    items.slice(0, usableItemTitles.length).forEach((it, i) => {
      reps[usableItemTitles[i]!.idx] = it.title;
      const body = usableItemBodies[i];
      if (body && it.desc) reps[body.idx] = it.desc;
    });
  } else if (isContentKind && bullets.length > 0 && usableItemTitles.length > 0) {
    bullets.slice(0, usableItemTitles.length).forEach((b, i) => {
      const parts = b.split(/[：:]/);
      reps[usableItemTitles[i]!.idx] = parts[0] ?? b;
      const body = usableItemBodies[i];
      if (body) reps[body.idx] = parts.length > 1 ? parts.slice(1).join('：') : '';
    });
  } else if (isContentKind && bullets.length > 0) {
    // 无窄标题槽可用：整句要点按槽面积降序填充（大槽优先，窄人名/日期
    // 装饰槽自然沉底不被占用）；候选合并 item-body（卡片正文槽）；
    // 长文拆两段（P4 事故：整句塞窄框导致缩号到 11pt 仍溢出）
    const slotsByArea = [...bodySlots, ...itemBodies]
      .filter((s) => !(s.idx in reps))
      .sort((a, b) => b.w * (b.h || 1) - a.w * (a.h || 1));
    bullets.slice(0, slotsByArea.length).forEach((b, i) => {
      const s = slotsByArea[i]!;
      if (b.length > 24 && s.h > 0.9) {
        const words = b.replace(/[，,]/g, '，').split('，');
        const mid = Math.ceil(words.length / 2);
        const two = [words.slice(0, mid).join('，'), words.slice(mid).join('，')]
          .filter((seg) => seg.length > 0);
        reps[s.idx] = two.join('\n');
      } else {
        reps[s.idx] = b;
      }
    });
  } else if (isContentKind && spec.metrics?.length) {
    const slotsByY = bodySlots.filter((s) => !(s.idx in reps)).sort(byGeo);
    spec.metrics.slice(0, slotsByY.length).forEach((m, i) => {
      reps[slotsByY[i]!.idx] = `${m.label} ${m.value}`;
    });
  }
  if (isContentKind && spec.content) {
    const wide =
      bodySlots.filter((s) => !(s.idx in reps)).sort((a, b) => b.w - a.w)[0] ??
      itemBodies.filter((s) => !(s.idx in reps))[0];
    if (wide) reps[wide.idx] = spec.content.slice(0, 200);
  }

  // 占位清理：未填充的正文/条目/标题槽一律清空，杜绝模板占位文字泄漏到成品；
  // 封面 4 位年份装饰除外
  for (const s of tpl.slots) {
    if (s.idx in reps) continue;
    if (s.role === 'body' || s.role === 'item-title' || s.role === 'item-body' || s.role === 'title') {
      if (tpl.kind === 'cover' && /^\d{4}$/.test(s.paras.join(''))) continue;
      reps[s.idx] = '';
    }
  }
  return { templatePage: tpl.page, replacements: reps, deletions: deletions.length > 0 ? deletions : undefined, shifts };
}

/* ───────────── 3. 执行克隆（python-pptx 子进程） ───────────── */

/** 生成 python 克隆脚本并执行：输出新 pptx */
export function executeClone(
  templatePath: string,
  outputPath: string,
  plans: Array<{ templatePage: number; replacements: Record<number, string>; deletions?: number[]; shifts?: Array<{ idx: number; dy: number }> }>
): { ok: boolean; output: string } {
  const pyScript = buildPythonScript(plans);
  const tmpPy = path.join(os.tmpdir(), `wd_clone_${Date.now()}.py`);
  const tmpJson = path.join(os.tmpdir(), `wd_clone_${Date.now()}.json`);
  fs.writeFileSync(tmpJson, JSON.stringify({ template: templatePath, output: outputPath, pages: plans }), 'utf8');
  fs.writeFileSync(tmpPy, pyScript, 'utf8');
  try {
    const out = execFileSync('python', ['-X', 'utf8', tmpPy, tmpJson], {
      encoding: 'utf8',
      timeout: 120000,
      windowsHide: true,
    }).toString();
    return { ok: true, output: out };
  } catch (err: any) {
    return { ok: false, output: String(err?.stderr || err) };
  } finally {
    try { fs.unlinkSync(tmpPy); } catch { /* */ }
    try { fs.unlinkSync(tmpJson); } catch { /* */ }
  }
}

function buildPythonScript(plans: unknown): string {
  return PYTHON_CLONE;
}

/* ───────────── 0. 模板扫描（python 子进程 → 槽位结构 JSON） ───────────── */

export interface ScanResult {
  ok: boolean;
  pages: TemplatePage[];
  error?: string;
}

/** 扫描模板：每页文本框 {idx,x,y,w,h,paras} → Node 侧角色/版式分类。
 *  idx 枚举口径与克隆侧严格一致（仅含非空文本框，按 shape 顺序） */
export function scanTemplate(templatePath: string): ScanResult {
  const tmpPy = path.join(os.tmpdir(), `wd_scan_${Date.now()}.py`);
  fs.writeFileSync(tmpPy, PYTHON_SCAN, 'utf8');
  try {
    const out = execFileSync('python', ['-X', 'utf8', tmpPy, templatePath], {
      encoding: 'utf8',
      timeout: 60000,
      windowsHide: true,
      maxBuffer: 16 * 1024 * 1024,
    }).toString();
    const data = JSON.parse(out.trim()) as {
      pages: Array<{ page: number; boxes: Array<{ idx: number; x: number; y: number; w: number; h: number; paras: string[] }> }>;
    };
    const pages = data.pages.map((p) => classifyTemplatePage(p.page, p.boxes));
    return { ok: true, pages };
  } catch (err: any) {
    return { ok: false, pages: [], error: String(err?.stderr || err) };
  } finally {
    try { fs.unlinkSync(tmpPy); } catch { /* */ }
  }
}

const PYTHON_SCAN = `
import sys, json
from pptx import Presentation
from pptx.util import Emu

def inch(v):
    return round(Emu(v).inches, 2) if v is not None else 0

prs = Presentation(sys.argv[1])

def walk(shapes):
    # 递归展开组合形状（目录条目等常嵌套在 Group 内，顶层遍历会漏）
    for sh in shapes:
        if sh.shape_type == 6:
            for sub in walk(sh.shapes):
                yield sub
        else:
            yield sh

pages = []
for si, slide in enumerate(prs.slides, start=1):
    boxes = []
    idx = 0
    for sh in walk(slide.shapes):
        if not sh.has_text_frame:
            continue
        paras = [p.text for p in sh.text_frame.paragraphs if p.text.strip()]
        if not paras:
            continue
        boxes.append({
            'idx': idx,
            'x': inch(sh.left), 'y': inch(sh.top),
            'w': inch(sh.width), 'h': inch(sh.height),
            'paras': paras,
        })
        idx += 1
    pages.append({'page': si, 'boxes': boxes})
print(json.dumps({'pages': pages}, ensure_ascii=False))
`;

/** python 侧：读 json 计划 → 保格式替换 → 删页重排 → 另存 */
const PYTHON_CLONE = `
import sys, json, copy
from pptx import Presentation
from pptx.oxml.ns import qn

cfg = json.load(open(sys.argv[1], encoding='utf-8'))
prs = Presentation(cfg['template'])
slides = list(prs.slides)

def walk(shapes):
    # 递归展开组合形状（与扫描侧同序，保证 idx 对齐）
    for sh in shapes:
        if sh.shape_type == 6:
            for sub in walk(sh.shapes):
                yield sub
        else:
            yield sh

def est_width_pt(text, sz):
    # 文本宽度估算（磅）：中文/全角 = 1em；西文/数字 ≈ 0.72em（雅黑西文
    # 实测接近 0.72，0.55/0.62 均低估导致成品溢出换行）；emoji ≈ 1.1em
    w = 0.0
    for ch in str(text):
        cp = ord(ch)
        if cp > 0x2E7F and not (0x1F000 <= cp <= 0x1FAFF):
            w += 1.0
        elif 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF:
            w += 1.1
        else:
            w += 0.72
    return w * sz

def wrap_none_fix(tf):
    # w:none（不换行）槽位保护：模板单行装饰槽（编号/日期/标语）被填入
    # 中文长文时会横向溢出边界（"排版错位"观感）→ 改为 square 允许折行
    bodyPr = tf._txBody.find(qn('a:bodyPr'))
    if bodyPr is not None and bodyPr.get('wrap') == 'none':
        bodyPr.set('wrap', 'square')

def fill_tf(tf, text, box_w_in, box_h_in):
    # 保格式替换 + 自适应排版：
    # 1) 抓原首个带 rPr 的 run（字体/字号/颜色/加粗）作为格式基准
    # 2) 文本超出框宽 96% → 字号按比例收缩（下限 max(14pt, 原×0.5)）
    #    （修复：长标题进小框 / "2026"类大字换行爆版）
    # 3) 原 run 无字号 → 显式 14pt 兜底（防 PowerPoint 默认 18pt 不统一）
    # 4) 多行文本估算累计行高，超框高时同步收缩
    wrap_none_fix(tf)
    rpr_xml = None
    base_sz = None
    for p in tf.paragraphs:
        for r in p.runs:
            rp = r._r.find(qn('a:rPr'))
            if rp is not None and rpr_xml is None:
                rpr_xml = rp
                if rp.get('sz'):
                    base_sz = int(rp.get('sz')) / 100.0
    if base_sz is None:
        base_sz = 14.0
    lines = [ln for ln in str(text).split('\\n')]
    # 可用宽/高按实际 inset 扣减（默认 lIns/rIns=91440EMU=7.2pt、tIns/bIns
    # =45720EMU=3.6pt；ins:0-0 槽全宽）。旧 0.96 系数对 7-7pt inset 的框
    # 会高估可用宽（2.71in 框实测高估 ~8pt → 边界文本溢出换行）
    bodyPr_el = tf._txBody.find(qn('a:bodyPr'))
    li = ri = 7.2
    ti = bi = 3.6
    if bodyPr_el is not None:
        try:
            li = int(bodyPr_el.get('lIns', 91440)) / 12700.0
            ri = int(bodyPr_el.get('rIns', 91440)) / 12700.0
            ti = int(bodyPr_el.get('tIns', 45720)) / 12700.0
            bi = int(bodyPr_el.get('bIns', 45720)) / 12700.0
        except ValueError:
            pass
    usable_w_pt = max(box_w_in, 0.3) * 72 - (li + ri)
    usable_h_pt = max(box_h_in, 0.3) * 72 - (ti + bi)
    sz = base_sz
    # wrap-first 策略：正文类文本（字号 <=18pt 且框足够宽）优先按框宽硬折行
    # 而非缩号——正文 12-14pt 折 2 行远比缩到 11pt 更可读（P4 排版难看事故：
    # 整句塞窄框缩到 11pt 下限）。wrap 折行后行数仍超框高 → 再走缩号兜底
    is_body_text = base_sz <= 18.0 and max(box_w_in, 0.3) >= 1.2
    # 单行优先（P9 金句事故泛化）：无换行文本若能缩号到 ≥11pt 单行放下，
    # 则不折行——“能单行就单行”。need_sz<11 的长正文自然继续走 wrap 折行
    single_all = '\\n' not in str(text)
    if single_all and lines:
        per_pt = est_width_pt(lines[0], 1.0)
        if per_pt > 0 and usable_w_pt / per_pt >= 11.0:
            need_sz = usable_w_pt / per_pt
            if need_sz < sz:
                sz = max(11.0, need_sz)
            is_body_text = False  # 单行模式，禁用 wrap-first 折行
    if is_body_text:
        wrapped_lines = []
        for ln in lines:
            if est_width_pt(ln, sz) <= usable_w_pt:
                wrapped_lines.append(ln)
                continue
            # 逐字符贪心折行（CJK 无空格分词）
            cur = ''
            for ch in ln:
                if est_width_pt(cur + ch, sz) > usable_w_pt and cur:
                    wrapped_lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                wrapped_lines.append(cur)
        total_h = len(wrapped_lines) * sz * 1.22
        if total_h <= usable_h_pt:
            lines = wrapped_lines  # wrap 可容纳 → 直接用，不缩号
    # 宽度自适应（含换行折算：过宽的行按折行后占两行计）
    import math
    wrapped = 0
    for ln in lines:
        need = est_width_pt(ln, sz)
        wrapped += max(1, math.ceil(need / usable_w_pt)) if usable_w_pt > 0 else 1
    line_h = sz * 1.22
    # 迭代收缩：宽超→缩；高超→缩；到下限停（下限 11pt：说明类长文案
    # 在窄槽仍可读，比 14pt 下限多 27% 收缩空间）
    for _ in range(24):
        overflow_w = any(est_width_pt(ln, sz) > usable_w_pt for ln in lines if ln)
        total_h = wrapped * line_h
        overflow_h = total_h > usable_h_pt
        if not overflow_w and not overflow_h:
            break
        new_sz = sz * 0.9
        if new_sz <= 11.0:
            sz = 11.0
            break
        sz = new_sz
        wrapped = sum(max(1, math.ceil(est_width_pt(ln, sz) / usable_w_pt)) for ln in lines) if usable_w_pt > 0 else len(lines)
        line_h = sz * 1.22
    for p in list(tf.paragraphs[1:]):
        p._p.getparent().remove(p._p)
    p0 = tf.paragraphs[0]
    for r in list(p0.runs):
        r._r.getparent().remove(r._r)
    first = True
    for line in lines:
        if not first:
            p0 = tf.add_paragraph()
        run = p0.add_run()
        run.text = line
        if rpr_xml is not None:
            rp = copy.deepcopy(rpr_xml)
            rp.set('sz', str(int(round(sz * 100))))
            run._r.insert(0, rp)
        else:
            from pptx.util import Pt
            run.font.size = Pt(sz)
        first = False

def emote_space(text):
    # emoji 间距修复：行首 emoji 后紧跟文字时补一个空格（PowerPoint 渲染
    # emoji 字形偏宽，无空格时与后续文字视觉挤压，如 🧩JSON / 🖥COM）
    if not text:
        return text
    head = text[0]
    cp = ord(head)
    is_emote = (0x1F000 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF)
    if is_emote and len(text) > 1 and not text[1].isspace():
        return head + ' ' + text[1:]
    return text

def num_guard(tf, box_w_in):
    # 数字框守卫：纯数字/百分比装饰（01/02/2026/88%）必须单行且居中。
    # 模板数字框普遍是"字号>框宽"的原生缺陷（P6/P8/P9 用户报告的换行与
    # 不居中根因），fill_tf 逐 run 收缩对多 run 数字框不完全可靠——这里
    # 在 XML 层一次到位：统一字号（按 0.75 超保守系数+6% 裕量反推）、
    # wrap=square（防 none 溢出）、anchor=ctr + algn=ctr（圆圈居中）
    import re as _re
    txt = tf.text.strip()
    if not _re.match(r'^\\d{1,4}%?$', txt):
        return
    if box_w_in < 0.28:
        return  # 极小装饰点保持原设计
    sz = None
    for p in tf.paragraphs:
        for r in p.runs:
            rp = r._r.find(qn('a:rPr'))
            if rp is not None and rp.get('sz'):
                sz = int(rp.get('sz')) / 100.0
                break
        if sz:
            break
    if sz is None:
        return
    bp = tf._txBody.find(qn('a:bodyPr'))
    ins = 14.4
    if bp is not None:
        try:
            li = int(bp.get('lIns', 91440)) / 12700.0
            ri = int(bp.get('rIns', 91440)) / 12700.0
            ins = li + ri
        except ValueError:
            pass
    safe_sz = (max(box_w_in, 0.28) * 72 - ins) * 0.94 / (len(txt) * 0.75)
    if safe_sz < sz:
        sz = max(10.0, round(safe_sz * 2) / 2.0)
        for p in tf.paragraphs:
            for r in p.runs:
                rp = r._r.find(qn('a:rPr'))
                if rp is not None:
                    rp.set('sz', str(int(sz * 100)))
    # wrap / anchor / algn 居中
    if bp is not None:
        if bp.get('wrap') == 'none':
            bp.set('wrap', 'square')
        bp.set('anchor', 'ctr')
    for p in tf.paragraphs:
        pPr = p._p.find(qn('a:pPr'))
        if pPr is None:
            pPr = p._p.makeelement(qn('a:pPr'), {})
            p._p.insert(0, pPr)
        pPr.set('algn', 'ctr')

def top_shape_of(slide, box_el):
    # 槽位元素 → 所属顶层形状元素（spTree 直接子节点）
    spTree = slide.shapes._spTree
    top_el = box_el
    while top_el.getparent() is not None and top_el.getparent() is not spTree:
        top_el = top_el.getparent()
    return top_el if top_el.getparent() is not None else None

def shift_top_shape(top_el, dy):
    # 顶层形状整体垂直平移 dy（组合：改 grpSpPr/xfrm/off，子形状随组坐标系
    # 自动同步；普通形状：改 spPr/xfrm/off）
    if not dy:
        return
    spPr = top_el.find(qn('p:spPr'))
    if spPr is None:
        spPr = top_el.find(qn('p:grpSpPr'))
    if spPr is None:
        return
    xfrm = spPr.find(qn('a:xfrm'))
    if xfrm is None:
        return
    off = xfrm.find(qn('a:off'))
    if off is None:
        return
    off.set('y', str(int(off.get('y', '0')) + int(dy)))

# [已移除 align_guard]（序号下坠事故根因）：该守卫曾按"编号归属最近
# 大装饰"启发式垂直移动编号框，但 (a) 中心点算术错误——(left+w)//2 把
# "右边缘坐标的一半"当成中心，导致编号被下推 0.7~2.9in、03/04 直接掉
# 出页面底边；(b) 即使算术修正，角标式（P10 风车四角编号）与堆叠标签
# 式（P29 步骤卡编号）版式仍会被误"居中"破坏——"模板原生缺陷"与
# "有意设计"无法用几何启发式区分。克隆引擎契约：只换文字，永不移动
# 模板形状位置；模板自身的对齐缺陷应在模板文件层面修复。

# 应用替换计划（每项 = 产出 PPT 的一页）
for plan in cfg['pages']:
    tpl_idx = plan['templatePage'] - 1
    if tpl_idx < 0 or tpl_idx >= len(slides):
        continue
    slide = slides[tpl_idx]
    # idx 口径与扫描侧一致：仅含非空文本框，按 walk 顺序（含组合内嵌套形状）
    boxes = [s for s in walk(slide.shapes) if s.has_text_frame and s.text_frame.text.strip()]
    # deletions：删除指定槽位所属的顶层形状（组合形状整组删除，把圆角
    # 矩形+编号+图标等装饰一并带走，避免残留空框）
    del_ids = set()
    for d in plan.get('deletions') or []:
        try:
            del_ids.add(int(d))
        except (TypeError, ValueError):
            pass
    if del_ids:
        # 标记法删除：python-pptx 形状代理对象每次迭代都是新实例，is/id()
        # 比较恒不相等（P2 空框问题三修未愈的根因）。改为在 XML 节点上打
        # 属性标记（纯 XML 层稳定），再删除"自身或子树带标记"的顶层形状
        MARK = '_wdDel'
        for di in del_ids:
            if 0 <= di < len(boxes):
                boxes[di]._element.set(MARK, '1')
        for top in list(slide.shapes):
            te = top._element
            if te.get(MARK) is not None or te.find('.//*[@' + MARK + ']') is not None:
                te.getparent().remove(te)
    # shifts：条目组删减后整体平移居中（P2 目录不居中事故：模板 5 条
    # 删 1 条后剩余 4 组原地不动 → 整体偏上约 1cm）。python 侧精确重算：
    # 目标中心优先取页面最大无文本装饰形状（目录大圆等视觉锚，排除面积
    # 超 60% 页面的全页背景）；无锚时退回 TS 估算 dy。同组多槽位只平移一次
    if plan.get('shifts'):
        shift_pairs = []
        for item in plan['shifts']:
            try:
                di = int(item['idx'])
                sdy = int(item['dy'])
            except (TypeError, ValueError, KeyError):
                continue
            if 0 <= di < len(boxes):
                shift_pairs.append((boxes[di], sdy))
        uniq_tops = []
        for box, _ in shift_pairs:
            te = top_shape_of(slide, box._element)
            if te is not None and all(t is not te for t in uniq_tops):
                uniq_tops.append(te)
        if uniq_tops:
            def _el_y_h(el):
                sp = el.find(qn('p:spPr'))
                if sp is None:
                    sp = el.find(qn('p:grpSpPr'))
                if sp is None:
                    return None
                xf = sp.find(qn('a:xfrm'))
                if xf is None:
                    return None
                off = xf.find(qn('a:off'))
                ext = xf.find(qn('a:ext'))
                if off is None or ext is None:
                    return None
                return int(off.get('y', '0')), int(ext.get('cy', '0'))
            yhs = [r for r in (_el_y_h(t) for t in uniq_tops) if r]
            dy = shift_pairs[0][1] if shift_pairs else 0
            if yhs:
                pw = prs.slide_width or 0
                ph = prs.slide_height or 0
                best_area = 0
                anchor_cy = None
                for s in slide.shapes:
                    if s.shape_type == 6:
                        continue
                    if s.has_text_frame and s.text_frame.text.strip():
                        continue
                    sw = s.width or 0
                    sh2 = s.height or 0
                    if sw <= 0 or sh2 <= 0:
                        continue
                    if pw > 0 and ph > 0 and sw * sh2 > pw * ph * 0.6:
                        continue
                    # 扁宽背景/竖幅装饰非居中锚（T3 目录页背景 Freeform 宽高
                    # 比 1.98，面积小于 60% 页面仍会误锚 → 锚到 0.23cm 偏差）
                    if sw / sh2 > 1.8 or sh2 / sw > 1.8:
                        continue
                    if sw * sh2 > best_area:
                        best_area = sw * sh2
                        anchor_cy = s.top + sh2 // 2
                if anchor_cy is not None:
                    dy = anchor_cy - (min(y for y, _ in yhs) + max(y + h for y, h in yhs)) // 2
            for te in uniq_tops:
                shift_top_shape(te, dy)
    for slot_idx, new_text in plan['replacements'].items():
        idx = int(slot_idx)
        if idx >= len(boxes):
            continue
        sh = boxes[idx]
        fill_tf(sh.text_frame, emote_space(new_text),
                (sh.width or 0) / 914400.0, (sh.height or 0) / 914400.0)
    # 数字框守卫（页级全量）：编号/百分比装饰框无论是否被替换，统一
    # 保证单行+居中（模板数字框字号普遍超框宽是原生缺陷）
    for sh in boxes:
        num_guard(sh.text_frame, (sh.width or 0) / 914400.0)

# 删页 + 重排：输出仅含计划页，顺序 = 计划顺序（封面在前/结尾在后）
sldIdLst = prs.part._element.find(qn('p:sldIdLst'))
all_sldIds = list(sldIdLst.findall(qn('p:sldId')))
order = [p['templatePage'] - 1 for p in cfg['pages']]
for el in all_sldIds:
    sldIdLst.remove(el)
max_id = max((int(el.get('id')) for el in all_sldIds), default=255)
seen = set()
for i in order:
    if i >= len(all_sldIds):
        continue
    el = all_sldIds[i]
    if i in seen:
        # 同一模板页复用（罕见兜底）：深拷贝独立 sldId
        el = copy.deepcopy(el)
        max_id += 1
        el.set('id', str(max_id))
    seen.add(i)
    sldIdLst.append(el)

prs.save(cfg['output'])
print('CLONE_OK planned=' + str(len(cfg['pages'])) + ' final=' + str(len(prs.slides)))
`;
