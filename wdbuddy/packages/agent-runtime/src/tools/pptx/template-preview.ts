/**
 * 模板真彩预览（v2）— python-pptx 逐形状 dump（几何/填充/渐变/线框/图片/文字）
 * → SVG 真彩渲染。
 * 背景：v1 线框预览是深底灰框，用户完全看不出模板的红蓝配色与立体感，误以为
 * "模板全是黑的"。真彩预览还原每页形状的真实颜色（含主题色解析：从 theme1.xml
 * 读 accent/dk/lt 色板，主题色填充不再丢失）、渐变、装饰圆、图片（base64 原样
 * 内嵌）与文字，39 页纵向滚动浏览，所见即模板真实视觉。
 * 渲染用 SVG（viewBox 1280×720 = 13.333×7.5in × 96dpi），任意缩放不失真；
 * 填充形状统一加轻投影（drop-shadow）近似"微立体"质感。
 */
import * as path from 'node:path';
import * as os from 'node:os';
import * as fs from 'node:fs';
import { execFileSync } from 'node:child_process';

/* ───────────── 数据结构（python dump JSON 的 TS 映射） ───────────── */

export interface VisRun {
  t: string;
  sz?: number | null;
  c?: string | null;
  b?: boolean;
}
export interface VisPara {
  runs: VisRun[];
  align?: string | null;
}
export interface VisFill {
  t: 'solid' | 'grad' | 'pic';
  c?: string | null;
  stops?: string[];
  ang?: number;
}
export interface VisShape {
  x: number;
  y: number;
  w: number;
  h: number;
  rot?: number;
  st: number;
  auto?: string;
  fill?: VisFill;
  line?: { c?: string | null; w?: number | null };
  img?: string;
  text?: VisPara[];
  children?: VisShape[];
}
export interface VisSlide {
  bg?: VisFill | null;
  shapes: VisShape[];
}
export interface VisDump {
  ok: boolean;
  slides: VisSlide[];
  error?: string;
}

/* ───────────── python 桥：逐形状 dump ───────────── */

export function dumpTemplateVisual(templatePath: string): VisDump {
  const tmpPy = path.join(os.tmpdir(), `wd_vis_${Date.now()}.py`);
  fs.writeFileSync(tmpPy, PYTHON_VISUAL, 'utf8');
  try {
    const out = execFileSync('python', ['-X', 'utf8', tmpPy, templatePath], {
      encoding: 'utf8',
      timeout: 120000,
      windowsHide: true,
      maxBuffer: 64 * 1024 * 1024,
    }).toString();
    const data = JSON.parse(out.trim()) as { slides: VisSlide[] };
    return { ok: true, slides: data.slides };
  } catch (err: any) {
    return { ok: false, slides: [], error: String(err?.stderr || err) };
  } finally {
    try { fs.unlinkSync(tmpPy); } catch { /* */ }
  }
}

