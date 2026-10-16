import { describe, expect, it } from 'vitest';
import { parseSpecFromText, looseParseJson } from './design-spec.js';

const VALID_SPEC = {
  name: '测试模板',
  vibe: '深蓝科技风',
  colors: [
    { hex: '#050836', usage: '主背景' },
    { hex: '#d4af37', usage: '强调' },
  ],
  font: '微软雅黑',
  slides: [
    {
      kind: '封面',
      title: '在此输入主标题文字内容',
      slots: [
        { type: 'text', role: 'title', text: '在此输入主标题文字内容', x: 0, y: 35, w: 100, h: 11, size: 40, color: '#ffffff', bold: true, align: 'center' },
        { type: 'decor', shape: 'rect', x: 0, y: 95, w: 100, h: 5, fill: '#d4af37', opacity: 0.2 },
      ],
    },
  ],
};

describe('looseParseJson — 视觉模型畸形输出修复', () => {
  it('正常 JSON 原样解析', () => {
    expect(looseParseJson(JSON.stringify(VALID_SPEC))).toEqual(VALID_SPEC);
  });

  it('markdown 围栏 + 前后杂文', () => {
    const text = `好的，以下是分析结果：\n\`\`\`json\n${JSON.stringify(VALID_SPEC)}\n\`\`\`\n希望对你有帮助`;
    expect(looseParseJson(text)).toEqual(VALID_SPEC);
  });

  it('尾逗号（对象+数组）', () => {
    const raw = JSON.stringify(VALID_SPEC)
      .replace(/"微软雅黑"/, '"微软雅黑",')
      .replace(/"封面"/, '"封面",');
    // 构造出的尾逗号在 stringify 后位于对象尾部前——直接手写更直观
    const hand = '{"name":"测试","colors":[{"hex":"#111111",},{"hex":"#222222",},],}';
    expect((looseParseJson(hand) as { name: string }).name).toBe('测试');
    void raw;
  });

  it('中文引号', () => {
    const hand = '{“name”:“测试模板”,“colors”:[{“hex”:“#111111”}]}';
    const r = looseParseJson(hand) as { name: string };
    expect(r.name).toBe('测试模板');
  });

  it('键名裸写 + 单引号值', () => {
    const hand = "{name:'测试模板', colors:[{hex:'#111111'}]}";
    const r = looseParseJson(hand) as { name: string };
    expect(r.name).toBe('测试模板');
  });

  it('截断未闭合（数组中途截断）补齐后可解析', () => {
    const hand = '{"name":"测试模板","colors":[{"hex":"#111111"},{"hex":"#222222"';
    const r = looseParseJson(hand) as { colors: unknown[] };
    expect(r.colors).toHaveLength(2);
  });

  it('position 634 类畸形（值内未转义双引号场景之外的典型样本）', () => {
    // 模拟真实事故：模型输出带注释与尾逗号混合
    const hand = `{
      "name": "深蓝模板", // 模板名
      "vibe": "科技风",
      "colors": [
        { "hex": "#050836", "usage": "背景", },
        { "hex": "#d4af37", "usage": "强调", },
      ],
      "slides": [
        { "kind": "cover", "slots": [ { "type": "text", "text": "标题", }, ], },
      ],
    }`;
    const r = looseParseJson(hand) as { name: string; slides: unknown[] };
    expect(r.name).toBe('深蓝模板');
    expect(r.slides).toHaveLength(1);
  });

  it('完全非 JSON 文本返回 null（不抛错）', () => {
    expect(looseParseJson('这是一段纯文字说明，没有任何结构化内容。')).toBeNull();
  });
});

describe('parseSpecFromText — 校验归一化', () => {
  it('合法 spec 通过并归一化（kind 中文→英文、hex 归一）', () => {
    const spec = parseSpecFromText(JSON.stringify(VALID_SPEC));
    expect(spec.name).toBe('测试模板');
    expect(spec.slides[0]!.kind).toBe('cover');
    expect(spec.colors[0]!.hex).toBe('#050836');
  });

  it('畸形输出（围栏+尾逗号）也能修复解析', () => {
    const text = '分析如下\n```json\n{"name":"蓝调","colors":[{"hex":"#123456",},{"hex":"#abcdef",}],"slides":[{"kind":"toc","slots":[{"type":"text","text":"目录",}]}]}\n```';
    const spec = parseSpecFromText(text);
    expect(spec.name).toBe('蓝调');
    expect(spec.slides[0]!.kind).toBe('toc');
  });

  it('非 JSON 输出抛出含原始预览的错误（供 Agent 排查）', () => {
    expect(() => parseSpecFromText('模型认为这张图不适合做模板，因为……')).toThrow(/原始输出前 500 字符/);
  });

  it('slides 为空不报错（由工具层判定非幻灯片图）', () => {
    const spec = parseSpecFromText('{"name":"色板","colors":[{"hex":"#111111"},{"hex":"#222222"}],"slides":[]}');
    expect(spec.slides).toHaveLength(0);
  });
});
