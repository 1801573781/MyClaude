/**
 * Markdown 内容稿 → SlideSpec[] 确定性转换器（零 LLM）
 * "真成功"最终防线的核心：LLM 输出超长 slides JSON 被截断/反复失败时，
 * 把已落盘的 md 逐页文案用纯代码解析成幻灯片结构，杜绝假成功与真失败。
 *
 * 支持形态（由粗到细）：
 * 1. 逐页小节：`## P1 封面` + 键值行（- 标题：xxx）/ 有序目录项 / 无序要点
 * 2. 一级标题分节：`# 章节` + 列表
 * 3. 无结构文本：按空行分块，每块一页；空文本兜底单页封面（永不返回空数组）
 */
import type { SlideSpec } from './pptx-engine.js';

const MAX_PAGES = 30;
const MAX_BULLET_LEN = 100;

/** 版式推断关键词（顺序即优先级）。
 *  closing 含"开启…之旅/篇章"类收尾语；grid-2x2 含"四大"（四要点宫格语义）。 */
const LAYOUT_HINTS: Array<{ layout: string; re: RegExp }> = [
  { layout: 'cover', re: /封面|cover|首页|开篇/i },
  { layout: 'toc', re: /目录|contents?|toc/i },
  { layout: 'closing', re: /结尾|谢谢|致谢|thank|closing|结束|开启.*(?:之旅|篇章)/i },
  { layout: 'timeline', re: /流程|步骤|时间线|timeline|roadmap|四步/i },
  { layout: 'compare', re: /对比|versus|\bvs\b|比较/i },
  { layout: 'grid-4x2', re: /八宫格|4x2|八大/i },
  { layout: 'grid-3x2', re: /六宫格|3x2|六大/i },
  { layout: 'grid-2x2', re: /四宫格|2x2|矩阵|四大/i },
  { layout: 'cards', re: /卡片|三卡|cards/i },
  { layout: 'section', re: /章节|section|part\s*\d/i },
];

function inferLayout(name: string): string {
  for (const h of LAYOUT_HINTS) if (h.re.test(name)) return h.layout;
  return 'content';
}

/** 节标题 → 页面名：剥离 P1 / 第1页 / 1. 等页码前缀 */
function pageName(heading: string): string {
  return heading
    .replace(/^p\s*\d+[\s.、:：-]*/i, '')
    .replace(/^第\s*\d+\s*页[\s:：-]*/, '')
    .replace(/^\d+[\s.、:：-]*/, '')
    .trim();
}

/** 行首 emoji（含变体选择符）匹配 */
const EMOJI_RE =
  /^([\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F000}-\u{1F02F}\u{FE0F}✓✕★☆✔✖⚙])\uFE0F?\s*/u;

/** 引擎支持的全部版式（"版式：" kv 行白名单；LAYOUT_HINTS 只是关键词推断子集） */
const KNOWN_LAYOUTS = [
  'cover', 'toc', 'section', 'content', 'two-col', 'cards', 'timeline',
  'grid-2x2', 'grid-3x2', 'grid-4x2', 'quote', 'compare', 'big-number',
  'chart', 'image-text', 'closing',
];

/** "- 🚀 要点" 列表行首图标提取（保留给 bullets/卡片渲染用） */
function lineEmoji(line: string): string | undefined {
  const m = /^[-*•]\s*([\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F000}-\u{1F02F}✓✕★☆✔✖⚙])\uFE0F?/u.exec(
    line.trim()
  );
  return m?.[1];
}

/** 键值行：`- 标题：xxx`（key ≤ 6 字）→ [key, value]；非键值行返回 null。
 *  剥离顺序：列表符(-*•) → emoji → 匹配。"- 📌 第一步：xxx" 剥两层后
 *  key="第一步"（干净无 emoji），emoji 由 lineEmoji 单独提取供图标用 */
function kvLine(line: string): [string, string] | null {
  let t = line.trim();
  t = t.replace(/^[-*•]\s*/, ''); // 列表符
  t = t.replace(EMOJI_RE, ''); // 行首 emoji
  const m = /^(?:\*\*)?([^:：]{1,6})(?:\*\*)?\s*[:：]\s*(.+)$/.exec(t);
  return m ? [m[1]!.trim(), m[2]!.trim()] : null;
}

