/**
 * design-spec — 图片 → DesignSpec 提取（template.from_image 视觉链路核心）
 *
 * 职责：
 * 1. buildSpecPrompt：给视觉模型的指令（要求输出结构化 JSON：
 *    全局色板 + 逐页版式 + 槽位几何/字号/颜色/对齐——这是还原度的关键，
 *    提示词必须把"槽位坐标系约定"和"克隆引擎槽位角色"讲清楚）
 * 2. parseSpec：模型输出 → 严格校验 + 归一化（颜色补 #、几何夹取 0-100、
 *    kind 中英映射），失败给精确错误（供上层重试）
 */
import type { DesignSpec, SpecSlide } from '@wdbuddy/shared-types';
import { extractJson } from '@wdbuddy/model-gateway';

/** 视觉提取提示词：坐标系 + 角色 + 输出格式三约定 */
export function buildSpecPrompt(opts: { maxPages?: number } = {}): string {
  const maxPages = opts.maxPages ?? 9;
  return `你是资深 PPT 设计师。分析这张幻灯片设计参考图，输出一个 JSON 设计规格（DesignSpec），用于程序化重建为可编辑 PPT 模板。

【重要：先判断图的类型】
- 若图片是单页幻灯片设计（常见），输出 slides 数组含 1 页，并按单页版式详细描述所有槽位。
- 若图片是多页缩略图拼贴（多张幻灯片拼在一张图里），请识别出每一页的边界，为每页输出一个 slide 描述。
- 若图片含大量连续照片或非幻灯片内容，只提取可用的设计语言（色板/质感），slides 输出空数组并在 vibe 中说明。

【全局部分】
- name：模板名（中文，6-14 字，形如"深空蓝调·科技数据汇报"）
- vibe：一句话风格描述（30 字内）
- colors：主色板 4-8 个（hex 带 #；标注 usage：主背景/卡片底/强调一/强调二/正文文字/副文字/线条）
- font：建议中文字体（不确定就写"微软雅黑"）

【每页 slides 部分】
kind（版式角色，二选一风格）：
  封面=cover 目录=toc 章节页=section 内容页=content 结尾页=closing
  内容页细分：三卡=content-3col 四卡=content-4col 时间线=timeline 两栏=two-col 列表=list
title：该页主标题占位文字（若无标题槽可省略）

slots：页面内所有可见元素（文本框与图形装饰，坐标为 0-100 归一化百分比，x/y=左上角，w/h=宽高占页面比例）：
文本框（type:"text"）：
  text：占位文字（示例化写法：主标题写"在此输入主标题文字内容"；条目卡标题写"添加标题一"；说明写"在这里输入说明文字占位内容"；编号写"01"）
  role（与克隆引擎对齐，必须正确标注）：title=主标题 | item-title=条目卡标题 | item-body=条目说明 | number=编号数字 | footer=页脚落款 | decor=纯装饰文字（如 CONTENTS/PART ONE/THANKS） | body=普通正文
  size：字号 pt（参考：封面主标题 36-44、页面标题 26、条目卡标题 16-18、说明文字 11-12、编号 18-24、装饰英文 12-15、大数字装饰 90-150）
  color：hex；bold：是否加粗；align：left/center/right（文本框宽 >60% 页宽且居中 → center）
  spc：字距（英文装饰字推荐 200-400，即 2-4pt）
图形装饰（type:"decor"）：
  shape：rect=矩形 | rounded=圆角矩形 | circle=圆 | ring=圆环(空心) | line=细线
  fill：填充色 hex（line/ring 用 stroke+strokeWidth）；opacity：不透明度 0-1（叠底装饰常用 0.1-0.3）
  radius：圆角系数 0-1（rounded 时，相对短边比例，普通卡片 0.05-0.16）
  text：形状上若有文字则填（如圆环内编号"01"），此时可带 size/color/bold/align

【几何规范（重要）】
- x/y/w/h 全部 0-100；w 是宽度占整页百分比
- 页面纵横比 16:9（13.33 x 7.5 英寸），换算示例：页宽 13.33in 时 x=6.75% ≈ 0.9in
- 元素之间留 0.5-1% 间隙避免重叠；文本框高度按字号 x 1.4 估
- 装饰圆环/圆可部分出页面边界（出血设计，x/y 可为负或 >100-w）

【输出约定】
- 只输出 JSON，不要任何解释文字（可放 \`\`\`json 围栏）
- 最多 ${maxPages} 页；图片中相似版式去重（保留差异化的）
- 色板 hex 必须从图片真实采样，禁止臆造
- 所有文本占位一律用中文示例（除英文装饰词）`;
}

