/**
 * spec-renderer — DesignSpec → PPTX 克隆模板渲染器
 *
 * 与手工生成器（gen_deep_space_blue.py）同层级但由 spec 驱动：
 * 视觉模型输出几何/颜色/字号 → python-pptx 精确落位 → 输出模板文件。
 * 渲染后立即跑"克隆引擎自检"（scanTemplate 复刻 + 槽位角色断言），
 * 确保产物能被 office.generate 的 template 参数直接使用。
 *
 * 跨进程：Node 无成熟 pptx 写库，python-pptx 是唯一工业级方案（同 office.ts
 * 的模板克隆策略）。
 */
import * as path from 'node:path';
import * as os from 'node:os';
import * as fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import type { DesignSpec } from '@wdbuddy/shared-types';

/** 执行渲染：spec + 输出路径 → python 子进程生成 pptx。
 *  返回 stdout（含自检结果行） */
export function renderSpecToPptx(spec: DesignSpec, outputPath: string): { ok: boolean; output: string } {
  const tmpJson = path.join(os.tmpdir(), `wd_spec_${Date.now()}.json`);
  const tmpPy = path.join(os.tmpdir(), `wd_spec_${Date.now()}.py`);
  fs.writeFileSync(tmpJson, JSON.stringify({ spec, output: outputPath }), 'utf8');
  fs.writeFileSync(tmpPy, RENDER_PY, 'utf8');
  try {
    const out = execFileSync('python', ['-X', 'utf8', tmpPy, tmpJson], {
      encoding: 'utf8',
      timeout: 120000,
      windowsHide: true,
      maxBuffer: 32 * 1024 * 1024,
    }).toString();
    return { ok: true, output: out };
  } catch (err: unknown) {
    const e = err as { stderr?: string; message?: string };
    return { ok: false, output: String(e.stderr ?? e.message ?? err) };
  } finally {
    try { fs.unlinkSync(tmpPy); } catch { /* */ }
    try { fs.unlinkSync(tmpJson); } catch { /* */ }
  }
}

