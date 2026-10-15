/**
 * template-clone 回归测试——固化 E2E 验证过的核心行为（2026-10-12 会话）：
 *  1. 页型识别：toc / section / content-4col / content-3col / closing
 *  2. 槽位角色：真实模板占位符（添加您的标题 / 在这里输入）→ item-title / item-body
 *  3. planClone 按页型分流：封面 CJK 主标题、章节编号、目录条目、metrics 落位
 *  4. 候选页 wanted 优先级（历史缺陷：cards 拿到无卡槽 content 页 → 条目丢失）
 *  5. 占位清理：未填充槽清空，杜绝模板占位文字泄漏
 * 真实模板 E2E（scan+execute 需 python）由 code_output 脚本覆盖，不入 vitest
 */
import { describe, expect, it } from 'vitest';
import {
  classifyTemplatePage,
  planClone,
  type TemplatePage,
} from './template-clone.js';
import type { SlideSpec } from './pptx-engine.js';

/* ───────────── 测试夹具：槽位构造器 ───────────── */

function slot(
  idx: number,
  x: number,
  y: number,
  w: number,
  paras: string[]
): { idx: number; x: number; y: number; w: number; paras: string[] } {
  return { idx, x, y, w, paras };
}

/** 带高度的槽位构造器（面积排序需要真实 h：正文槽高、标签槽矮） */
function slotH(
  idx: number,
  x: number,
  y: number,
  w: number,
  h: number,
  paras: string[]
): { idx: number; x: number; y: number; w: number; h: number; paras: string[] } {
  return { idx, x, y, w, h, paras };
}

