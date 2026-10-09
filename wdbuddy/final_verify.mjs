import fs from 'node:fs';
const root = 'D:/AI/MyClaude/wdbuddy';
const results = [];

function check(name, path, keyword) {
  try {
    const c = fs.readFileSync(path, 'utf8');
    results.push([name, c.includes(keyword)]);
  } catch (e) {
    results.push([name, false]);
  }
}

check('executor: office.generate 提示词', root + '/packages/agent-runtime/dist/core/executor.js', 'office.generate');
check('executor: 谎报禁令', root + '/packages/agent-runtime/dist/core/executor.js', '严禁谎报成功');
check('executor: search.web 用法', root + '/packages/agent-runtime/dist/core/executor.js', 'search.web');
check('runtime: ddgSearch 注入', root + '/packages/agent-runtime/dist/index.js', 'ddgSearch');
check('runtime: officeGenerateTool 注册', root + '/packages/agent-runtime/dist/index.js', 'officeGenerateTool');
check('desktop: chat:export handler', root + '/apps/desktop/dist/main/ipc/index.js', 'chat:export');
check('desktop: 保存对话框', root + '/apps/desktop/dist/main/ipc/index.js', 'showSaveDialog');
check('preload: chatExport', root + '/apps/desktop/dist/preload/index.js', 'chatExport');
check('renderer: 导出按钮(chatExport 调用)', root + '/apps/renderer/dist/assets/index-Bghr-zF6.js', 'chatExport');
check('renderer: StepTimeline 实时耗时', root + '/apps/renderer/dist/assets/index-Bghr-zF6.js', 'setInterval');

let all = true;
for (const [name, ok] of results) {
  console.log((ok ? '[PASS] ' : '[FAIL] ') + name);
  if (!ok) all = false;
}
console.log(all ? '=== ALL PASS ===' : '=== FAILED ===');
process.exitCode = all ? 0 : 1;
