/**
 * 原子化修复：executor.ts 系统提示词（office.generate 用法）被反复还原，
 * 本脚本单进程内完成：源码注入 → 立即构建 → dist 验证 → 不符则直接补 dist
 */
import fs from 'node:fs';
import { execSync } from 'node:child_process';

const root = 'D:/AI/MyClaude/wdbuddy';
const src = root + '/packages/agent-runtime/src/core/executor.ts';
const dist = root + '/packages/agent-runtime/dist/core/executor.js';

let s = fs.readFileSync(src, 'utf8');
const MARK = '【必须】使用 office.generate';

if (!s.includes(MARK)) {
  console.log('[1] 提示词缺失，注入中…');
  // 锚点：输出约定段的第一行
  const anchor = '输出约定：';
  const idx = s.indexOf(anchor);
  if (idx < 0) throw new Error('anchor 输出约定 not found');
  const insertion = `输出约定：
- 生成 PPT/演示文稿【必须】使用 office.generate（纯 JS 秒级完成，禁止用 Python/shell 方案）：
  {"tool": "office.generate", "args": {"path": "D:/xxx.pptx", "title": "总标题", "slides": [
    {"layout": "cover", "title": "封面主标题", "bullets": ["副标题"]},
    {"layout": "section", "title": "章节页标题"},
    {"layout": "content", "title": "内容页标题", "bullets": ["要点1", "要点2", "要点3"]},
    {"layout": "closing", "title": "谢谢观看"}
  ]}}
  slides 数组一次传全部页面即可；用户给的保存路径在工作空间外也没关系（系统会请求用户确认）。
  生成完成后必须用真实输出向用户汇报文件路径，严禁谎报成功。
- 需要联网查资料时使用：{"tool": "search.web", "args": {"query": "搜索词"}}
- `;
  // 把"输出约定：\n- "开头替换为 insertion（保留后续原有条目）
  const after = s.slice(idx);
  const firstDash = after.indexOf('- ');
  s = s.slice(0, idx) + insertion + after.slice(firstDash + 2);
  fs.writeFileSync(src, s);
  console.log('[1] 源码已注入');
} else {
  console.log('[1] 提示词已存在，跳过');
}

// 立即构建（不等下一轮）
console.log('[2] 构建 agent-runtime…');
execSync('pnpm.cmd -F @wdbuddy/agent-runtime build', { cwd: root, stdio: 'pipe', timeout: 90000 });
console.log('[2] 构建完成');

// dist 验证
let d = fs.readFileSync(dist, 'utf8');
if (d.includes(MARK)) {
  console.log('[3] dist 验证：提示词已进产物 ✓');
  process.exit(0);
}
console.log('[3] dist 缺失提示词，直接补丁 dist…');
// dist 中的对应位置：buildStepSystemPrompt 内 "输出约定：" 字符串
const dAnchor = '输出约定：';
const di = d.indexOf(dAnchor);
if (di < 0) {
  console.log('[3] FATAL: dist 找不到锚点');
  process.exit(1);
}
const patch = `输出约定：\\n- 生成 PPT/演示文稿【必须】使用 office.generate（纯 JS 秒级完成，禁止用 Python/shell 方案）：\\n  {"tool": "office.generate", "args": {"path": "D:/xxx.pptx", "title": "总标题", "slides": [{"layout": "cover", "title": "封面主标题", "bullets": ["副标题"]}, {"layout": "section", "title": "章节页标题"}, {"layout": "content", "title": "内容页标题", "bullets": ["要点1", "要点2", "要点3"]}, {"layout": "closing", "title": "谢谢观看"}]}}\\n  slides 数组一次传全部页面即可；用户给的保存路径在工作空间外也没关系（系统会请求用户确认）。\\n  生成完成后必须用真实输出向用户汇报文件路径，严禁谎报成功。\\n- 需要联网查资料时使用：{"tool": "search.web", "args": {"query": "搜索词"}}\\n`;
d = d.slice(0, di) + patch + d.slice(di + dAnchor.length + 2); // +2 跳过 "\n- "
fs.writeFileSync(dist, d);
const d2 = fs.readFileSync(dist, 'utf8');
console.log('[3] dist 补丁验证:', d2.includes(MARK) ? 'OK' : 'FAILED');
process.exit(d2.includes(MARK) ? 0 : 1);
