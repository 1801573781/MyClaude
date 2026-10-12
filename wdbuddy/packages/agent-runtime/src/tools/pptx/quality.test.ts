import { describe, it, expect } from 'vitest';
import { mdToSlides } from './md-to-slides.js';
import { measureText, fitFontSize, metricBasePt, renderSlidesTo } from './pptx-engine.js';
import { getTheme } from './themes.js';
import { injectEaFont, patchThemeEa } from '../../tools/office.js';

/** 渲染质量回归测试——历史踩坑全量固化。
 *  每个用例对应一次真实事故，改渲染/解析代码必须保持全绿，
 *  否则视为回归（新问题也要先固化成用例再修复） */

describe('文本宽度自适应（P4 数字换行事故）', () => {
  it('measureText：汉字按 1em、半角按 0.55em 估算宽度', () => {
    const fs76 = 76 / 72;
    expect(measureText('486家', 76)).toBeCloseTo((3 * 0.55 + 1) * fs76, 2);
    expect(measureText('12.6亿元', 76)).toBeGreaterThan(measureText('486家', 76));
  });

  it('fitFontSize：三栏下短值保 76pt，宽值自动缩号且缩后放得下', () => {
    const colW = 11.9 / 3 - 0.25; // 三栏分栏可用宽（76pt 基准）
    expect(fitFontSize('486家', colW, 76)).toBe(76);
    const fs = fitFontSize('12.6亿元', colW, 76);
    expect(fs).toBeLessThan(76);
    expect(fs).toBeGreaterThanOrEqual(30);
    // 缩号后确实放得下
    expect(measureText('12.6亿元', fs)).toBeLessThanOrEqual(colW + 0.01);
  });

  it('四栏基准递减至 60pt（metricBasePt），短值单行', () => {
    expect(metricBasePt(3)).toBe(76);
    expect(metricBasePt(4)).toBe(60);
    const colW = 11.9 / 4 - 0.25;
    expect(fitFontSize('486家', colW, metricBasePt(4))).toBe(60);
    expect(measureText('486家', 60)).toBeLessThanOrEqual(colW + 0.01);
  });

  it('极端长值缩到下限仍超宽 → 检测可判（调用方允许换行）', () => {
    const colW = 11.9 / 3 - 0.25;
    const fs = fitFontSize('同比增长23.5个百分点', colW, 76);
    expect(fs).toBeGreaterThanOrEqual(30);
    // 到下限仍超宽：调用方通过 measureText 二次判定走换行路径
    expect(measureText('同比增长23.5个百分点', fs)).toBeGreaterThan(colW);
  });
});

describe('解析形态泛化（历次空页事故）', () => {
  const cases: Array<{ name: string; md: string; check: (s: any[]) => void }> = [
    {
      name: '中文序数步骤（论文三步走）',
      md: '## P 场景示范\n- 版式：timeline\n- 📌 第一步：输入题目\n- 📌 第二步：指令检索\n- 📌 第三步：粘贴初稿',
      check: (s) => expect(s[0]!.steps?.length).toBe(3),
    },
    {
      name: '季度/月份步骤（Q1/3月）',
      md: '## P 里程碑\n- 版式：timeline\n- 3月：新品立项\n- 6月：基地投产\n- 9月：海外首单\n- 11月：中台上线',
      check: (s) => expect(s[0]!.steps?.length).toBe(4),
    },
    {
      name: '无冒号组标签对比页（传统 vs 新方式）',
      md: '## P 对比\n- 版式：compare\n传统学习方式\n- 查资料：翻20个网页\n- 写总结：抓不住重点\nWorkBuddy 新方式\n- 查资料：10分钟出汇总\n- 写总结：自动标出处',
      check: (s) => {
        expect(s[0]!.compare?.leftLabel).toBe('传统学习方式');
        expect(s[0]!.compare?.rightLabel).toBe('WorkBuddy 新方式');
        expect(s[0]!.compare?.leftPoints.length).toBe(2);
      },
    },
    {
      name: '中文标签度量（big-number）',
      md: '## P 核心指标\n- 版式：big-number\n- 全年营收 12.6亿元：同比+23.5%\n- 净利润 1.89亿元：净利率15%',
      check: (s) => expect(s[0]!.metrics?.length).toBe(2),
    },
    {
      name: 'chart 版式收集标签数值',
      md: '## P 季度营收\n- 版式：chart\n- 一季度 3.1亿元：夯实基础\n- 二季度 3.3亿元：新品拉动\n- 三季度 3.5亿元：渠道见效',
      check: (s) => {
        expect(s[0]!.chart?.series[0]?.labels).toEqual(['一季度', '二季度', '三季度']);
        expect(s[0]!.chart?.series[0]?.values).toEqual([3.1, 3.3, 3.5]);
      },
    },
    {
      name: 'quote 金句/署名',
      md: '## P 感悟\n- 版式：quote\n- 金句：增长的上限，取决于组织能力的高度。\n- 署名：2026年度经营复盘共识',
      check: (s) => {
        expect(s[0]!.quote?.text).toContain('组织能力');
        expect(s[0]!.quote?.author).toContain('复盘');
      },
    },
    {
      name: 'two-col 带 emoji 行进右栏',
      md: '## P 团队\n- 版式：two-col\n- 全员规模稳定286人\n- 📊 人效：人均营收156万元\n- 🎓 梯队：内部晋升12人',
      check: (s) => {
        expect(s[0]!.items?.length).toBe(2);
        expect(s[0]!.bullets?.length).toBe(1);
      },
    },
    {
      name: 'cards 三卡（P4 空卡事故）',
      md: '## P 核心功能（三卡片）\n- 智能问答：随时提问秒级响应\n- 任务管理：待办清单一站式\n- 文档助手：资料总结自动生成',
      check: (s) => expect(s[0]!.cards?.length).toBe(3),
    },
    {
      name: '六宫格/八宫格自适应',
      md: '## P 八大场景\n- 📚 课程学习：复习提纲\n- ✍️ 论文写作：文献检索\n- 📊 数据作业：图表生成\n- 🎤 课堂展示：讲稿PPT\n- 🗂 资料整理：结构笔记\n- 📧 邮件文书：通知起草\n- 🗓 日程管理：待办规划\n- 🎯 竞赛备赛：方案初稿',
      check: (s) => {
        expect(s[0]!.cards?.length).toBe(8);
        expect(s[0]!.layout).toBe('grid-4x2');
      },
    },
  ];

  for (const c of cases) {
    it(c.name, () => {
      const slides = mdToSlides(c.md, '测试');
      c.check(slides);
    });
  }
});