/** 行首 emoji 图标剥离："🚀 智能规划：理解需求" → { icon: '🚀', text: '智能规划：…' } */
function stripIcon(s: string): { icon?: string; text: string } {
  const head = EMOJI_RE.exec(s);
  if (!head) return { text: s };
  const rest = s.slice(head[0].length);
  return rest ? { icon: head[1], text: rest.trim() } : { text: s };
}

function clampBullet(s: string): string {
  const t = s
    .trim()
    .replace(/^[-*•]\s*/, '')
    .replace(/^\d+[.、)]\s*/, '');
  return t.length > MAX_BULLET_LEN ? `${t.slice(0, MAX_BULLET_LEN)}…` : t;
}

/** "明确目标：你说出需求" → { label, desc }（冒号前为标签） */
function splitLabelDesc(text: string): { label: string; desc?: string } {
  const idx = text.search(/[：:]/);
  if (idx <= 0 || idx > 12) return { label: text.slice(0, 20) };
  return { label: text.slice(0, idx).trim(), desc: text.slice(idx + 1).trim() || undefined };
}

/** 目录条目："01 认识 WorkBuddy：它是什么" / "认识——是什么" → { label, desc } */
function pushTocItem(arr: Array<{ label: string; desc?: string }>, text: string): void {
  let label = text;
  let desc: string | undefined;
  const num = /^(\d{1,2})\s+(.+)$/.exec(text);
  if (num) label = num[2]!;
  const sepIdx = label.search(/[：:]|——/);
  if (sepIdx > 0) {
    desc = label.slice(sepIdx + 1).trim() || undefined;
    label = label.slice(0, sepIdx).trim();
  }
  arr.push({ label: `${num ? `${num[1]} ` : ''}${label}`, desc });
}

