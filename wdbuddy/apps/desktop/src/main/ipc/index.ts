/**
 * IPC 路由（§11.1）
 * task:create/list/control/approvePlan、prompt:enhance、workspace:grant、fs:reveal、
 * settings:get/set、runtime:event（M→R 广播）
 * 通道与 Payload 类型全部来自 shared-types，禁止 any。
 */
import { BrowserWindow, dialog, ipcMain, shell } from 'electron';
import * as fs from 'node:fs';
import type {
  AppSettings,
  Message,
  ModelId,
  NewTaskInput,
  PlanApprovalRequest,
  ProviderKeyEntry,
  PromptEnhanceResult,
  RuntimeEvent,
  TaskControlRequest,
  TaskFilter,
} from '@wdbuddy/shared-types';
import { BUILTIN_MODEL_IDS } from '@wdbuddy/shared-types';
import type { TaskStore } from '../services/task-store.js';
import type { SettingsService } from '../services/settings.js';
import type { RuntimeBridge } from '../services/runtime-bridge.js';
import type { SkillStore } from '../services/skill-store.js';

export interface IpcDeps {
  store: TaskStore;
  settings: SettingsService;
  runtime: RuntimeBridge;
  /** 内置 + 自定义模型密钥存取（main/index.ts 构造，经 safeStorage 加密） */
  getProviderKey: (modelId: string) => string;
  setProviderKey: (modelId: string, apiKey: string) => void;
  clearProviderKey: (modelId: string) => void;
  /** 自定义技能存储 */
  skillStore: SkillStore;
  getWindow: () => BrowserWindow | null;
}

