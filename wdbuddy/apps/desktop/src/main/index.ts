/**
 * WDBuddy 主进程入口（§10.1）
 * 职责：窗口/托盘生命周期、IPC 路由、任务持久化与调度、Runtime 子进程管理（看门狗）、更新。
 */
import { app, BrowserWindow, ipcMain, protocol, safeStorage } from 'electron';
import * as path from 'node:path';
import { existsSync } from 'node:fs';
import { createMainWindow, ensureSingleInstance } from './window.js';
import { createTray } from './tray.js';
import { registerIpc, broadcastRuntimeEvent } from './ipc/index.js';
import { openDatabase } from './db/database.js';
import { TaskStore } from './services/task-store.js';
import { SettingsService } from './services/settings.js';
import { RuntimeBridge } from './services/runtime-bridge.js';
import { SkillStore } from './services/skill-store.js';
import { AuthService } from './services/auth.js';
import { UpdaterService } from './services/updater.js';
import { NotificationService } from './services/notification.js';
import type { ModelId, RuntimeEvent, RuntimeNotification, RuntimeProviderConfig } from '@wdbuddy/shared-types';
import { BUILTIN_MODEL_IDS } from '@wdbuddy/shared-types';

let mainWindow: BrowserWindow | null = null;
let runtimeBridge: RuntimeBridge | null = null;
/** 退出标志（Electron App 类型无 isQuitting 属性，用模块级标志替代） */
let isQuitting = false;

// ─────────── wb-preview:// 协议（模版库预览，修复 iframe srcDoc 黑屏） ───────────
// 背景：srcdoc iframe 继承页面 CSP（script-src 'self'），deck 内联翻页脚本被
// 阻止 → 所有 .slide 保持 display:none → 预览只剩 #111 黑底。协议响应是独立
// 文档，不受父页面 CSP 约束，内联脚本正常执行。
// URL 形如 wb-preview://templates/{themeId|layouts}，HTML 缓存自 Runtime RPC。
const previewHtmlCache = new Map<string, string>();

// registerSchemesAsPrivileged 必须在 app ready 之前调用（模块加载即执行）
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'wb-preview',
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
    },
  },
]);

