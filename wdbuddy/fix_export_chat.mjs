/**
 * 原子修复：ChatPanel.exportChat（第一版遗留错误逻辑）→ 简洁版
 * + ipc.ts 无需动 + 全量构建 + dist 验证
 */
import fs from 'node:fs';
import { execSync } from 'node:child_process';

const root = 'D:/AI/MyClaude/wdbuddy';
const file = root + '/apps/renderer/src/features/chat/ChatPanel.tsx';

let s = fs.readFileSync(file, 'utf8');
const startMark = '/** 导出当前任务对话为 .md / .html（主进程读取数据库完整记录生成文件） */';
const endMark = '  };';

const startIdx = s.indexOf(startMark);
if (startIdx < 0) {
  console.log('[ChatPanel] 未找到旧版 exportChat（可能已被替换），检查简洁版是否存在');
  console.log('[ChatPanel] 简洁版存在:', s.includes('api.fsReveal(result.path)'));
} else {
  // 找到该函数块结尾：从 startIdx 开始第一个 "  };" 后的换行
  let endIdx = s.indexOf(endMark + '\n', startIdx);
  if (endIdx < 0) endIdx = s.indexOf(endMark, startIdx) + endMark.length;
  else endIdx += endMark.length;
  const replacement = `/** 导出当前任务对话为 .md / .html：主进程弹原生保存对话框并生成文件 */
  const exportChat = async (format: 'md' | 'html'): Promise<void> => {
    if (!task) return;
    const api = requireApi();
    const result = await api.chatExport(task.id, format);
    if (result.ok) {
      void api.fsReveal(result.path);
    }
  };`;
  s = s.slice(0, startIdx) + replacement + s.slice(endIdx);
  fs.writeFileSync(file, s);
  console.log('[ChatPanel] exportChat 已替换为简洁版');
  const check = fs.readFileSync(file, 'utf8');
  console.log('[ChatPanel] 回读确认:', check.includes('api.fsReveal(result.path)') && !check.includes('__wbLastExportDir'));
}

// 全量构建（shared-types → desktop → renderer 全链）
console.log('[build] 全量构建 desktop + renderer…');
execSync('pnpm.cmd exec turbo run build --force --filter=@wdbuddy/desktop --filter=@wdbuddy/renderer', {
  cwd: root, stdio: 'pipe', timeout: 300000,
});
console.log('[build] 完成');

// dist 验证
const checks = [
  ['renderer 导出按钮', root + '/apps/renderer/dist/assets', null],
];
// renderer 是 bundle，检查产物目录中的 js 是否含 "chatExport"
const assetsDir = root + '/apps/renderer/dist/assets';
const files = fs.readdirSync(assetsDir).filter(f => f.endsWith('.js'));
let rendererOk = false;
for (const f of files) {
  const c = fs.readFileSync(assetsDir + '/' + f, 'utf8');
  if (c.includes('chatExport')) { rendererOk = true; break; }
}
console.log('[verify] renderer bundle 含 chatExport:', rendererOk);
const desktopJs = fs.readFileSync(root + '/apps/desktop/dist/main/ipc/index.js', 'utf8');
console.log('[verify] desktop chat:export handler:', desktopJs.includes('chat:export'));
const preloadJs = fs.readFileSync(root + '/apps/desktop/dist/preload/index.js', 'utf8');
console.log('[verify] preload chatExport:', preloadJs.includes('chatExport'));
const executorJs = fs.readFileSync(root + '/packages/agent-runtime/dist/core/executor.js', 'utf8');
console.log('[verify] executor office.generate 提示词:', executorJs.includes('office.generate'));
console.log(executorJs.includes('office.generate') && rendererOk && desktopJs.includes('chat:export') && preloadJs.includes('chatExport') ? '=== ALL PASS ===' : '=== CHECK FAILED ===');