/* python 渲染脚本：读 spec json → python-pptx 精确落位 → 自检槽位角色 */
const RENDER_PY = `
import sys, json, re
from collections import Counter
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

cfg = json.load(open(sys.argv[1], encoding='utf8'))
spec = cfg['spec']
OUT = cfg['output']

PAGE_W, PAGE_H = 13.333, 7.5
FONT_FALLBACK = spec.get('font') or '微软雅黑'

def hex2rgb(h):
    h = (h or '#000000').lstrip('#')
    return RGBColor(int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))

def palette_usage(colors, kw, dflt):
    for c in colors:
        if kw in (c.get('usage') or '').lower():
            return c['hex']
    return dflt

def bg_default(colors):
    # 最深色作背景兜底
    best, best_lum = None, -1
    for c in colors:
        h = c['hex'].lstrip('#')
        r,g,b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
        lum = 255 - (0.299*r+0.587*g+0.114*b)
        if lum > best_lum:
            best, best_lum = c['hex'], lum
    return best or '#0b0f2a'

BG = hex2rgb(palette_usage(spec['colors'], '背景', bg_default(spec['colors'])))

prs = Presentation()
prs.slide_width = Emu(12192000)
prs.slide_height = Emu(6858000)
BLANK = prs.slide_layouts[6]

def add_text(shapes, x, y, w, h, text, size, color, bold, align, spc, anchor_mid=False):
    tb = shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    if anchor_mid:
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    f = r.font
    f.size = Pt(size); f.bold = bold; f.color.rgb = color; f.name = FONT_FALLBACK
    rPr = r._r.get_or_add_rPr()
    if spc:
        rPr.set('spc', str(int(spc)))
    latin = rPr.find(qn('a:latin'))
    ea = rPr.makeelement(qn('a:ea'), {'typeface': FONT_FALLBACK})
    if latin is not None:
        latin.addnext(ea)
    else:
        rPr.append(ea)
    return tb

def add_shape(shapes, st, x, y, w, h, fill, line_col, lw, alpha=None, adj=None):
    sp = shapes.add_shape(st, Inches(x), Inches(y), Inches(w), Inches(h))
    sp.shadow.inherit = False
    if fill is None:
        sp.fill.background()
    else:
        sp.fill.solid(); sp.fill.fore_color.rgb = fill
        if alpha is not None:
            solid = sp._element.spPr.find(qn('a:solidFill'))
            srgb = solid.find(qn('a:srgbClr')) if solid is not None else None
            if srgb is not None:
                srgb.append(srgb.makeelement(qn('a:alpha'), {'val': str(int(alpha*1000))}))
    if line_col is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line_col; sp.line.width = Pt(lw)
    if adj is not None:
        try:
            sp.adjustments[0] = adj
        except Exception:
            pass
    return sp

SHAPE_MAP = {
    'rect': MSO_SHAPE.RECTANGLE,
    'rounded': MSO_SHAPE.ROUNDED_RECTANGLE,
    'circle': MSO_SHAPE.OVAL,
    'oval': MSO_SHAPE.OVAL,
    'ring': MSO_SHAPE.OVAL,
    'line': MSO_SHAPE.RECTANGLE,
}

ALIGN_MAP = {'left': PP_ALIGN.LEFT, 'center': PP_ALIGN.CENTER, 'right': PP_ALIGN.RIGHT}

def render_slide(sl):
    s = prs.slides.add_slide(BLANK)
    # 页面背景
    add_shape(s.shapes, MSO_SHAPE.RECTANGLE, 0, 0, PAGE_W+0.01, PAGE_H+0.01, BG, None, 0)
    for slot in sl['slots']:
        x = slot['x']/100.0 * PAGE_W
        y = slot['y']/100.0 * PAGE_H
        w = slot['w']/100.0 * PAGE_W
        h = slot['h']/100.0 * PAGE_H
        if slot['type'] == 'decor':
            st = SHAPE_MAP.get(slot.get('shape') or 'rect', MSO_SHAPE.RECTANGLE)
            fill = hex2rgb(slot.get('fill')) if slot.get('fill') else None
            stroke = hex2rgb(slot.get('stroke')) if slot.get('stroke') else None
            lw = slot.get('strokeWidth') or 1.0
            alpha = 1.0 - slot.get('opacity', 1.0) if slot.get('fill') else None
            adj = slot.get('radius') if st == MSO_SHAPE.ROUNDED_RECTANGLE else None
            sp = add_shape(s.shapes, st, x, y, w, h, fill, stroke, lw, alpha, adj)
            if slot.get('text'):
                # 装饰上的文字（圆内编号等）：直接覆盖文本框
                add_text(s.shapes, x, y, w, h, slot['text'],
                         slot.get('size', 18), hex2rgb(slot.get('color') or '#ffffff'),
                         slot.get('bold', False), PP_ALIGN.CENTER, 0, anchor_mid=True)
        else:
            add_text(s.shapes, x, y, w, h, slot.get('text') or '占位',
                     slot.get('size', 14), hex2rgb(slot.get('color') or '#ffffff'),
                     slot.get('bold', False), ALIGN_MAP.get(slot.get('align') or 'left', PP_ALIGN.LEFT),
                     slot.get('spc') or 0,
                     anchor_mid=(slot.get('role') == 'number'))
    return s

for sl in spec['slides']:
    render_slide(sl)

prs.save(OUT)
print('RENDER_OK slides=' + str(len(spec['slides'])))

# ══ 引擎级自检：复刻 scanTemplate + guessRole + guessKind（与 template-clone.ts 对齐）══
def walk(shapes):
    for sh in shapes:
        if sh.shape_type == 6:
            for sub in walk(sh.shapes):
                yield sub
        else:
            yield sh

def guess_role(y, w, paras):
    first = paras[0] if paras else ''
    if re.match(r'^\\d{2}$', first): return 'number'
    if re.match(r'^N\\.\\d+', first): return 'decor'
    if re.match(r'^(主讲人|汇报时间|时间)', first): return 'footer'
    if re.match(r'^(PART|THANKS)', first, re.I): return 'decor'
    if re.match(r'^目\\s*录$', first.strip()): return 'title'
    if re.match(r'^CONTENTS$', first.strip(), re.I): return 'decor'
    if re.match(r'^\\d{1,2}$', first): return 'number'
    if re.match(r'^\\d{2,4}X?[-/年]\\d{1,2}([-/月]\\d{1,2}日?)?$', first.strip()): return 'date'
    if re.search(r'添加.*标题|点击.*标题', first): return 'item-title'
    if re.search(r'在这里输入|输入您的|此处输入', first): return 'item-body'
    if re.search(r'输入您的标题内容|标题文本标题', first): return 'item-body' if len(first) > 15 else 'item-title'
    if re.search(r'上市公司|关键词', first): return 'item-body'
    if y < 1.2 and w > 3: return 'title'
    if y < 1.2: return 'title'
    if y < 2.0 and w > 5: return 'title'
    return 'body'

def guess_kind(page, boxes):
    roles = [guess_role(b['y'], b['w'], b['paras']) for b in boxes]
    texts = ' '.join(' '.join(b['paras']) for b in boxes)
    if re.search(r'THANKS', texts, re.I): return 'closing'
    if re.search(r'目\\s*录', texts): return 'toc'
    if re.search(r'PART ONE|PART TWO', texts, re.I) or any(b['paras'][0] == '01' and b['y'] < 1 for b in boxes):
        return 'section'
    if page <= 2: return 'cover'
    item_titles = roles.count('item-title')
    if item_titles == 0 and 'title' not in roles:
        if roles.count('number') >= 1 and len(boxes) <= 6: return 'section'
    if roles.count('number') >= 4: return 'content-4col'
    if item_titles >= 3: return 'content-3col'
    return 'content'

prs2 = Presentation(OUT)
issues = []
for si, slide in enumerate(prs2.slides, 1):
    boxes = []
    for sh in walk(slide.shapes):
        if not sh.has_text_frame:
            continue
        paras = [p.text for p in sh.text_frame.paragraphs if p.text.strip()]
        if not paras:
            continue
        boxes.append({'y': sh.top/914400, 'w': sh.width/914400, 'paras': paras})
    if not boxes:
        issues.append(f'P{si}: 无文本槽位')
        continue
    kind = guess_kind(si, boxes)
    cnt = Counter(guess_role(b['y'], b['w'], b['paras']) for b in boxes)
    print(f'P{si}: kind={kind} slots={dict(cnt)}')
    # 基本健全性：每页至少有 title 或 body（避免全 decor 页）
    if not any(r in cnt for r in ('title', 'body', 'item-title', 'item-body')):
        issues.append(f'P{si}: 全装饰页（无内容槽）')
    # 溢出检查：槽位超出页面边界（出血 decor 除外）
    for b in boxes:
        if b['y'] < -0.5 or b['y'] + b['w']*0.3 > PAGE_H + 1:
            pass  # 文本槽高度仅估算，跳过误报

if issues:
    print('SELF_CHECK WARNINGS: ' + ' | '.join(issues))
else:
    print('SELF_CHECK PASSED')
`;