/** 注册协议 handler（app ready 后调用一次） */
function registerWbPreviewHandler(): void {
  protocol.handle('wb-preview', async (request) => {
    // 标准协议 URL 解析：wb-preview://templates/business-blue →
    //   hostname='templates'，pathname='/business-blue' → key='business-blue'
    //   wb-preview://templates/layouts → key='layouts'
    const url = new URL(request.url);
    const key = decodeURIComponent(url.pathname.replace(/^\//, '')) || 'layouts';
    let html = previewHtmlCache.get(key);
    if (!html) {
      // 惰性刷新：启动预取失败/未完成时，首次请求触发补拉
      await refreshPreviewCache();
      html = previewHtmlCache.get(key);
    }
    return new Response(html ?? '', {
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });
  });
}

/** 拉取模版预览数据并填充协议缓存（templates:preview IPC 首次调用时触发） */
async function refreshPreviewCache(): Promise<void> {
  if (!runtimeBridge) return;
  try {
    await runtimeBridge.ensureStarted();
    const r = (await runtimeBridge.request('templates/preview', {})) as {
      themes: Array<{ id: string }>;
      htmls: Record<string, string>;
      layoutsHtml: string;
      cloneTemplates?: Array<{ id: string; previewHtml: string }>;
    };
    previewHtmlCache.clear();
    for (const t of r.themes) {
      previewHtmlCache.set(t.id, r.htmls[t.id] ?? '');
    }
    previewHtmlCache.set('layouts', r.layoutsHtml);
    for (const c of r.cloneTemplates ?? []) {
      previewHtmlCache.set(`clone-${c.id}`, c.previewHtml);
    }
  } catch (err) {
    console.error('[wb-preview] 预览缓存刷新失败：', err);
  }
}

async function bootstrap(): Promise<void> {
  // 1. 数据库
  const db = openDatabase(path.join(app.getPath('userData'), 'wdbuddy.db'));
  const store = new TaskStore(db.db);
  const settings = new SettingsService(store);

  // 2. 安全存储（safeStorage 包装：set 加密 / get 解密，必须对称）
  const SECURE_ENC_PREFIX = 'enc:';
  /** 历史无前缀密文识别：整串为 base64 且解码后以 OSCrypt v10/v11 版本头开头 */
  const isLegacyCipherBlob = (s: string): boolean => {
    if (!/^[A-Za-z0-9+/]+={0,2}$/.test(s)) return false;
    try {
      const head = Buffer.from(s, 'base64').subarray(0, 3).toString('latin1');
      return head === 'v10' || head === 'v11';
    } catch {
      return false;
    }
  };
  const secureStorage = {
    set(key: string, value: string): void {
      if (safeStorage.isEncryptionAvailable()) {
        localStorage_set(
          key,
          SECURE_ENC_PREFIX + safeStorage.encryptString(value).toString('base64')
        );
      } else {
        localStorage_set(key, value);
      }
    },
    get(key: string): string | null {
      const raw = localStorage_get(key);
      if (raw === null || raw === '') return raw;
      // 剥离可能的 enc: 前缀（含历史字节位移变体，如 nec:）得到密文部分
      let cipherText = raw;
      let wasEncrypted = false;
      if (raw.startsWith(SECURE_ENC_PREFIX)) {
        cipherText = raw.slice(SECURE_ENC_PREFIX.length);
        wasEncrypted = true;
      } else if (raw.startsWith('nec:')) {
        // 历史写入异常：前缀发生单字节位移（enc:→nec:），剥离后同 base64 密文
        cipherText = raw.slice(4);
        wasEncrypted = true;
      }
      // 双层 base64：整体可能被 JSON/base64 再包一层（诊断发现的历史形态）。
      // 检测：单层解码后若以 enc: 开头，取其内层
      try {
        const once = Buffer.from(cipherText, 'base64').toString('utf-8');
        if (once.startsWith(SECURE_ENC_PREFIX)) {
          cipherText = once.slice(SECURE_ENC_PREFIX.length);
          wasEncrypted = true;
        }
      } catch {
        /* 保持原样 */
      }
      // 尝试解密（密文部分已是 base64 的 cipher 字节）
      if (safeStorage.isEncryptionAvailable()) {
        try {
          const decrypted = safeStorage.decryptString(Buffer.from(cipherText, 'base64'));
          if (decrypted) return decrypted;
        } catch {
          /* fallthrough */
        }
      }
      // 最后兜底：确认是密文形态（enc:/nec: 前缀或 v10 blob）却解不开时返回 null，
      // 严禁把密文串当明文 API Key 下发给 Runtime（否则引发难以排查的 401）。
      if (wasEncrypted || isLegacyCipherBlob(raw)) return null;
      // 写入时加密不可用的旧明文数据，原样返回
      return raw;
    },
    delete(key: string): void {
      localStorage_del(key);
    },
  };
  // 小型加密 KV（复用 settings 表）
  const localStorage_set = (key: string, v: string) => store.setSetting(`secure:${key}`, v);
  const localStorage_get = (key: string): string | null => store.getSetting<string>(`secure:${key}`);
  const localStorage_del = (key: string) => store.setSetting(`secure:${key}`, null);

  const auth = new AuthService(
    process.env['WB_API_BASE_URL'] ?? 'https://api.wdbuddy.example.com',
    secureStorage
  );
  auth.restore();

  // 2.1 Provider 密钥管理（§10.4：密钥经 safeStorage 加密落 settings 表 secure:* 前缀）
  const PROVIDER_KEY_PREFIX = 'provider:';
  const providerKeyId = (modelId: string): string => `${PROVIDER_KEY_PREFIX}${modelId}`;
  const setProviderKey = (modelId: string, apiKey: string): void => {
    secureStorage.set(providerKeyId(modelId), apiKey);
  };
  const getProviderKey = (modelId: string): string => secureStorage.get(providerKeyId(modelId)) ?? '';
  const clearProviderKey = (modelId: string): void => {
    secureStorage.delete(providerKeyId(modelId));
  };

  /** Main → Runtime 的 Provider 配置源（initialize 握手与热更新共用） */
  const getProviderConfig = (): RuntimeProviderConfig => {
    const s = settings.get();
    const providerKeys: Partial<Record<ModelId, string>> = {};
    for (const modelId of BUILTIN_MODEL_IDS) {
      const key = getProviderKey(modelId);
      if (key) providerKeys[modelId] = key;
    }
    for (const p of s.models.customProviders) {
      const key = getProviderKey(p.id);
      if (key) providerKeys[p.id as ModelId] = key;
    }
    return {
      providerKeys,
      apiModelOverrides: s.models.apiModelOverrides ?? {},
      customProviders: s.models.customProviders,
    };
  };

  // 3. 通知
  const notifier = new NotificationService();

  // 4. Runtime 子进程（统一使用 dist 构建产物；agent-runtime 是独立 Node 进程，不参与 HMR）
  const runtimeEntry = resolveRuntimeEntry();

  const handleNotification = (n: RuntimeNotification): void => {
    if (n.method === 'task/done') {
      notifier.taskDone('任务', undefined);
    } else if (n.method === 'task/error') {
      const cause = String(n.params['cause'] ?? '');
      notifier.taskError('任务', cause);
    }
  };

  runtimeBridge = new RuntimeBridge({
    runtimeEntry,
    getProviderConfig,
    onNotification: handleNotification,
    onEvent: (event: RuntimeEvent) => {
      // 统一事件流广播渲染层（§10.2 chat/store 订阅）
      broadcastRuntimeEvent(() => mainWindow, event);
      // 任务状态落库
      if (event.type === 'task.done') {
        store.updateStatus(event.taskId, 'completed');
        for (const a of event.artifacts) store.insertArtifact(a);
        // 完成提示落库（修复：此前仅渲染层内存显示，导出/回看任务时
        // "✅ 任务已完成…"整段消失）
        const files = [
          ...new Set(event.artifacts.filter((a) => a.kind === 'file' && a.path).map((a) => a.path)),
        ];
        const fileList = files.length > 0 ? files.map((f) => `\n📄 ${f}`).join('') : '';
        store.appendMessage(
          event.taskId,
          'system',
          files.length > 0
            ? `✅ 任务已完成！生成的成果文件：${fileList}\n\n👉 已同步到右侧"文件"栏，可点击查看/预览；点右栏"打开目录"直达文件位置。`
            : '✅ 任务已完成！（本次任务无文件产物，回答内容见上方对话）'
        );
      } else if (event.type === 'task.error') {
        store.updateStatus(event.taskId, 'failed');
        store.appendMessage(event.taskId, 'system', `⚠ 任务失败：${event.cause}`);
      } else if (event.type === 'task.rejected') {
        store.updateStatus(event.taskId, 'draft');
      } else if (event.type === 'task.cancelled') {
        store.updateStatus(event.taskId, 'cancelled');
        store.appendMessage(event.taskId, 'system', '⏹ 任务已停止');
      } else if (event.type === 'task.awaiting.input') {
        // 等待用户补充信息：任务暂停（用户在输入框跟进后自动唤醒继续）
        store.updateStatus(event.taskId, 'paused');
        // Agent 提问落库（修复：提问仅存在于前端卡片，导出/回看时提问记录
        // 消失，对话呈现"用户自问自答"的错乱观感）
        store.appendMessage(event.taskId, 'assistant', event.question);
      } else if (event.type === 'step.update') {
        // upsert（修复：craft 模式无 plan.ready，UPDATE 空表 no-op →
        // 步骤时间线重进任务即消失、导出缺"执行过程"）
        store.upsertStep(event.taskId, event.step);
      } else if (event.type === 'artifact.created') {
        store.insertArtifact(event.artifact);
      } else if (event.type === 'plan.ready') {
        store.upsertSteps(event.taskId, event.steps);
        store.updateStatus(event.taskId, 'awaiting-approval');
      } else if (event.type === 'token.delta') {
        // 流式拼接落库（修复：此前每 delta 独立成条，导出时消息碎片化）
        store.appendAssistantDelta(event.taskId, event.text);
      }
    },
    onOffline: (reason) => {
      broadcastRuntimeEvent(() => mainWindow, { type: 'runtime.offline', reason });
      notifier.taskError('Agent Runtime', reason);
    },
  });

  // 5. 权限应答回流（Renderer → Main → Runtime）
  ipcMain.handle('permission:respond', (_e, payload: { requestId: string; decision: string }) => {
    runtimeBridge?.respondPermission(
      payload.requestId,
      payload.decision as 'allow-once' | 'allow-always-task' | 'deny'
    );
    return { ok: true };
  });

  // 6. 窗口 + 托盘 + IPC
  mainWindow = createMainWindow();
  const tray = createTray(() => mainWindow);
  registerIpc({
    store,
    settings,
    runtime: runtimeBridge,
    getProviderKey,
    setProviderKey,
    clearProviderKey,
    skillStore: new SkillStore(),
    getWindow: () => mainWindow,
  });

  // 7. 更新
  const updater = new UpdaterService((s) => {
    mainWindow?.webContents.send('update:status', s);
  });
  updater.startSilentCheck();

  // 8. 启动恢复：扫描 running/paused → interrupted 提示（ADR-0002）
  const interrupted = store.findInterruptedTasks();
  for (const t of interrupted) {
    store.updateStatus(t.id, 'interrupted');
    broadcastRuntimeEvent(() => mainWindow, {
      type: 'checkpoint.recovery',
      taskId: t.id,
      hint: `任务「${t.title}」因异常中断，可从检查点恢复`,
    });
  }

  // 9. 每日备份（可靠性 §13）
  db.dailyBackup(path.join(app.getPath('userData'), 'backups'));

  // 10. 拉起 Agent Runtime 子进程（ADR-0001：主进程拉起守护）
  // 非阻塞、非致命：失败仅记录日志，任务操作时经 ensureStarted() 惰性重试
  // 启动完成后预取模版预览缓存（wb-preview:// 协议数据源；失败时协议
  // handler 惰性刷新兜底，不阻塞应用启动）
  registerWbPreviewHandler();
  void runtimeBridge
    .start()
    .then(() => refreshPreviewCache())
    .catch((err: unknown) => {
      console.error('[main] Runtime 初始启动失败（任务运行时将自动重试）：', err);
    });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) mainWindow = createMainWindow();
  });

  // 最小化到托盘（FR-22）
  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault();
      mainWindow?.hide();
    }
  });

  const _unused = tray; // 托盘常驻引用
  void _unused;
  const _unused2 = updater;
  void _unused2;
}