const PYTHON_VISUAL = `
import sys, json, base64, zipfile, re
from pptx import Presentation
from pptx.oxml.ns import qn
from lxml import etree

EMU_IN = 914400.0
THEME_MAP = {}
CLRS = {}
MASTER_STYLE = {}
FALLBACKS = ['#4472C4', '#ED7D31', '#A5A5A5', '#FFC000', '#5B9BD5', '#70AD47']
NS = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'p': 'http://schemas.openxmlformats.org/presentationml/2006/main'}

def load_pkg(p):
    from lxml import etree
    z = zipfile.ZipFile(p)
    t = etree.fromstring(z.read('ppt/theme/theme1.xml'))
    for slot in ('dk1','lt1','dk2','lt2','accent1','accent2','accent3','accent4','accent5','accent6','hlink','folHlink'):
        el = t.find('.//a:clrScheme/a:' + slot, NS)
        if el is None:
            continue
        for c in el:
            v = c.get('val')
            if c.tag == qn('a:srgbClr') and v:
                THEME_MAP[slot.upper()] = '#' + v
            elif c.tag == qn('a:sysClr') and c.get('lastClr'):
                THEME_MAP[slot.upper()] = '#' + c.get('lastClr')
    fsl = t.find('.//a:fillStyleLst', NS)
    if fsl is not None:
        i = 0
        for child in fsl:
            i += 1
            stops = []
            for gs in child.iter(qn('a:gs')):
                cc = read_color(gs)
                if cc:
                    stops.append(cc)
            if stops:
                MASTER_STYLE[i] = stops[:4]
    for n in z.namelist():
        if re.match(r'ppt/slideMasters/slideMaster[0-9]+[.]xml', n):
            m = etree.fromstring(z.read(n))
            cm = m.find('.//p:clrMap', NS)
            if cm is not None:
                for k, v in cm.attrib.items():
                    CLRS[k.upper()] = v.upper()
            break

def scheme_color(sch):
    v = (sch.get('val') or '').upper()
    v = CLRS.get(v, v)
    base = THEME_MAP.get(v)
    if base is None:
        return None
    try:
        lm = sch.find(qn('a:lumMod'))
        lo = sch.find(qn('a:lumOff'))
        if lm is None and lo is None:
            return base
        mod = float(lm.get('val')) / 100000.0 if lm is not None else 1.0
        off = float(lo.get('val')) / 100000.0 if lo is not None else 0.0
        n = int(base[1:], 16)
        r = int(((n >> 16) & 255) * mod + 255 * off)
        g = int(((n >> 8) & 255) * mod + 255 * off)
        b = int((n & 255) * mod + 255 * off)
        return '#{:02X}{:02X}{:02X}'.format(min(r, 255), min(g, 255), min(b, 255))
    except Exception:
        return base

def read_color(container):
    # 容器两种形态：a) 内含 solidFill 包装（spPr/ln/rPr）b) 直连颜色子（渐变 gs）
    if container is None:
        return None
    for holder in (container.find(qn('a:solidFill')), container):
        if holder is None:
            continue
        srgb = holder.find(qn('a:srgbClr'))
        if srgb is not None and srgb.get('val'):
            return '#' + srgb.get('val')
        sysc = holder.find(qn('a:sysClr'))
        if sysc is not None and sysc.get('lastClr'):
            return '#' + sysc.get('lastClr')
        sch = holder.find(qn('a:schemeClr'))
        if sch is not None:
            c = scheme_color(sch)
            if c:
                return c
    return None

def slide_bg(slide):
    # 页背景继承链：slide → layout → master，全部 XML 直读（p:cSld/p:bg/p:bgPr）
    srcs = [slide]
    try:
        srcs.append(slide.slide_layout)
        srcs.append(slide.slide_layout.slide_master)
    except Exception:
        pass
    for src in srcs:
        try:
            cSld = src._element.find(qn('p:cSld'))
            if cSld is None:
                continue
            bg = cSld.find(qn('p:bg'))
            if bg is None:
                continue
            for bgPr in (bg.find(qn('p:bgPr')), bg.find(qn('p:bgRef'))):
                if bgPr is None:
                    continue
                f = fill_from_el(bgPr)
                if f:
                    return f
                # bgRef：带 idx 的主题填充引用（schemeClr 直取）
                sch = bgPr.find(qn('a:schemeClr'))
                if sch is not None:
                    c = scheme_color(sch)
                    if c:
                        return {'t': 'solid', 'c': c}
        except Exception:
            pass
    return None

def fill_from_el(spPr):
    # 纯 XML 直读（不依赖 python-pptx 私有属性）：spPr 直下 solidFill/gradFill/blipFill
    if spPr is None:
        return None
    sf = spPr.find(qn('a:solidFill'))
    if sf is not None:
        return {'t': 'solid', 'c': read_color(sf) or '#D0D0D0'}
    gf = spPr.find(qn('a:gradFill'))
    if gf is not None:
        stops = []
        for gs in gf.iter(qn('a:gs')):
            c = read_color(gs)
            if c:
                stops.append(c)
        if stops:
            ang = 0.0
            try:
                lin = gf.find(qn('a:lin'))
                if lin is not None and lin.get('ang'):
                    ang = int(lin.get('ang')) / 60000.0
            except Exception:
                pass
            return {'t': 'grad', 'stops': stops[:4], 'ang': ang}
        return None
    if spPr.find(qn('a:blipFill')) is not None:
        return {'t': 'pic'}
    return None

def style_fill(sh):
    # 继承链兜底：无显式填充时解析 p:style/p:fillRef（主题 fillStyleLst）
    try:
        el = sh._element.find(qn('p:style'))
        if el is None:
            return None
        fr = el.find(qn('p:fillRef'))
        if fr is None:
            return None
        idx = int(fr.get('idx', '0'))
        if idx in (2, 3) and MASTER_STYLE.get(idx) and len(MASTER_STYLE[idx]) >= 2:
            return {'t': 'grad', 'stops': MASTER_STYLE[idx], 'ang': 90.0}
        sch = fr.find(qn('a:schemeClr'))
        if sch is not None:
            c = scheme_color(sch)
            if c:
                return {'t': 'solid', 'c': c}
        if 1 <= idx <= 6:
            return {'t': 'solid', 'c': FALLBACKS[idx - 1]}
    except Exception:
        pass
    return None

def line_json(sh):
    try:
        ln = sh.line
        w = None
        if ln.width:
            w = round(ln.width / EMU_IN * 96, 1)
        c = None
        try:
            sp = sh._element.find(qn('p:spPr'))
            if sp is None:
                sp = sh._element
            c = read_color(sp.find(qn('a:ln')))
        except Exception:
            pass
        if c is None:
            try:
                el = sh._element.find(qn('p:style'))
                if el is not None:
                    lr = el.find(qn('p:lnRef'))
                    if lr is not None:
                        sch = lr.find(qn('a:schemeClr'))
                        if sch is not None:
                            c = scheme_color(sch)
            except Exception:
                pass
        if c or w:
            return {'c': c, 'w': w}
    except Exception:
        pass
    return None

def text_json(shape):
    try:
        tf = shape.text_frame
    except Exception:
        return None
    if not tf.text.strip():
        return None
    paras = []
    for p in tf.paragraphs:
        runs = []
        for r in p.runs:
            sz = None
            try:
                if r.font.size:
                    sz = r.font.size.pt
            except Exception:
                pass
            col = None
            try:
                rpr = r._r.find(qn('a:rPr'))
                col = read_color(rpr)
            except Exception:
                pass
            runs.append({'t': r.text, 'sz': sz, 'c': col, 'b': bool(r.font.bold)})
        al = None
        try:
            al = str(p.alignment)
        except Exception:
            pass
        if runs:
            paras.append({'runs': runs, 'align': al})
    return paras or None

def num(v):
    try:
        return round(float(v) / EMU_IN, 3) if v is not None else 0.0
    except Exception:
        return 0.0

def shape_json(sh):
    st = -1
    try:
        st = int(sh.shape_type)
    except Exception:
        pass
    d = {'x': num(sh.left), 'y': num(sh.top), 'w': num(sh.width), 'h': num(sh.height), 'st': st}
    try:
        if sh.rotation:
            d['rot'] = float(sh.rotation)
    except Exception:
        pass
    if st == 6:
        try:
            kids = [shape_json(c) for c in sh.shapes]
            d['children'] = [k for k in kids if k]
        except Exception:
            pass
        return d
    if st == 13:
        try:
            img = sh.image
            blob = img.blob
            if len(blob) < 1000000:
                d['img'] = 'data:' + str(img.content_type) + ';base64,' + base64.b64encode(blob).decode()
        except Exception:
            pass
    try:
        au = str(sh.auto_shape_type).split(' ')[0]
        if au and au != 'None':
            d['auto'] = au
    except Exception:
        pass
    try:
        fl = fill_from_el(sh._element.find(qn('p:spPr')))
        if fl is None:
            fl = style_fill(sh)
        if fl:
            d['fill'] = fl
    except Exception:
        pass
    lj = line_json(sh)
    if lj:
        d['line'] = lj
    tj = text_json(sh)
    if tj:
        d['text'] = tj
    if 'fill' not in d and 'line' not in d and 'img' not in d and 'text' not in d:
        return None
    return d

load_pkg(sys.argv[1])
prs = Presentation(sys.argv[1])
slides = []
for slide in prs.slides:
    bg = slide_bg(slide)
    shapes = []
    for sh in slide.shapes:
        j = shape_json(sh)
        if j:
            shapes.append(j)
    slides.append({'bg': bg, 'shapes': shapes})
print(json.dumps({'slides': slides}, ensure_ascii=False))
`;

