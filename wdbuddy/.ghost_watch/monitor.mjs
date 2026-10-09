/**
 * 还原现象监控（实验B）：每 2 秒记录目标文件的 md5+mtime 到 watch.log
 * 目标：probe_marker.txt（新文件）+ SkillsCenter.tsx（历史被还原文件）
 * 运行 3 分钟后自动退出；每条记录带时间戳，任何改写一目了然
 */
import * as fs from 'node:fs';
import * as crypto from 'node:crypto';

const root = 'D:/AI/MyClaude/wdbuddy';
const targets = [
  '.ghost_watch/probe_marker.txt',
  'apps/renderer/src/features/skills/SkillsCenter.tsx',
  'apps/renderer/src/features/chat/StepTimeline.tsx',
];
const logPath = root + '/.ghost_watch/watch.log';

const startTime = Date.now();
const DURATION_MS = 3 * 60 * 1000;

function fingerprint(p) {
  try {
    const buf = fs.readFileSync(root + '/' + p);
    return crypto.createHash('md5').update(buf).digest('hex').slice(0, 10) + ' mtime=' + fs.statSync(root + '/' + p).mtime.toISOString();
  } catch (e) {
    return 'MISSING(' + e.code + ')';
  }
}

fs.writeFileSync(logPath, '=== watch start ' + new Date().toISOString() + ' ===\n');
console.log('monitor started, duration 3min, log:', logPath);

let last = {};
const timer = setInterval(() => {
  const now = new Date().toISOString();
  let line = now + ' | ';
  let changed = false;
  for (const t of targets) {
    const fp = fingerprint(t);
    if (last[t] !== fp) {
      changed = true;
      line += `[CHANGED] ${t} -> ${fp} ; `;
    }
    last[t] = fp;
  }
  if (changed) fs.appendFileSync(logPath, line + '\n');
  if (Date.now() - startTime > DURATION_MS) {
    clearInterval(timer);
    fs.appendFileSync(logPath, '=== watch end ' + new Date().toISOString() + ' ===\n');
    console.log('monitor finished');
    process.exit(0);
  }
}, 2000);