/**
 * 解析 agent-runtime 入口（ADR-0001）。
 * dev 模式下 electron 以 dist/main/index.js 为入口启动，app.getAppPath() 返回的是
 * dist\main（入口所在目录）而非 apps\desktop，基于 getAppPath() 回溯会错位两级，
 * 拼出不存在的 apps\desktop\packages\...（MODULE_NOT_FOUND 根因）。
 * 因此统一基于 __dirname（编译产物位于 apps\desktop\dist\main）定位，双路径兜底：
 *   ① pnpm workspace 链接 apps/desktop/node_modules/@wdbuddy/agent-runtime
 *   ② monorepo 根 packages/agent-runtime（自 dist/main 向上回溯 4 级）
 * 两者均不存在（如打包产物未内置 runtime）时返回路径 ①，由 start() 失败日志暴露。
 */
function resolveRuntimeEntry(): string {
  const viaWorkspaceLink = path.resolve(
    __dirname,
    '../../node_modules/@wdbuddy/agent-runtime/dist/index.js'
  );
  if (existsSync(viaWorkspaceLink)) return viaWorkspaceLink;
  const viaMonorepoRoot = path.resolve(
    __dirname,
    '../../../../packages/agent-runtime/dist/index.js'
  );
  if (existsSync(viaMonorepoRoot)) return viaMonorepoRoot;
  return viaWorkspaceLink;
}

// ─────────── 生命周期 ───────────

if (!ensureSingleInstance()) {
  // 已有实例，退出
} else {
  app.whenReady().then(() => {
    void bootstrap().catch((err) => {
      console.error('[main] 启动失败：', err);
      app.quit();
    });
  });
}

app.on('before-quit', () => {
  isQuitting = true;
  runtimeBridge?.stop();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    // 托盘常驻：不退出
  }
});
