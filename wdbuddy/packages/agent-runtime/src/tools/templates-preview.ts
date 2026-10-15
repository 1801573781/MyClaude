/**
 * 模版库预览数据（10 主题 × 迷你 deck + 14 版式演示）
 * 供 main 进程经 JSON-RPC templates/preview 调用。
 * 背景：agent-runtime 是 ESM 包且 package.json exports 只暴露 "."，
 * main 进程（CJS）既无法 require 也无法包名子路径导入——渲染逻辑必须在
 * 本进程（ESM 入口，import 自家模块零障碍）完成，经 stdio RPC 回传。
 * 主题/版式是静态设计系统：一次生成全量缓存，终身复用。
 */
import type { TemplatesPreviewResult, CloneTemplateInfo } from '@wdbuddy/shared-types';
import { THEMES } from './pptx/themes.js';
import { buildHtmlDeck } from './pptx/html-engine.js';
import type { SlideSpec } from './pptx/pptx-engine.js';
import { CLONE_TEMPLATES, templatesDir } from './pptx/clone-registry.js';
import { dumpTemplateVisual, buildColorPreviewHtml } from './pptx/template-preview.js';
import * as path from 'node:path';
import * as fs from 'node:fs';

let cache: TemplatesPreviewResult | null = null;

/** 每主题迷你 deck：覆盖 5 种核心版式 */
function miniDeck(name: string): SlideSpec[] {
  return [
    { layout: 'cover', title: name, subtitle: 'WDBuddy 主题模版' },
    {
      layout: 'toc',
      title: '目录',
      items: [
        { label: '什么是 WorkBuddy', desc: '产品定位' },
        { label: '核心能力', desc: '四大功能' },
        { label: '工作流程', desc: '四步闭环' },
        { label: '使用场景', desc: '谁能用在哪' },
      ],
    },
    { layout: 'content', title: '内容要点页', bullets: ['要点一：简洁有力', '要点二：视觉层次', '要点三：重点突出', '要点四：风格统一'] },
    {
      layout: 'timeline',
      title: '流程时间线',
      steps: [
        { label: '明确目标', desc: '你说需求' },
        { label: '制定计划', desc: 'AI 拆步骤' },
        { label: '执行任务', desc: '自动调工具' },
        { label: '交付成果', desc: '生成文件' },
      ],
    },
    {
      layout: 'big-number',
      title: '数据大屏',
      metrics: [
        { value: '10倍', label: '效率提升', desc: '资料整理提速' },
        { value: '1分钟', label: '出稿速度', desc: '秒级生成' },
        { value: '24h', label: '全天在线', desc: '随叫随到' },
      ],
    },
    { layout: 'closing', title: '谢谢观看', subtitle: `WDBuddy · ${name}` },
  ];
}

/** 14 种版式演示 deck（商务蓝主题，每页一种版式） */
function layoutsDeck(): SlideSpec[] {
  return [
    { layout: 'cover', title: '14 种版式总览', subtitle: '每页演示一种版式（←→ 键翻页）' },
    { layout: 'toc', title: '① 目录 toc', items: [{ label: '条目一', desc: '编号+标题+说明' }, { label: '条目二', desc: '最多 8 条' }] },
    { layout: 'section', subtitle: '02', title: '② 章节页 section', bullets: ['大编号 + 章节标题'] },
    { layout: 'content', title: '③ 要点 content', bullets: ['要点一', '要点二', '要点三'] },
    { layout: 'two-col', title: '④ 双栏 two-col', bullets: ['左侧要点列表'], items: [{ label: '右侧条目卡片', desc: '最多 4 个' }] },
    { layout: 'cards', title: '⑤ 三卡片 cards', cards: [{ icon: '⚡', title: '卡片一', desc: '图标+标题+说明' }, { icon: '🛠', title: '卡片二', desc: '三列布局' }, { icon: '🔒', title: '卡片三', desc: '强调色顶条' }] },
    { layout: 'timeline', title: '⑥ 时间线 timeline', steps: [{ label: '步骤一', desc: '上下交错' }, { label: '步骤二', desc: '节点连线' }, { label: '步骤三', desc: '最多 5 步' }] },
    { layout: 'grid-2x2', title: '⑦ 四宫格 grid-2x2', cards: [{ title: '宫格一', desc: '编号徽章' }, { title: '宫格二', desc: '2×2 布局' }, { title: '宫格三', desc: '标题+说明' }, { title: '宫格四', desc: '卡片阴影' }] },
    { layout: 'quote', title: '⑧ 金句 quote', quote: { text: '把重复劳动交给 AI，把创造力留给自己。', author: 'WDBuddy' } },
    { layout: 'compare', title: '⑨ 对比 compare', compare: { leftLabel: '传统方式', leftPoints: ['手动搜索', '排版繁琐'], rightLabel: 'WorkBuddy', rightPoints: ['一句话下达', '秒级生成'] } },
    { layout: 'big-number', title: '⑩ 数据大屏 big-number', metrics: [{ value: '99%', label: '满意度', desc: '深底强调' }, { value: '3秒', label: '响应', desc: '大字号' }] },
    { layout: 'chart', title: '⑪ 图表 chart', chart: { type: 'bar', series: [{ name: '销量', labels: ['Q1', 'Q2', 'Q3', 'Q4'], values: [100, 180, 240, 320] }] } },
    { layout: 'image-text', title: '⑫ 图文 image-text', image: { side: 'left', caption: '图片占位区' }, bullets: ['右侧要点说明', '适合案例展示'] },
    { layout: 'closing', title: '⑭ 结尾 closing', subtitle: '谢谢观看 · 行动号召' },
  ];
}

/* ───────────── 克隆模板预览（内置第三方 pptx → 真彩 SVG deck） ───────────── */

/** 扫描全部内置克隆模板 → CloneTemplateInfo[]（文件缺失/扫描失败跳过，不阻断） */
function buildCloneTemplateInfos(): CloneTemplateInfo[] {
  const out: CloneTemplateInfo[] = [];
  for (const def of CLONE_TEMPLATES) {
    const file = path.join(templatesDir(), def.file);
    if (!fs.existsSync(file)) continue;
    const dump = dumpTemplateVisual(file);
    if (!dump.ok || dump.slides.length === 0) continue;
    const previewHtml = buildColorPreviewHtml(def.name, dump);
    out.push({
      id: def.id,
      name: def.name,
      vibe: def.vibe,
      tags: def.tags,
      pages: dump.slides.length,
      kinds: { '全页预览': dump.slides.length },
      previewHtml,
    });
  }
  return out;
}

export function buildTemplatesPreview(): TemplatesPreviewResult {
  if (cache) return cache;
  const htmls: Record<string, string> = {};
  const themes = THEMES.map((t) => {
    htmls[t.id] = buildHtmlDeck(`${t.name} 模版预览`, miniDeck(t.name), t);
    return {
      id: t.id,
      name: t.name,
      vibe: t.vibe,
      colors: [`#${t.primary}`, `#${t.secondary}`, `#${t.accent}`, `#${t.bgDark}`, `#${t.bgLight}`],
    };
  });
  const bizBlue = THEMES.find((t) => t.id === 'business-blue') ?? THEMES[0]!;
  const layoutsHtml = buildHtmlDeck('14 种版式演示', layoutsDeck(), bizBlue);
  const cloneTemplates = buildCloneTemplateInfos();
  cache = { themes, htmls, layoutsHtml, cloneTemplates };
  return cache;
}
