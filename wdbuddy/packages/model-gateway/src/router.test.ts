import { describe, expect, it } from 'vitest';
import { routeAuto, detectCodeRelated, detectResearch } from './router.js';

describe('routeAuto 路由表', () => {
  it('长上下文优先路由 Kimi', () => {
    const r = routeAuto({ mode: 'craft', longContext: true, codeRelated: true, research: false });
    expect(r.model).toBe('kimi');
  });

  it('深度调研路由 DeepSeek', () => {
    const r = routeAuto({ mode: 'craft', longContext: false, codeRelated: false, research: true });
    expect(r.model).toBe('deepseek');
  });

  it('代码类任务路由 DeepSeek', () => {
    const r = routeAuto({ mode: 'craft', longContext: false, codeRelated: true, research: false });
    expect(r.model).toBe('deepseek');
  });

  it('轻量问答路由 GLM', () => {
    const r = routeAuto({ mode: 'ask', longContext: false, codeRelated: false, research: false });
    expect(r.model).toBe('zhipu-glm');
  });

  it('craft 无特征兜底 DeepSeek', () => {
    const r = routeAuto({ mode: 'craft', longContext: false, codeRelated: false, research: false });
    expect(r.model).toBe('deepseek');
  });

  it('路由结果携带 reason', () => {
    const r = routeAuto({ mode: 'ask', longContext: false, codeRelated: false, research: false });
    expect(r.reason).toBeTruthy();
  });
});

describe('特征检测', () => {
  it('detectCodeRelated 命中代码关键词', () => {
    expect(detectCodeRelated('帮我做一个环保节日日历网页')).toBe(true);
    expect(detectCodeRelated('修复这个函数的 bug')).toBe(true);
    expect(detectCodeRelated('写一份周报')).toBe(false);
  });

  it('detectResearch 命中调研关键词', () => {
    expect(detectResearch('做一份竞品调研报告')).toBe(true);
    expect(detectResearch('收集资料写分析报告')).toBe(true);
    expect(detectResearch('整理合同金额')).toBe(false);
  });
});