/** 单个小节 → 一页 SlideSpec（含结构化版式升级） */
function parseSection(heading: string, bodyLines: string[], fallbackTitle: string): SlideSpec {
  const name = pageName(heading) || heading;
  // 注意 let：版式 kv 行（"- 版式：cards"）会同步更新此局部变量。
  // 历史缺陷：只更新 slide.layout 不更新 layout，导致宫格/度量/组等解析
  // 分支全部按旧版式判断 → 数据漏进 bullets → 结构化版式渲染空页
  let layout = inferLayout(name);
  const slide: SlideSpec = { layout };
  slide.title = name; // 节名默认为页标题（kv 行"标题："可覆盖）
  // chart 数据收集（"标签 数值：说明"行，chart 版式时组装 series）
  const chartLabels: string[] = [];
  const chartValues: number[] = [];
  const chartDescs: string[] = [];
  // two-col 右栏条目（"📊 人效：人均营收156万元"带图标 kv 行 → items）
  const twoColItems: Array<{ label: string; desc?: string }> = [];
  const bullets: string[] = [];
  const tocItems: Array<{ label: string; desc?: string }> = [];
  const olSteps: Array<{ label: string; desc?: string }> = [];
  const metrics: Array<{ value: string; label: string; desc?: string }> = [];
  const gridCards: Array<{ title: string; desc?: string; icon?: string }> = [];
  const groups: Array<{ label: string; points: string[] }> = [];
  let curGroup: { label: string; points: string[] } | null = null;
  /** 暂存的组标签（compare 组标签不带冒号场景：短行后跟列表行时回溯成组） */
  let pendingGroupLabel: string | null = null;
  // 宫格类版式集合（卡片数自适应 2x2/3x2/4x2）
  const gridLayouts = ['grid-2x2', 'grid-3x2', 'grid-4x2', 'cards'];

  for (const raw of bodyLines) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || line === '---') continue;
    // 引用行（> xxx）→ 讲者备注（原版每页均有讲稿，md 中用 > 标注）
    if (line.startsWith('>')) {
      const noteText = line.replace(/^>\s*/, '').trim();
      if (noteText) slide.notes = slide.notes ? `${slide.notes} ${noteText}` : noteText.slice(0, 300);
      continue;
    }
    const kv = kvLine(line);
    if (kv) {
      const [k, v] = kv;
      if (/^(标题|题目|title)$/i.test(k)) slide.title = v;
      else if (/^(副标题|子标题|subtitle)$/i.test(k)) slide.subtitle = v;
      else if (/^(要点|bullets?)$/i.test(k)) {
        bullets.push(...v.split(/[；;]/).map(clampBullet).filter((s) => s.length > 0));
      } else if (/^(版式|页面|layout)$/i.test(k)) {
        const low = v.toLowerCase().trim();
        if (KNOWN_LAYOUTS.includes(low)) {
          slide.layout = low;
          layout = low; // 关键：同步局部变量，后续解析分支按新版式分流
        }
      } else if (/^(金句|quote)$/i.test(k)) {
        slide.quote = { ...(slide.quote ?? {}), text: v.slice(0, 120) };
      } else if (/^(署名|作者|author)$/i.test(k)) {
        slide.quote = { text: slide.quote?.text ?? '', author: v.slice(0, 40) };
      } else if (/^(备注|讲稿|讲者|notes?|speaker)$/i.test(k)) {
        slide.notes = v.slice(0, 300);
      } else if (/^(徽章|章节|页眉|badge)$/i.test(k)) {
        slide.badge = v.slice(0, 40);
      } else if (/^(内容|正文|说明|content|desc)$/i.test(k)) {
        slide.content = slide.content ? `${slide.content}\n${v}` : v;
      } else {
        // 未知 key 的 kv 行按上下文分流（否则宫格卡片/对比组/度量全部漏进 bullets）
        const isMetric = /^\d+(?:\.\d+)?\s*[^\s：:]{0,3}$/.test(k);
        // 消费暂存组标签（compare：前一行短行 + 本行 kv → 组标签）。
        // curGroup 已存在时也切换（第二组标签场景），与无序列表分支语义一致
        if (pendingGroupLabel) {
          curGroup = { label: pendingGroupLabel, points: [] };
          groups.push(curGroup);
          pendingGroupLabel = null;
        }
        // 中文标签度量："全年营收 12.6亿元" / "回款率 96.8%"（标签+数值+单位）
        const labelMetric = /^([\u4e00-\u9fa5A-Za-z0-9]{1,8})\s+(\d+(?:\.\d+)?)\s*([^\s：:，,]{0,4})$/.exec(k);
        // 季度/月份序数（timeline 步骤）："Q1" / "3月" / "1季度" / "Q1季度"
        const periodStep = /^(Q[1-4]|q[1-4]|[1-4]季度|第[1-4]季度|1[0-2]月|[1-9]月|第[一二三四]季度|Q[1-4]季度)$/.test(k);
        if (curGroup) {
          curGroup.points.push(clampBullet(`${k}：${v}`));
        } else if (periodStep) {
          olSteps.push({ label: k, desc: v });
        } else if (/^(第[一二三四五六七八九十\d]+步|步骤[一二三四五六七八九十\d]+|step\s*\d+)$/i.test(k)) {
          // 中文序数步骤（"第一步：xxx" / "步骤一：xxx"）→ timeline 步骤
          olSteps.push({ label: k, desc: v });
        } else if (layout === 'two-col' && lineEmoji(line)) {
          // two-col：带 emoji 的 kv 行 → 右栏条目卡（左栏留给普通要点）
          twoColItems.push({ label: k, desc: v });
        } else if (labelMetric && layout === 'big-number') {
          metrics.push({ value: `${labelMetric[2]}${labelMetric[3] ?? ''}`, label: labelMetric[1]!, desc: v });
        } else if (labelMetric && layout === 'chart') {
          // chart 版式："一季度 3.1亿元：夯实基础" → 标签+数值
          chartLabels.push(labelMetric[1]!);
          chartValues.push(parseFloat(labelMetric[2]!));
          chartDescs.push(v);
        } else if (gridLayouts.includes(layout)) {
          gridCards.push({ icon: lineEmoji(line), title: k, desc: v });
        } else if (isMetric) {
          metrics.push({ value: k, label: v });
        } else {
          const em = lineEmoji(line);
          bullets.push(clampBullet(em ? `${em} ${k}：${v}` : `${k}：${v}`));
        }
      }
      continue;
    }
    // 有序列表：timeline 步骤（或目录条目）
    const ol = /^(\d+)[.、)]\s*(.+)$/.exec(line);
    if (ol) {
      const text = ol[2]!.trim();
      if (layout === 'toc') pushTocItem(tocItems, text);
      else {
        olSteps.push(splitLabelDesc(text));
        bullets.push(clampBullet(text));
      }
      continue;
    }
    // 无序列表
    if (/^[-*•]/.test(line)) {
      const content = line.replace(/^[-*•]\s*/, '');
      if (layout === 'toc') {
        pushTocItem(tocItems, content);
        continue;
      }
      // 消费暂存组标签（compare：前面短行 + 本列表行 → 成组）。
      // curGroup 已存在时也切换（第二组标签"WorkBuddy 新方式"场景）
      if (pendingGroupLabel) {
        curGroup = { label: pendingGroupLabel, points: [] };
        groups.push(curGroup);
        pendingGroupLabel = null;
      }
      if (curGroup) {
        curGroup.points.push(clampBullet(content));
        continue;
      }
      // two-col：带 emoji 的含冒号行 → 右栏条目卡
      if (layout === 'two-col') {
        const ic2 = stripIcon(content);
        const c2 = /^([^：:]{1,10})[：:]\s*(.+)$/.exec(ic2.text);
        if (ic2.icon && c2) {
          twoColItems.push({ label: c2[1]!.trim(), desc: c2[2]!.trim() });
          continue;
        }
      }
      // 数字度量（"- 10 倍：资料整理提速" → big-number metrics）
      const mt = /^(\d+(?:\.\d+)?\s*[^\s：:]{1,4})\s*[：:]\s*(.+)$/.exec(content);
      // 中文标签度量（"- 全年营收 12.6亿元：同比+23.5%"）
      const lmt = /^([\u4e00-\u9fa5A-Za-z0-9]{1,8})\s+(\d+(?:\.\d+)?)\s*([^\s：:，,]{0,4})\s*[：:]\s*(.+)$/.exec(content);
      // 季度/月份步骤（"- Q1：完成组织调整" / "- 3月：新品立项"）
      const pst = /^(Q[1-4]|[1-4]季度|第[1-4]季度|1[0-2]月|[1-9]月)\s*[：:]\s*(.+)$/.exec(content);
      if (pst && (layout === 'timeline' || olSteps.length > 0)) {
        olSteps.push({ label: pst[1]!, desc: pst[2]!.trim() });
        continue;
      }
      if (lmt && (layout === 'big-number' || layout === 'chart')) {
        const val = `${lmt[2]}${lmt[3] ?? ''}`;
        if (layout === 'big-number') metrics.push({ value: val, label: lmt[1]!, desc: lmt[4]!.trim() });
        else {
          chartLabels.push(lmt[1]!);
          chartValues.push(parseFloat(lmt[2]!));
          chartDescs.push(lmt[4]!.trim());
        }
        continue;
      }
      if (mt && metrics.length >= 0 && !bullets.some((b) => b.includes(content.slice(0, 4)))) {
        metrics.push({ value: mt[1]!.trim(), label: mt[2]!.trim() });
        continue;
      }
      // 宫格/卡片列表（覆盖全部宫格版式；emoji 图标可选）
      if (gridLayouts.includes(layout)) {
        const ic = stripIcon(content);
        const c = /^([^：:]{1,10})[：:]\s*(.+)$/.exec(ic.text);
        if (c) {
          gridCards.push({ icon: ic.icon, title: c[1]!.trim(), desc: c[2]!.trim() });
          continue;
        }
      }
      bullets.push(clampBullet(content));
      continue;
    }
    // compare 子块标签："传统方式：" / "WorkBuddy："（短行以冒号结尾）
    if (/^.{1,12}[：:]$/.test(line)) {
      curGroup = { label: line.replace(/[：:]$/, '').trim(), points: [] };
      groups.push(curGroup);
      continue;
    }
    // 普通段落：首段短文本可作副标题；其余始终存入 content（修复：公式行等
    // 关键文本曾被静默丢弃）。content 与 bullets 可共存，渲染层负责分区展示
    const cleanLine = line.replace(/[#*`]/g, '').trim();
    // 无冒号短行（2~16字符、无标点结尾）→ 疑似 compare 组标签，暂存待下一行
    // 判定：下一行是列表/kv 则成组，否则循环结束后归位为普通段落。
    // 上限 16：覆盖"WorkBuddy 新方式"（英文品牌+中文，12 字符）类组标签
    if (
      cleanLine.length >= 2 &&
      cleanLine.length <= 16 &&
      !/[。！？；，、…\s]$/.test(cleanLine) &&
      !/[：:]$/.test(cleanLine) &&
      cleanLine !== (curGroup?.label ?? '')
    ) {
      pendingGroupLabel = cleanLine;
      continue;
    }
    if (!slide.subtitle && line.length <= 60 && !slide.content) {
      slide.subtitle = cleanLine;
    } else {
      slide.content = slide.content ? `${slide.content}\n${cleanLine}` : cleanLine;
    }
  }
  // 残留暂存组标签（后面没跟列表/kv 行）→ 归位为普通段落
  if (pendingGroupLabel) {
    const lbl = pendingGroupLabel;
    if (!slide.subtitle && !slide.content) slide.subtitle = lbl;
    else slide.content = slide.content ? `${slide.content}\n${lbl}` : lbl;
    pendingGroupLabel = null;
  }

  // 结构化版式升级（compare > big-number > timeline > grid-2x2）
  if (groups.length === 2 && groups.every((g) => g.points.length > 0)) {
    slide.layout = 'compare';
    slide.compare = {
      leftLabel: groups[0]!.label,
      leftPoints: groups[0]!.points.slice(0, 4),
      rightLabel: groups[1]!.label,
      rightPoints: groups[1]!.points.slice(0, 4),
    };
  } else if (layout === 'chart' && chartLabels.length >= 2) {
    // chart 版式：收集的标签/数值组装成 bar 图系列
    slide.chart = { type: 'bar', series: [{ name: slide.title, labels: chartLabels.slice(0, 8), values: chartValues.slice(0, 8) }] };
    if (chartDescs.length > 0) slide.notes = slide.notes ?? chartDescs.slice(0, 4).join('；');
  } else if (layout === 'two-col' && twoColItems.length >= 2) {
    // two-col：带图标 kv 行进右栏 items（左栏 bullets 已分流）
    slide.items = twoColItems.slice(0, 4);
  } else if (layout === 'big-number' && metrics.length >= 2) {
    slide.layout = 'big-number';
    slide.metrics = metrics.slice(0, 3);
  } else if (metrics.length >= 3 && layout === 'content') {
    // 内容页意外攒够度量 → 升级数据大屏（保持旧有行为）
    slide.layout = 'big-number';
    slide.metrics = metrics.slice(0, 3);
  } else if (olSteps.length >= 3) {
    // 有序步骤 ≥3 即升级 timeline（标题已含"流程/步骤"关键词时 layout 本就是
    // timeline，旧条件 `&& layout==='content'` 会导致 steps 永不赋值 → 空时间线）
    slide.layout = 'timeline';
    slide.steps = olSteps.slice(0, 5);
  } else if (gridCards.length >= 3 && gridLayouts.includes(slide.layout)) {
    // 卡片数自适应宫格：3 张 → cards 三卡；4 张 → 2x2；5-6 张 → 3x2；≥7 张 → 4x2。
    // 修复：旧条件 ≥4 导致 cards 版式 3 卡不赋值 → 空卡片页（数据滞留 gridCards）
    slide.cards = gridCards.slice(0, 8);
    if (slide.layout === 'cards' && gridCards.length <= 3) {
      // 保持 cards 三卡版式
    } else if (gridCards.length <= 4) {
      slide.layout = 'grid-2x2';
    } else if (gridCards.length <= 6) {
      slide.layout = 'grid-3x2';
    } else {
      slide.layout = 'grid-4x2';
    }
  }

  if (slide.layout === 'toc') {
    slide.items =
      tocItems.length > 0
        ? tocItems.slice(0, 8)
        : bullets.slice(0, 8).map((b) => ({ label: b }));
  } else if (bullets.length > 0 && !slide.compare && !slide.metrics && !slide.steps && !slide.cards) {
    slide.bullets = bullets.slice(0, 6);
  }
  if (slide.layout === 'cover' && !slide.subtitle && bullets.length > 0) {
    slide.subtitle = bullets.join(' · ').slice(0, 80);
  }
  return slide;
}