describe('零空页保证（renderBulletsFallback）', () => {
  it('结构化数据缺失时页面仍有内容渲染', () => {
    const slides = mdToSlides('## P 流程\n- 版式：timeline\n- 要点甲：说明一\n- 要点乙：说明二\n- 要点丙：说明三', '测试');
    // timeline 无 steps 但有 bullets → 渲染层兜底，SlideSpec 必须携带 bullets
    expect((slides[0]!.bullets?.length ?? 0)).toBeGreaterThanOrEqual(2);
  });
});

describe('中文 ea 字体注入（图表轴标签等线事故）', () => {
  it('injectEaFont：latin 标签后补 ea 标签，已有 ea 不重复', () => {
    const xml = '<a:latin typeface="Microsoft YaHei"/>';
    const patched = injectEaFont(xml);
    expect(patched).toBe('<a:latin typeface="Microsoft YaHei"/><a:ea typeface="Microsoft YaHei"/>');
    // 已有 ea → 不重复注入
    expect(injectEaFont(patched)).toBe(patched);
    // 多处注入
    const multi = '<a:latin typeface="Microsoft YaHei"/>x<a:latin typeface="Microsoft YaHei"/>';
    expect(injectEaFont(multi).match(/<a:ea /g)!.length).toBe(2);
    // 其他字体不受影响
    expect(injectEaFont('<a:latin typeface="Arial"/>')).toBe('<a:latin typeface="Arial"/>');
  });

  it('patchThemeEa：主题空 ea 填充为微软雅黑', () => {
    expect(patchThemeEa('<a:ea typeface=""/>')).toBe('<a:ea typeface="Microsoft YaHei"/>');
    expect(patchThemeEa('<a:ea typeface="宋体"/>')).toBe('<a:ea typeface="宋体"/>');
  });

  it('catAx/valAx/dLbls 场景：注入后中文字符走雅黑路径', () => {
    const chartAx = '<c:catAx><a:defRPr><a:latin typeface="Microsoft YaHei"/></a:defRPr></c:catAx>';
    const patched = injectEaFont(chartAx);
    expect(patched).toContain('<a:ea typeface="Microsoft YaHei"/>');
  });
});

describe('全量渲染冒烟（16 版式 + 深/浅主题）', () => {
  const layouts: Array<[string, any]> = [
    ['cover', { title: 'T', subtitle: 'S' }],
    ['toc', { title: '目录', items: [{ label: '一' }] }],
    ['section', { title: '章节', subtitle: '01' }],
    ['content', { title: '内容', bullets: ['a：b', 'c：d'] }],
    ['two-col', { title: '双栏', bullets: ['x'], items: [{ label: 'y', desc: 'z' }] }],
    ['cards', { title: '三卡', cards: [{ title: 'a', desc: 'b' }, { title: 'c' }, { title: 'd' }] }],
    ['timeline', { title: '流程', steps: [{ label: '1', desc: 'a' }, { label: '2', desc: 'b' }, { label: '3' }] }],
    ['grid-2x2', { title: '宫格', cards: [{ title: 'a' }, { title: 'b' }, { title: 'c' }, { title: 'd' }] }],
    ['grid-3x2', { title: '六格', cards: [{ title: 'a' }, { title: 'b' }, { title: 'c' }, { title: 'd' }, { title: 'e' }, { title: 'f' }] }],
    ['grid-4x2', { title: '八格', cards: Array.from({ length: 8 }, (_, i) => ({ title: `卡${i}` })) }],
    ['quote', { title: '金句', quote: { text: '道生一', author: '老子' } }],
    ['compare', { title: '对比', compare: { leftLabel: '旧', leftPoints: ['a'], rightLabel: '新', rightPoints: ['b'] } }],
    ['big-number', { title: '数据', metrics: [{ value: '12.6亿元', label: '营收' }, { value: '486家', label: '客户' }, { value: '96.8%', label: '回款' }] }],
    ['chart', { title: '图表', chart: { type: 'bar', series: [{ name: 'S', labels: ['Q1', 'Q2'], values: [1, 2] }] } }],
    ['image-text', { title: '图文', bullets: ['a'], image: { caption: '示意图' } }],
    ['closing', { title: '谢谢' }],
  ];

  for (const themeId of ['tech-dark', 'fresh-mint', 'business-blue']) {
    it(`${themeId} 主题 16 版式全渲染不抛错`, () => {
      const pptxStub = {
        addSlide: () => ({
          addText: () => undefined,
          addShape: () => undefined,
          addNotes: () => undefined,
          addChart: () => undefined,
        }),
      };
      expect(() => renderSlidesTo(pptxStub as any, layouts.map(([l, data]) => ({ layout: l, ...data })) as any, getTheme(themeId))).not.toThrow();
    });
  }
});