/** 39 页真实模板的代表页结构（walk 修复后扫描快照的忠实摘录） */
const COVER: Array<{ idx: number; x: number; y: number; w: number; paras: string[] }> = [
  slot(0, 4.58, 4.64, 6.89, ['2018 YEAR-END SUMMARY OF WORK']),
  slot(1, 4.55, 3.3, 5.51, ['BUSINESS POWERPOINT']),
  slot(2, 4.53, 3.79, 7.61, ['工作总结暨新年工作计划']),
  slot(3, 4.53, 1.32, 4.81, ['2026']),
];
const TOC = [
  slot(0, 3.15, 2.84, 2.05, ['目 录']),
  slot(1, 2.79, 3.61, 2.76, ['CONTENTS']),
  slot(2, 5.81, 1.84, 2.71, ['年度工作概述']),
  slot(3, 5.04, 1.78, 0.66, ['01']),
  slot(4, 5.81, 2.67, 2.71, ['工作完成情况']),
  slot(5, 5.04, 2.6, 0.66, ['02']),
  slot(6, 5.81, 3.5, 2.71, ['项目成果展示']),
  slot(7, 5.04, 3.45, 0.66, ['03']),
  slot(8, 5.81, 4.33, 2.71, ['工作不足之处']),
  slot(9, 5.04, 4.28, 0.66, ['04']),
];
const SECTION = [
  slot(0, 5.41, 1.92, 2.52, ['01']),
  slot(1, 2.71, 4.28, 7.92, ['年度工作概述']),
  slot(2, 4.2, 5.53, 4.94, ['JOB DESCRIPTION']),
];
/** P9 content-3col：3 组（标题占位 + 正文占位）+ 3 个短编号 */
const CONTENT_3COL = [
  slot(0, 1.42, 0.31, 5.98, ['重点工作回顾']),
  slot(1, 5.1, 5.77, 0.26, ['1']),
  slot(2, 7.73, 4.59, 0.34, ['2']),
  slot(3, 9.72, 2.98, 0.43, ['3']),
  slot(4, 1.75, 4.92, 3.24, ['在这里输入您的文字内容，或者将您的文字内容复制在这里']),
  slot(5, 1.75, 4.5, 2.17, ['添加您的标题']),
  slot(6, 5.84, 2.52, 3.28, ['在这里输入您的文字内容，或者将您的文字内容复制在这里']),
  slot(7, 5.84, 2.1, 2.17, ['添加您的标题']),
  slot(8, 8.15, 5.46, 3.2, ['在这里输入您的文字内容，或者将您的文字内容复制在这里']),
  slot(9, 8.15, 5.03, 2.17, ['添加您的标题']),
];
/** P6 content（团队成员页）：无 item-title 槽 */
const CONTENT_PLAIN = [
  slot(0, 1.42, 0.31, 5.98, ['团队成员介绍']),
  slot(1, 2.03, 3.89, 1.14, ['代用名']),
  slot(2, 1.76, 4.23, 1.67, ['部门职务']),
  slot(3, 1.76, 4.71, 1.67, ['在这里输入您的文字内容，或者将您的文字内容复制在这里']),
  slot(4, 4.74, 3.89, 1.14, ['代用名']),
  slot(5, 4.48, 4.23, 1.67, ['部门职务']),
  slot(6, 4.48, 4.71, 1.67, ['在这里输入您的文字内容，或者将您的文字内容复制在这里']),
];
/** P6 人名页（带真实高度）：代用名 0.40h / 部门职务 0.34h / 正文 1.04h */
const CONTENT_P4 = [
  slotH(0, 1.42, 0.31, 5.98, 0.64, ['语义理解与任务规划']),
  slotH(1, 2.03, 3.89, 1.14, 0.40, ['代用名']),
  slotH(2, 1.76, 4.23, 1.67, 0.34, ['部门职务']),
  slotH(3, 1.76, 4.71, 1.67, 1.04, ['在这里输入您的文字内容，或者将您的文字内容复制在这里']),
  slotH(4, 4.74, 3.89, 1.14, 0.40, ['代用名']),
  slotH(5, 4.48, 4.23, 1.67, 0.34, ['部门职务']),
  slotH(6, 4.48, 4.71, 1.67, 1.04, ['在这里输入您的文字内容，或者将您的文字内容复制在这里']),
];
/** P7 大事件页：含 202X-03 日期大字槽 + 5 组（PROJECT n + 日期 + 卡片正文） */
const CONTENT_DATE = [
  slot(0, 1.42, 0.31, 5.98, ['年度工作大事件']),
  slot(1, 1.56, 5.85, 1.04, ['PROJECT 1']),
  slot(2, 1.37, 3.25, 2.17, ['202X-03']),
  slot(3, 1.11, 2.10, 1.94, ['在这里输入您的文字内容，或者将您的文字内容复制在这里，在这里输入您的文字内容']),
  slot(4, 3.86, 1.82, 1.07, ['PROJECT 2']),
  slot(5, 3.68, 4.08, 2.17, ['202X-06']),
  slot(6, 3.43, 4.81, 1.94, ['在这里输入您的文字内容，或者将您的文字内容复制在这里，在这里输入您的文字内容']),
];
const CLOSING = [
  slot(0, 4.79, 1.93, 7.58, ['THANKS']),
  slot(1, 4.85, 4.7, 6.89, ['2018 YEAR-END SUMMARY OF WORK']),
  slot(2, 4.85, 4.0, 6.72, ['感谢公司领导和全体同事的全力支持']),
];

function poolOf(...defs: Array<[number, Array<{ idx: number; x: number; y: number; w: number; paras: string[] }>]>): TemplatePage[] {
  return defs.map(([page, boxes]) => classifyTemplatePage(page, boxes));
}

/* ───────────── 1. 页型识别 ───────────── */

describe('classifyTemplatePage 页型识别', () => {
  it('目录页：含"目 录" → toc', () => {
    expect(classifyTemplatePage(3, TOC).kind).toBe('toc');
  });

  it('章节页：编号+中文标题+英文装饰（槽≤6、无条目卡）→ section', () => {
    expect(classifyTemplatePage(4, SECTION).kind).toBe('section');
  });

  it('结尾页：THANKS → closing', () => {
    expect(classifyTemplatePage(39, CLOSING).kind).toBe('closing');
  });

  it('内容页：占位标题卡 ≥3 → content-3col（P9 结构）', () => {
    expect(classifyTemplatePage(9, CONTENT_3COL).kind).toBe('content-3col');
  });

  it('内容页：无条目卡 → content（P6 结构）', () => {
    expect(classifyTemplatePage(6, CONTENT_PLAIN).kind).toBe('content');
  });

  it('占位符角色：添加您的标题 → item-title；在这里输入 → item-body', () => {
    const page = classifyTemplatePage(9, CONTENT_3COL);
    const roles = new Map(page.slots.map((s) => [s.paras[0] ?? '', s.role]));
    expect(roles.get('添加您的标题')).toBe('item-title');
    expect(roles.get('在这里输入您的文字内容，或者将您的文字内容复制在这里')).toBe('item-body');
  });
});