/** 按标题正则切分（title + 对应正文）。注意正则必须带 m 标志（^ 匹配行首）。 */
function splitByHeading(text: string, re: RegExp): Array<{ title: string; body: string }> {
  const out: Array<{ title: string; body: string }> = [];
  const marks: Array<{ title: string; start: number; headEnd: number }> = [];
  const rx = new RegExp(re.source, re.flags.includes('g') ? re.flags : `${re.flags}g`);
  let m: RegExpExecArray | null;
  while ((m = rx.exec(text)) !== null) {
    marks.push({ title: m[1] ?? '', start: m.index, headEnd: rx.lastIndex });
  }
  for (let i = 0; i < marks.length; i++) {
    const end = i + 1 < marks.length ? marks[i + 1]!.start : text.length;
    out.push({ title: marks[i]!.title, body: text.slice(marks[i]!.headEnd, end) });
  }
  return out;
}

export function mdToSlides(md: string, fallbackTitle: string): SlideSpec[] {
  if (!md || !md.trim()) {
    return [{ layout: 'cover', title: fallbackTitle, subtitle: '（内容稿为空）' }];
  }
  // 主标题（首个 # 行）与紧随副标题行 → 封面页素材（历史 bug：主标题被丢弃致封面缺失）
  const mainM = /^#\s+(.+)$/m.exec(md);
  const mainTitle = mainM ? mainM[1]!.trim() : '';
  let mainSubtitle = '';
  if (mainM) {
    const after = md.slice(mainM.index + mainM[0].length);
    const firstLine = after
      .split('\n')
      .map((s) => s.trim())
      .find((s) => s && !s.startsWith('#') && s !== '---' && !s.startsWith('>'));
    if (firstLine) mainSubtitle = firstLine.slice(0, 80);
  }
  const text = md
    .replace(/\r\n/g, '\n')
    // 剥离代码围栏（内部 markdown 不参与分页）
    .replace(/```[\s\S]*?```/g, '');

  // 1) 逐页小节（## / ###）：真实"逐页文字内容"格式
  const sections = splitByHeading(text, /^#{2,3}\s+(.+)$/m);
  if (sections.length > 0) {
    const slides = sections.map((s) => parseSection(s.title, s.body.split('\n'), fallbackTitle));
    // 首节非封面 → 用文档主标题补一页封面（修复：# 主标题被丢弃、封面缺失）
    if (mainTitle && slides[0]?.layout !== 'cover') {
      slides.unshift({
        layout: 'cover',
        title: mainTitle,
        subtitle: mainSubtitle || undefined,
      });
    }
    // 旧格式兼容：首节已是封面但缺副标题 → 用主标题行补全
    const first = slides[0]!;
    if (first.layout === 'cover' && !first.subtitle && mainSubtitle) {
      first.subtitle = mainSubtitle;
    }
    return slides.slice(0, MAX_PAGES);
  }

  // 2) 一级标题分节
  const h1 = splitByHeading(text, /^#\s+(.+)$/m);
  if (h1.length > 1) {
    return h1
      .map((s) => parseSection(s.title, s.body.split('\n'), fallbackTitle))
      .slice(0, MAX_PAGES);
  }

  // 3) 无标题结构：按空行分块；单块降级为内容页
  const blocks = text
    .split(/\n\s*\n/)
    .map((b) => b.trim())
    .filter((b) => b.length > 0);
  if (blocks.length <= 1) {
    const firstLine = text.trim().split('\n')[0] ?? fallbackTitle;
    return [
      { layout: 'content', title: firstLine.slice(0, 40), content: text.trim().slice(0, 600) },
    ];
  }
  const slides = blocks.map((b) => parseSection('', b.split('\n'), fallbackTitle));
  const cover: SlideSpec = {
    layout: 'cover',
    title: mainTitle || fallbackTitle,
    subtitle: mainSubtitle || undefined,
  };
  return [cover, ...slides].slice(0, MAX_PAGES);
}