export function registerIpc(deps: IpcDeps): void {
  const { store, settings, runtime, getProviderKey, setProviderKey, clearProviderKey } = deps;

  // ─────────── 任务 ───────────

  ipcMain.handle('task:create', (_e, payload: { input: NewTaskInput }) => {
    const id = `task-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const title = payload.input.description.slice(0, 30) || '新任务';
    const task = store.createTask(payload.input, id, title);
    store.appendMessage(id, 'user', payload.input.description);
    return { taskId: task.id };
  });

  ipcMain.handle('task:list', (_e, payload: { filter: TaskFilter }) => {
    return { tasks: store.listTasks(payload.filter) };
  });

  ipcMain.handle('task:control', async (_e, req: TaskControlRequest) => {
    const task = store.getTask(req.taskId);
    if (!task) return { ok: true };

    switch (req.action) {
      case 'start': {
        store.updateStatus(req.taskId, 'planning');
        // 顶栏当前模型同步进任务：修复"已有任务永远复用创建时的 modelId，
        // 顶栏切换模型无效"（表现为无论选什么都按旧模型/Auto 路由报错）
        if (req.modelId && req.modelId !== task.modelId) {
          store.updateTaskModel(req.taskId, req.modelId);
        }
        // Composer 当前模式同步进任务：修复"模式在任务创建时固化，之后切换
        // Ask/Plan/Craft 无效"（plan 任务永远要求审批 / ask 任务永远不出计划）
        if (req.mode && req.mode !== task.mode) {
          store.updateTaskMode(req.taskId, req.mode);
        }
        // 工作空间回填：任务未指定时取设置页配置的首个工作空间。
        // 修复"侧栏配置的工作空间从未被任务使用"（取证实锤：所有任务 ws=null，
        // Agent 只能写默认目录）——现在配置了工作空间的任务，Agent 的文件操作
        // 沙箱就以它为边界
        if (!task.workspaceUri) {
          const firstWs = settings.get().workspaces[0];
          if (firstWs) store.updateTaskWorkspace(req.taskId, firstWs);
        }
        const taskNow = store.getTask(req.taskId) ?? task;
        // 跟进指令：追加为用户消息并作为本次执行指令
        // （修复：此前跟进输入被静默丢弃，任务反复重跑首条指令）
        const followUp = req.instruction?.trim();
        let instruction = taskNow.title;
        if (followUp) {
          store.appendMessage(req.taskId, 'user', followUp);
          instruction = followUp;
        } else {
          const msgs = store.listMessages(req.taskId);
          const firstUser = msgs.find((m) => m.role === 'user');
          instruction = firstUser?.content ?? taskNow.title;
        }
        // 完整对话历史（跟进续跑的关键上下文；单条截断防超长）：
        // 修复"跟进后任务从头来一遍"——此前 Runtime 只收到本轮单条指令
        const history = store
          .listMessages(req.taskId)
          .filter((m) => m.role === 'user' || m.role === 'assistant')
          .map((m) => ({ role: m.role as 'user' | 'assistant', content: m.content.slice(0, 4000) }));
        // 上轮未完成步骤：存在则续跑，全完成则由 Runtime 重新规划
        const pendingSteps = store
          .listSteps(req.taskId)
          .filter((s) => s.status === 'pending' || s.status === 'running');
        await runtime.runTask({ ...taskNow, status: 'running' }, instruction, { history, pendingSteps });
        store.updateStatus(req.taskId, 'running');
        break;
      }
      case 'resume':
        store.updateStatus(req.taskId, 'running');
        await runtime.controlTask(req);
        break;
      case 'cancel':
        store.updateStatus(req.taskId, 'cancelled');
        await runtime.controlTask(req);
        break;
      case 'approve':
        // Plan 审批：req.steps（PlanCard 用户可能编辑过）随请求转发 Runtime
        await runtime.controlTask(req);
        break;
      case 'reject':
        store.updateStatus(req.taskId, 'draft');
        await runtime.controlTask(req);
        break;
      case 'pause':
        store.updateStatus(req.taskId, 'paused');
        await runtime.controlTask(req);
        break;
    }
    return { ok: true };
  });

  ipcMain.handle('task:approvePlan', (_e, req: PlanApprovalRequest) => {
    store.upsertSteps(req.taskId, req.steps);
    store.updateStatus(req.taskId, 'running');
    return { ok: true };
  });

  // ─────────── 提示词增强（FR-07） ───────────

  ipcMain.handle('prompt:enhance', async (_e, payload: { text: string }): Promise<PromptEnhanceResult> => {
    // 结构化增强：背景/目标/约束/期望输出（主进程本地模板 + LLM 可选）
    const original = payload.text.trim();
    const enhanced = enhanceLocally(original);
    return {
      original,
      enhanced,
      title: original.slice(0, 20),
    };
  });

  // ─────────── 工作空间 ───────────

  ipcMain.handle('workspace:grant', async (e, _payload: { uri: string }) => {
    const win = BrowserWindow.fromWebContents(e.sender);
    const result = await dialog.showOpenDialog(win!, {
      properties: ['openDirectory'],
      title: '选择要授权给 WDBuddy 的文件夹',
    });
    if (result.canceled || result.filePaths.length === 0) {
      return { ok: false, uri: null };
    }
    const uri = result.filePaths[0]!;
    const s = settings.get();
    if (!s.workspaces.includes(uri)) {
      settings.set({ ...s, workspaces: [...s.workspaces, uri] });
    }
    return { ok: true, uri };
  });

  ipcMain.handle('fs:reveal', (_e, payload: { path: string }) => {
    void shell.showItemInFolder(payload.path);
    return { ok: true };
  });

  // ─────────── 设置 ───────────

  ipcMain.handle('settings:get', () => settings.get());

  ipcMain.handle('settings:set', (_e, payload: { settings: AppSettings }) => {
    settings.set(payload.settings);
    // 自定义 Provider 列表可能变化 → 推送最新配置到 Runtime（密钥同步已在 providerKey:set 处理）
    runtime.updateProviders();
    return { ok: true };
  });

  // ─────────── Provider 密钥（NFR-03：明文仅存主进程内存与 safeStorage，渲染层只见脱敏） ───────────

  /** 密钥脱敏展示（保留前 3 后 4） */
  const maskKey = (key: string): string => {
    if (key.length <= 8) return key ? '****' : '';
    return `${key.slice(0, 3)}***${key.slice(-4)}`;
  };

  ipcMain.handle('providerKey:list', (_e, _payload: Record<string, never>) => {
    const s = settings.get();
    const ids: ModelId[] = [...BUILTIN_MODEL_IDS, ...s.models.customProviders.map((p) => p.id as ModelId)];
    const keys: ProviderKeyEntry[] = ids.map((modelId) => {
      const key = getProviderKey(modelId);
      return { modelId, hasKey: key.length > 0, masked: maskKey(key) };
    });
    return { keys };
  });

  ipcMain.handle('providerKey:set', (_e, payload: { modelId: ModelId; apiKey: string }) => {
    if (payload.apiKey.trim()) {
      setProviderKey(payload.modelId, payload.apiKey.trim());
    } else {
      clearProviderKey(payload.modelId);
    }
    runtime.updateProviders();
    return { ok: true };
  });

  // ─────────── 任务详情（消息/步骤/产物） ───────────

  ipcMain.handle('task:messages', (_e, payload: { taskId: string }) => {
    return { messages: store.listMessages(payload.taskId) };
  });

  ipcMain.handle('task:steps', (_e, payload: { taskId: string }) => {
    return { steps: store.listSteps(payload.taskId) };
  });

  ipcMain.handle('task:artifacts', (_e, payload: { taskId: string }) => {
    return { artifacts: store.listArtifacts(payload.taskId) };
  });

  ipcMain.handle('task:rename', (_e, payload: { taskId: string; title: string }) => {
    store.renameTask(payload.taskId, payload.title);
    return { ok: true };
  });

  ipcMain.handle('task:delete', (_e, payload: { taskId: string }) => {
    store.deleteTask(payload.taskId);
    return { ok: true };
  });

  ipcMain.handle('task:accept', (_e, payload: { taskId: string }) => {
    // 验收闭环（FR-11）：任务进入已完成
    store.updateStatus(payload.taskId, 'completed');
    return { ok: true };
  });

  // ─────────── 自定义技能（FR-17） ───────────

  ipcMain.handle('skills:list', () => {
    return { skills: deps.skillStore.list() };
  });

  ipcMain.handle('skills:create', (_e, payload: { input: { name: string; description: string; icon: string; category: string; instruction: string; permissions: string[] } }) => {
    const r = deps.skillStore.create(payload.input);
    return r;
  });

  ipcMain.handle('skills:remove', (_e, payload: { id: string }) => {
    deps.skillStore.remove(payload.id);
    return { ok: true };
  });

  // ─────────── 模版库预览（10 主题 × 真实渲染 + 14 版式演示） ───────────

  ipcMain.handle('templates:preview', async () => {
    // 渲染逻辑在 Runtime 子进程完成（agent-runtime 为 ESM 包且 exports 只暴露"."，
    // main 进程无法直接 require——经 JSON-RPC 调用；runtime 离线时由渲染层显示错误提示）
    await runtime.ensureStarted();
    return (await runtime.request('templates/preview', {})) as import('@wdbuddy/shared-types').TemplatesPreviewResult;
  });

  // ─────────── 对话导出（.md / .html） ───────────

  ipcMain.handle(
    'chat:export',
    async (e, payload: { taskId: string; format: 'md' | 'html' }) => {
      const task = store.getTask(payload.taskId);
      if (!task) return { ok: false, path: '' };
      const messages = mergeFragmented(store.listMessages(payload.taskId));
      const steps = store.listSteps(payload.taskId);

      // 原生保存对话框：用户选择保存位置（默认文件名带任务标题+日期）
      const win = BrowserWindow.fromWebContents(e.sender);
      const safeName = (task.title || 'wdbuddy-chat').replace(/[\\/:*?"<>|]/g, '_').slice(0, 60);
      const now = new Date();
      const pad = (n: number): string => String(n).padStart(2, '0');
      const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
      const defaultName = `${safeName}_${ts}.${payload.format}`;
      const dlg = await dialog.showSaveDialog(win!, {
        title: '导出对话记录',
        defaultPath: defaultName,
        filters: [
          payload.format === 'md'
            ? { name: 'Markdown', extensions: ['md'] }
            : { name: 'HTML', extensions: ['html'] },
        ],
      });
      if (dlg.canceled || !dlg.filePath) return { ok: false, path: '' };
      const savePath = dlg.filePath;

      // Markdown：完整对话 + 执行过程
      const md: string[] = [
        `# ${task.title}`,
        '',
        `- 任务模式：${task.mode} | 模型：${task.modelId} | 状态：${task.status}`,
        `- 创建时间：${new Date(task.createdAt).toLocaleString('zh-CN')}`,
        '',
        '## 对话记录',
        '',
      ];
      for (const m of messages) {
        const who = m.role === 'user' ? '👤 **用户**' : m.role === 'assistant' ? '🤖 **WDBuddy**' : '⚙️ **系统**';
        md.push(`### ${who}（${new Date(m.createdAt).toLocaleString('zh-CN')}）`, '', m.content, '');
      }
      if (steps.length > 0) {
        md.push('## 执行过程', '');
        for (const s of steps) {
          const icon = s.status === 'done' ? '✓' : s.status === 'failed' ? '✕' : s.status === 'running' ? '●' : '○';
          const dur = s.startedAt && s.endedAt ? `（${((s.endedAt - s.startedAt) / 1000).toFixed(1)}s）` : '';
          md.push(`- ${icon} ${s.title}${dur}`);
          if (s.summary) md.push(`  - ${s.summary}`);
        }
        md.push('');
      }

      if (payload.format === 'md') {
        fs.writeFileSync(savePath, md.join('\n'), 'utf8');
        return { ok: true, path: savePath };
      }
      // HTML：内联轻量样式
      const esc = (t: string): string =>
        t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const msgHtml = messages
        .map((m) => {
          const cls = m.role === 'user' ? 'user' : m.role === 'assistant' ? 'agent' : 'sys';
          const name = m.role === 'user' ? '👤 用户' : m.role === 'assistant' ? '🤖 WDBuddy' : '⚙️ 系统';
          return `<div class="msg ${cls}"><div class="who">${name} · ${new Date(m.createdAt).toLocaleString('zh-CN')}</div><div class="body">${esc(m.content)}</div></div>`;
        })
        .join('\n');
      const stepsHtml = steps
        .map((s) => {
          const icon = s.status === 'done' ? '✓' : s.status === 'failed' ? '✕' : '○';
          return `<li>${icon} ${esc(s.title)}${s.summary ? ` — ${esc(s.summary)}` : ''}</li>`;
        })
        .join('\n');
      const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>${esc(task.title)}</title>
<style>
body{font-family:"Segoe UI","Microsoft YaHei",sans-serif;max-width:900px;margin:0 auto;padding:24px;background:#f7f8fa;color:#24292f}
h1{font-size:22px;border-bottom:2px solid #2f5496;padding-bottom:8px}
.meta{color:#57606a;font-size:13px;margin-bottom:20px}
.msg{border:1px solid #d0d7de;border-radius:10px;padding:12px 16px;margin:12px 0;background:#fff}
.msg.user{background:#eef4fb;border-color:#b6d0f0}
.msg.sys{background:#fdf3f2;border-color:#f0c8c4}
.who{font-size:12px;color:#57606a;margin-bottom:6px}
.body{white-space:pre-wrap;line-height:1.7;font-size:14px}
h2{font-size:17px;margin-top:28px}
ol,ul{line-height:2}
</style></head>
<body>
<h1>${esc(task.title)}</h1>
<div class="meta">模式：${task.mode} · 模型：${task.modelId} · 状态：${task.status} · 导出时间：${new Date().toLocaleString('zh-CN')}</div>
<h2>对话记录</h2>
${msgHtml}
${steps.length > 0 ? `<h2>执行过程</h2><ul>${stepsHtml}</ul>` : ''}
</body></html>`;
      fs.writeFileSync(savePath, html, 'utf8');
      return { ok: true, path: savePath };
    }
  );
}

/** 广播 RuntimeEvent 给渲染层 */
export function broadcastRuntimeEvent(getWindow: () => BrowserWindow | null, event: RuntimeEvent): void {
  const win = getWindow();
  if (win && !win.isDestroyed()) {
    win.webContents.send('runtime:event', event);
  }
}

/**
 * 合并历史碎片消息：旧版本落库时每个 token.delta 独立成条（导出"一字一条"）。
 * 规则：相邻 assistant 消息且时间差 < 3 秒 → 拼接为一条（新数据本身完整则无影响）。
 */
function mergeFragmented(messages: Message[]): Message[] {
  const out: Message[] = [];
  for (const m of messages) {
    const last = out[out.length - 1];
    if (
      last &&
      last.role === 'assistant' &&
      m.role === 'assistant' &&
      m.createdAt - last.createdAt < 3000
    ) {
      last.content += m.content;
      if (m.meta) last.meta = m.meta;
    } else {
      out.push({ ...m });
    }
  }
  return out;
}

/** 本地提示词增强模板（FR-07：背景/目标/约束/期望输出） */
function enhanceLocally(text: string): string {
  return [
    '## 背景',
    '我需要完成一项工作任务。',
    '',
    '## 目标',
    text,
    '',
    '## 约束',
    '- 产物需可直接使用，无需二次加工',
    '- 中文输出，符合中文办公规范',
    '- 涉及文件操作限定在已授权工作空间内',
    '',
    '## 期望输出',
    '- 交付完整成果文件，并附简要说明',
    '- 若有数据整理，保证准确性与格式统一',
  ].join('\n');
}