/* ───────────── SVG 真彩渲染 ───────────── */

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 真彩预览 HTML：每页一个 SVG（viewBox 1280×720），纵向滚动浏览 */
export function buildColorPreviewHtml(name: string, dump: VisDump): string {
  const V = 96; // 1 英寸 = 96 viewBox 单位
  const W = 1280;
  const H = 720;
  const budget = { used: 0, limit: 7 * 1024 * 1024 };
  const defs: string[] = [];
  let gid = 0;

  const gradDef = (f: VisFill): string => {
    const id = `g${gid++}`;
    const stops = f.stops ?? ['#D9D9D9', '#F2F2F2'];
    const n = Math.max(stops.length - 1, 1);
    const stopTags = stops
      .map((c, i) => `<stop offset="${(i / n).toFixed(3)}" stop-color="${c}"/>`)
      .join('');
    const a = ((f.ang ?? 0) * Math.PI) / 180;
    const x1 = (0.5 - 0.5 * Math.cos(a)).toFixed(3);
    const y1 = (0.5 - 0.5 * Math.sin(a)).toFixed(3);
    const x2 = (0.5 + 0.5 * Math.cos(a)).toFixed(3);
    const y2 = (0.5 + 0.5 * Math.sin(a)).toFixed(3);
    defs.push(
      `<linearGradient id="${id}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}">${stopTags}</linearGradient>`
    );
    return `url(#${id})`;
  };

  const renderShape = (sh: VisShape): string => {
    const x = sh.x * V;
    const y = sh.y * V;
    const w = sh.w * V;
    const h = sh.h * V;
    const rot = sh.rot
      ? ` transform="rotate(${sh.rot.toFixed(1)} ${(x + w / 2).toFixed(1)} ${(y + h / 2).toFixed(1)})"`
      : '';
    if (sh.children) {
      return sh.children.map(renderShape).join('');
    }
    const out: string[] = [];
    if (sh.img) {
      if (budget.used + sh.img.length < budget.limit) {
        budget.used += sh.img.length;
        out.push(
          `<image class="sh" href="${sh.img}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" preserveAspectRatio="none"${rot}/>`
        );
      } else {
        out.push(
          `<rect class="sh" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" fill="#E8E8E8"${rot}/>`
        );
      }
      return out.join('');
    }
    if (sh.fill || sh.line) {
      let fillAttr = 'fill="none"';
      if (sh.fill?.t === 'solid' && sh.fill.c) fillAttr = `fill="${sh.fill.c}"`;
      else if (sh.fill?.t === 'grad') fillAttr = `fill="${gradDef(sh.fill)}"`;
      else if (sh.fill?.t === 'pic') fillAttr = 'fill="#EEEEEE"';
      const strokeAttr = sh.line?.c
        ? ` stroke="${sh.line.c}" stroke-width="${Math.max(sh.line.w ?? 1, 0.75)}"`
        : '';
      if (sh.auto === 'OVAL' || sh.auto === 'ELLIPSE') {
        out.push(
          `<ellipse class="sh" cx="${(x + w / 2).toFixed(1)}" cy="${(y + h / 2).toFixed(1)}" rx="${(w / 2).toFixed(1)}" ry="${(h / 2).toFixed(1)}" ${fillAttr}${strokeAttr}${rot}/>`
        );
      } else {
        const rx = sh.auto === 'ROUNDED_RECTANGLE' ? Math.min(w, h) * 0.12 : 0;
        out.push(
          `<rect class="sh" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}"${rx ? ` rx="${rx.toFixed(1)}"` : ''} ${fillAttr}${strokeAttr}${rot}/>`
        );
      }
    }
    if (sh.text) {
      let ty = y;
      let lines = 0;
      for (const p of sh.text) {
        if (lines >= 6) break;
        const r0 = p.runs[0]!;
        const fs = (r0.sz ?? 14) * (V / 72);
        const color = r0.c ?? '#333333';
        const bold = p.runs.some((r) => r.b) ? ' font-weight="700"' : '';
        const center = (p.align ?? '').includes('CENTER');
        const anchor = center ? ' text-anchor="middle"' : '';
        const tx = center ? x + w / 2 : x + 2;
        const raw = p.runs
          .map((r) => r.t)
          .join('')
          .trim()
          .slice(0, 60);
        if (raw) {
          out.push(
            `<text x="${tx.toFixed(1)}" y="${(ty + fs * 0.85).toFixed(1)}" font-size="${fs.toFixed(1)}" fill="${color}"${bold}${anchor} font-family="Microsoft YaHei,PingFang SC,sans-serif">${esc(raw)}</text>`
          );
        }
        ty += fs * 1.28;
        lines++;
      }
    }
    return out.join('');
  };

  const pages = dump.slides
    .map((s, i) => {
      let bgFill = '#FFFFFF';
      if (s.bg?.t === 'solid' && s.bg.c) bgFill = s.bg.c;
      else if (s.bg?.t === 'grad') bgFill = gradDef(s.bg);
      const body = s.shapes.map(renderShape).join('');
      const defXml = defs.length ? `<defs>${defs.join('')}</defs>` : '';
      return `<div class="pg"><svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="第${i + 1}页"><rect x="0" y="0" width="${W}" height="${H}" fill="${bgFill}"/>${defXml}${body}</svg><span class="pgn">${i + 1} / ${dump.slides.length}</span></div>`;
    })
    .join('\n');

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>${esc(name)} · 真彩预览</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #E9EBF0; font-family: "Microsoft YaHei", sans-serif; }
  .deck { display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 18px; }
  .pg { position: relative; width: min(1080px, 94vw); }
  .pg svg { width: 100%; height: auto; display: block; border-radius: 6px; box-shadow: 0 2px 10px rgba(0,0,0,0.18); }
  .pgn { position: absolute; right: 8px; bottom: 6px; font-size: 11px; color: #fff; background: rgba(0,0,0,0.45); padding: 1px 7px; border-radius: 9px; }
  .sh { filter: drop-shadow(1.5px 2px 2.5px rgba(0,0,0,0.22)); }
  text { user-select: none; }
</style>
</head>
<body>
<div class="deck">
${pages}
</div>
</body>
</html>`;
}