/** kind 中英归一化（视觉模型可能输出中文或英文） */
function normalizeKind(raw: string): string {
  const k = raw.trim().toLowerCase();
  const map: Record<string, string> = {
    'cover': 'cover', '封面': 'cover',
    'toc': 'toc', '目录': 'toc',
    'section': 'section', '章节': 'section', '章节页': 'section',
    'closing': 'closing', '结尾': 'closing', '结尾页': 'closing', '封底': 'closing',
    'content-3col': 'content-3col', '三卡': 'content-3col', '三列卡': 'content-3col', '卡片x3': 'content-3col',
    'content-4col': 'content-4col', '四卡': 'content-4col', '四列卡': 'content-4col', '卡片x4': 'content-4col',
    'timeline': 'timeline', '时间线': 'timeline', '流程': 'timeline',
    'two-col': 'two-col', '两栏': 'two-col', '双栏': 'two-col',
    'list': 'list', '列表': 'list',
    'content': 'content', '内容': 'content', '内容页': 'content',
  };
  return map[k] ?? 'content';
}

function normHex(raw: unknown): string | undefined {
  if (typeof raw !== 'string') return undefined;
  let s = raw.trim().replace(/^#/, '');
  if (/^[0-9a-fA-F]{3}$/.test(s)) {
    s = s.split('').map((c) => c + c).join('');
  }
  if (!/^[0-9a-fA-F]{6}$/.test(s)) return undefined;
  return `#${s.toLowerCase()}`;
}

function clamp(n: unknown, lo: number, hi: number, dflt: number): number {
  const v = typeof n === 'number' && Number.isFinite(n) ? n : dflt;
  return Math.min(hi, Math.max(lo, v));
}

function normAlign(raw: unknown): 'left' | 'center' | 'right' {
  const s = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
  if (s === 'center' || s === 'ctr' || s === 'middle') return 'center';
  if (s === 'right') return 'right';
  return 'left';
}

/** 模型输出 → 校验归一化 DesignSpec。失败抛错（含原因，供重试反馈） */
export function parseSpec(rawJson: unknown): DesignSpec {
  if (!rawJson || typeof rawJson !== 'object') {
    throw new Error('DesignSpec 非对象');
  }
  const obj = rawJson as Record<string, unknown>;
  const name = typeof obj['name'] === 'string' && obj['name'].trim() ? obj['name'].trim().slice(0, 30) : '';
  if (!name) throw new Error('缺少模板名 name');

  const colors: DesignSpec['colors'] = [];
  for (const c of Array.isArray(obj['colors']) ? obj['colors'] : []) {
    if (!c || typeof c !== 'object') continue;
    const co = c as Record<string, unknown>;
    const hex = normHex(co['hex'] ?? co['color']);
    if (!hex) continue;
    colors.push({ hex, usage: typeof co['usage'] === 'string' ? co['usage'].slice(0, 20) : undefined });
  }
  if (colors.length < 2) throw new Error('色板颜色少于 2 个');

  const rawSlides = Array.isArray(obj['slides']) ? obj['slides'] : [];
  const slides: SpecSlide[] = [];
  for (const sl of rawSlides.slice(0, 12)) {
    if (!sl || typeof sl !== 'object') continue;
    const so = sl as Record<string, unknown>;
    const kind = normalizeKind(typeof so['kind'] === 'string' ? so['kind'] : 'content');
    const rawSlots = Array.isArray(so['slots']) ? so['slots'] : [];
    if (rawSlots.length === 0) continue;
    const slots: SpecSlide['slots'] = [];
    for (const slt of rawSlots.slice(0, 40)) {
      if (!slt || typeof slt !== 'object') continue;
      const so2 = slt as Record<string, unknown>;
      const type = so2['type'] === 'decor' ? 'decor' : 'text';
      const text = typeof so2['text'] === 'string' ? so2['text'].slice(0, 80) : undefined;
      const role = typeof so2['role'] === 'string' ? so2['role'] : undefined;
      const hexColor = normHex(so2['color']);
      const fill = normHex(so2['fill']);
      const stroke = normHex(so2['stroke']);
      slots.push({
        type,
        text: text || (type === 'text' ? '占位' : undefined),
        role: type === 'text' ? role : undefined,
        x: clamp(so2['x'], -30, 100, 0),
        y: clamp(so2['y'], -30, 100, 0),
        w: clamp(so2['w'], 0.5, 100, 10),
        h: clamp(so2['h'], 0.3, 100, 5),
        size: clamp(so2['size'], 8, 200, 14),
        color: hexColor,
        bold: so2['bold'] === true,
        align: normAlign(so2['align']),
        shape: typeof so2['shape'] === 'string' ? so2['shape'].toLowerCase() : undefined,
        fill,
        stroke,
        strokeWidth: clamp(so2['strokeWidth'], 0.25, 8, 1),
        opacity: clamp(so2['opacity'], 0.05, 1, 1),
        radius: clamp(so2['radius'], 0, 0.5, 0.08),
        spc: clamp(so2['spc'], 0, 1000, 0),
      });
    }
    if (slots.length === 0) continue;
    slides.push({
      kind,
      templateName: name,
      title: typeof so['title'] === 'string' ? so['title'].slice(0, 40) : undefined,
      slots,
    });
  }
  // slides 允许为空（非幻灯片图片仅提取色板）——但名字与色板必须有效
  return {
    name,
    vibe: typeof obj['vibe'] === 'string' ? obj['vibe'].slice(0, 60) : undefined,
    colors,
    font: typeof obj['font'] === 'string' && obj['font'].trim() ? obj['font'].slice(0, 20) : '微软雅黑',
    slides,
  };
}

/** 剥离 markdown 围栏/杂文 → 提取最外层 {...} 文本片段（不 JSON.parse） */
function extractJsonRaw(text: string): string | null {
  const fence = /```(?:json)?\s*([\s\S]*?)```/.exec(text);
  const raw = fence ? fence[1]! : text;
  // 优先截取首个 { 到最后一个 }（跨围栏兜底）
  const start = raw.indexOf('{');
  const end = raw.lastIndexOf('}');
  if (start >= 0 && end > start) return raw.slice(start, end + 1);
  return null;
}

/** 宽松修复视觉模型常见 JSON 畸形（修复后再 parse，失败返回 null）
 *  1. 尾逗号：[1,2,] / {"a":1,} → 去除
 *  2. 中文引号：键值两侧 “ ” ‘ ’ → 英文引号
 *  3. 单引号键/值 → 双引号（仅在键名与值边界，避免破坏内文单引号）
 *  4. 键名裸写：{name: "..."} → {"name": "..."}
 *  5. 控制字符/零宽字符剔除（\u0000-\u001F 除外 \n 换行语义由 parse 处理）
 *  6. 未闭合：结尾缺 } 或 ] 时按嵌套深度补齐（截断兜底）
 */
export function looseParseJson(text: string): unknown | null {
  let raw = extractJsonRaw(text);
  if (!raw) return null;
  // 5. 控制字符与零宽字符剔除
  raw = raw.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u200B-\u200D\uFEFF]/g, '');
  // 2. 中文引号 → 英文
  raw = raw.replace(/[“”«»]/g, '"').replace(/[‘’]/g, "'");
  // 1. 尾逗号
  raw = raw.replace(/,\s*([\]}])/g, '$1');
  // 4. 键名裸写：{ name: "x" } 或 [换行] name: → "name":
  raw = raw.replace(/([{,]\s*)([A-Za-z_][\w-]*)\s*:/g, '$1"$2":');
  // 3. 单引号字符串 → 双引号（保守：仅处理 'xxx' 整段，内部无单引号才转）
  raw = raw.replace(/'([^'\\]*)'/g, '"$1"');
  try {
    return JSON.parse(raw);
  } catch {
    /* 继续修复 */
  }
  // 6. 未闭合补齐：统计嵌套深度（字符串外），补齐缺失的 ] }
  let depth2 = 0; // {
  let depthArr = 0; // [
  let inStr = false;
  let esc = false;
  for (const ch of raw) {
    if (esc) { esc = false; continue; }
    if (ch === '\\') { if (inStr) esc = true; continue; }
    if (ch === '"') { inStr = !inStr; continue; }
    if (inStr) continue;
    if (ch === '{') depth2++;
    else if (ch === '}') depth2 = Math.max(0, depth2 - 1);
    else if (ch === '[') depthArr++;
    else if (ch === ']') depthArr = Math.max(0, depthArr - 1);
  }
  let fixed = raw;
  if (inStr) fixed += '"'; // 字符串截断：先闭合引号
  fixed += ']'.repeat(depthArr) + '}'.repeat(depth2);
  if (fixed !== raw) {
    try {
      return JSON.parse(fixed);
    } catch {
      /* 兜底失败 */
    }
  }
  return null;
}

/** 文本 →（宽松解析 + parseSpec）。视觉模型输出容错入口：
 *  先严格提取，失败则宽松修复（尾逗号/引号/截断），再失败抛出带原始片段的错误 */
export function parseSpecFromText(text: string): DesignSpec {
  let json: unknown;
  try {
    json = extractJson(text);
  } catch (err) {
    json = looseParseJson(text);
    if (json === null) {
      const preview = text.slice(0, 500).replace(/\s+/g, ' ');
      throw new Error(
        `JSON 解析失败（严格与宽松修复均不可用）：${err instanceof Error ? err.message : String(err)}。原始输出前 500 字符：${preview}`
      );
    }
  }
  return parseSpec(json);
}