/* ───────────── 2. planClone 按页型分流 ───────────── */

describe('planClone 槽位映射', () => {
  it('封面：主标题进 CJK 最多的槽，副标题进最下方英文槽', () => {
    const pool = poolOf([1, COVER]);
    const plan = planClone(
      { layout: 'cover', title: '2026年度工作总结汇报', subtitle: '2026 YEAR-END SUMMARY OF WORK' },
      pool,
      new Set()
    )!;
    expect(plan).not.toBeNull();
    expect(plan.replacements[2]).toBe('2026年度工作总结汇报'); // CJK 主标题槽
    expect(plan.replacements[0]).toBe('2026 YEAR-END SUMMARY OF WORK'); // y=4.64 最下方英文槽
  });

  it('章节页：短副标题（01）进编号槽，标题进中文槽', () => {
    const pool = poolOf([4, SECTION]);
    const plan = planClone(
      { layout: 'section', title: '年度工作概况', subtitle: '01' },
      pool,
      new Set()
    )!;
    expect(plan.replacements[0]).toBe('01');
    expect(plan.replacements[1]).toBe('年度工作概况');
  });

  it('目录页：条目标签按 y 序落位，多余条目槽与编号清空', () => {
    const pool = poolOf([3, TOC]);
    const plan = planClone(
      {
        layout: 'toc',
        title: '目录',
        items: [
          { label: '年度工作概况' },
          { label: '重点工作回顾' },
          { label: '团队建设情况' },
        ],
      },
      pool,
      new Set()
    )!;
    expect(plan.replacements[0]).toBe('目录');
    expect(plan.replacements[2]).toBe('年度工作概况');
    expect(plan.replacements[4]).toBe('重点工作回顾');
    expect(plan.replacements[6]).toBe('团队建设情况');
    expect(plan.replacements[8]).toBe(''); // 第 4 条目槽：无条目 → 清空
    expect(plan.replacements[9]).toBe(''); // 第 4 编号槽：无条目 → 清空
  });

  it('数据页：metrics 值进 item-title（视觉突出），标签+desc 进 item-body', () => {
    const pool = poolOf([9, CONTENT_3COL]);
    const plan = planClone(
      {
        layout: 'big-number',
        title: '数据总览',
        metrics: [
          { value: '12.6亿元', label: '全年营收', desc: '同比+23.5%' },
          { value: '486家', label: '服务客户', desc: '净增142家' },
        ],
      },
      pool,
      new Set()
    )!;
    expect(plan.replacements[0]).toBe('数据总览');
    // item-title 按 y 序：(7)y2.1 → (5)y4.5 → (9)y5.03
    expect(plan.replacements[7]).toBe('12.6亿元');
    expect(plan.replacements[5]).toBe('486家');
    expect(plan.replacements[6]).toContain('全年营收');
    expect(plan.replacements[4]).toContain('服务客户');
  });

  it('cards 页：卡片标题+desc 进 item-title/item-body 配对槽', () => {
    const pool = poolOf([9, CONTENT_3COL]);
    const plan = planClone(
      {
        layout: 'cards',
        title: '重点战役复盘',
        cards: [
          { title: '智能化项目攻坚', desc: '交付17个AI场景，形成可复用中台' },
          { title: '大客户深度经营', desc: 'Top20客户贡献营收占比升至58%' },
        ],
      },
      pool,
      new Set()
    )!;
    expect(plan.replacements[7]).toBe('智能化项目攻坚');
    expect(plan.replacements[6]).toBe('交付17个AI场景，形成可复用中台');
    expect(plan.replacements[5]).toBe('大客户深度经营');
    expect(plan.replacements[4]).toBe('Top20客户贡献营收占比升至58%');
  });

  it('结尾页：subtitle 进 CJK 槽，旧英文标语清空', () => {
    const pool = poolOf([39, CLOSING]);
    const plan = planClone(
      { layout: 'closing', title: 'THANKS', subtitle: '感谢聆听 · 期待2027' },
      pool,
      new Set()
    )!;
    expect(plan.replacements[2]).toBe('感谢聆听 · 期待2027');
    expect(plan.replacements[1]).toBe(''); // 2018 旧标语 → 清空
  });

  it('占位清理：未填充的 item-title/item-body 清空（防模板占位泄漏）', () => {
    const pool = poolOf([9, CONTENT_3COL]);
    const plan = planClone({ layout: 'content', title: '概览' }, pool, new Set())!;
    // 3 组卡槽全部无内容 → 全部清空
    expect(plan.replacements[5]).toBe('');
    expect(plan.replacements[7]).toBe('');
    expect(plan.replacements[9]).toBe('');
    expect(plan.replacements[4]).toBe('');
    expect(plan.replacements[6]).toBe('');
    expect(plan.replacements[8]).toBe('');
  });

  it('content 页 bullets 兜底：无卡槽时按 y 序填 body 槽', () => {
    const pool = poolOf([6, CONTENT_PLAIN]);
    const plan = planClone(
      { layout: 'content', title: '团队介绍', bullets: ['第一条要点', '第二条要点'] },
      pool,
      new Set()
    )!;
    const texts = Object.values(plan.replacements);
    expect(texts).toContain('第一条要点');
    expect(texts).toContain('第二条要点');
  });

  it('date 槽（202X-03）不被正文占用：bullets 分流到宽 body 槽（P5 位置错事故）', () => {
    const pool = poolOf([7, CONTENT_DATE]);
    const plan = planClone(
      {
        layout: 'content',
        title: 'RAG 检索增强',
        bullets: ['📚 片段检索：先从绑定知识库与文档取相关片段', '✅ 有据可依：基于资料组织内容，减少凭空编造'],
      },
      pool,
      new Set()
    )!;
    // bullets 必须落到宽的卡片正文槽（idx 3/6，1.94in），而非 2.17in 的日期大字槽
    expect(plan.replacements[3]).toContain('片段检索');
    expect(plan.replacements[6]).toContain('有据可依');
    // 日期槽 idx 2/5 保持模板原文（未被清空也未被塞正文）
    expect(plan.replacements[2]).toBeUndefined();
    expect(plan.replacements[5]).toBeUndefined();
  });

  it('quote 金句页：金句+署名落位，不再空页（P9 空页事故）', () => {
    const pool = poolOf([9, CONTENT_3COL]);
    const plan = planClone(
      {
        layout: 'quote',
        title: '核心金句',
        quote: { text: 'AI 负责内容与逻辑，渲染器负责格式与兼容性', author: 'WorkBuddy 设计理念' },
      },
      pool,
      new Set()
    )!;
    const texts = Object.values(plan.replacements);
    const joined = texts.join('\n');
    expect(joined).toContain('AI 负责内容');
    expect(joined).toContain('渲染器负责');
    expect(texts).toContain('WorkBuddy 设计理念');
  });

  it('窄人名槽（1.14in 代用名）不再被整句塞入：bullets 走宽 body 槽（P4 排版事故）', () => {
    const pool = poolOf([6, CONTENT_P4]);
    const plan = planClone(
      {
        layout: 'content',
        title: '语义理解与任务规划',
        bullets: ['🔍 要素识别：解析主题、页数、风格、受众', '🎯 受众适配：给老板看与给客户看，侧重不同'],
      },
      pool,
      new Set()
    )!;
    // 1.14in 窄槽（idx 1/4，代用名）不得承接整句要点
    expect(plan.replacements[1]).toBe('');
    expect(plan.replacements[4]).toBe('');
    // 整句进面积最大的正文槽（idx 3/6，1.67×1.04）
    expect(plan.replacements[3]).toContain('要素识别');
    expect(plan.replacements[6]).toContain('受众适配');
  });

  it('长文拆分：>24 字且槽高 >0.9 时按逗号拆两段填入（P4 排版事故）', () => {
    const pool = poolOf([6, CONTENT_P4]);
    const plan = planClone(
      {
        layout: 'content',
        title: '语义理解',
        bullets: ['要素识别，解析主题，页数，风格，受众与场景等关键信息'],
      },
      pool,
      new Set()
    )!;
    const filled = Object.values(plan.replacements).find((v) => v.includes('要素识别'));
    expect(filled).toBeDefined();
    expect(filled!.includes('\n')).toBe(true); // 拆成两段
  });

  it('toc 多余条目组进入 deletions（P2 空框事故）', () => {
    const pool = poolOf([3, TOC]);
    const plan = planClone(
      {
        layout: 'toc',
        title: '目录',
        items: [
          { label: '语义理解与任务规划' },
          { label: 'RAG 检索增强内容' },
          { label: '结构化大纲与风格' },
        ],
      },
      pool,
      new Set()
    )!;
    // 模板 4 个条目槽 vs 3 条内容 → 第 4 条目槽进入 deletions 且被清空
    expect(plan.deletions).toBeDefined();
    expect(plan.deletions!).toContain(8); // idx 8 = 第 4 个 item-title 槽
    expect(plan.replacements[8]).toBe('');
  });

  it('toc 条目与模板槽数一致时无 deletions', () => {
    const pool = poolOf([3, TOC]);
    const plan = planClone(
      {
        layout: 'toc',
        title: '目录',
        items: [
          { label: '年度工作概述' },
          { label: '工作完成情况' },
          { label: '项目成果展示' },
          { label: '工作不足之处' },
        ],
      },
      pool,
      new Set()
    )!;
    expect(plan.deletions).toBeUndefined();
  });
});

/* ───────────── 3. 候选页 wanted 优先级（历史缺陷固化） ───────────── */

describe('planClone 候选页优先级', () => {
  it('cards 内容必须优先匹配有卡槽的 content-3col，而非页号更小的无卡槽 content 页', () => {
    // 页号序：P6(无卡槽) 在前、P9(有卡槽) 在后——历史缺陷会错误选中 P6
    const pool = poolOf([6, CONTENT_PLAIN], [9, CONTENT_3COL]);
    const plan = planClone(
      {
        layout: 'cards',
        title: '重点战役复盘',
        cards: [{ title: '智能化项目攻坚', desc: '交付17个AI场景' }],
      },
      pool,
      new Set()
    )!;
    expect(plan.templatePage).toBe(9); // 必须拿到有 item-title 槽的页
    expect(plan.replacements[7]).toBe('智能化项目攻坚');
  });

  it('全部 3col 用尽后才降级到无卡槽 content 页', () => {
    const pool = poolOf([6, CONTENT_PLAIN], [9, CONTENT_3COL]);
    const used = new Set([9]); // 唯一 3col 已被占用
    const plan = planClone(
      { layout: 'cards', title: '第二组卡片', cards: [{ title: '卡片A', desc: '说明' }] },
      pool,
      used
    )!;
    expect(plan.templatePage).toBe(6); // 降级
    expect(plan.replacements[0]).toBe('第二组卡片');
  });

  it('同 kind 用尽 → 允许复用（最后一道兜底）', () => {
    const pool = poolOf([9, CONTENT_3COL]);
    const plan1 = planClone({ layout: 'cards', title: 'A', cards: [{ title: 'x' }] }, pool, new Set())!;
    const plan2 = planClone({ layout: 'cards', title: 'B', cards: [{ title: 'y' }] }, pool, new Set())!;
    expect(plan1).not.toBeNull();
    expect(plan2).not.toBeNull();
    expect(plan2.templatePage).toBe(9);
  });
});
