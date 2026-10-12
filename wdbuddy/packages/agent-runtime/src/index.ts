/**
 * Agent Runtime 入口（ADR-0001/0003）
 * JSON-RPC over stdio：Main 拉起本进程，经 stdin/stdout 双向通信。
 *
 * 请求（M→R）：runtime/initialize、task/run、task/control、task/loadCheckpoint
 * 通知（R→M）：task/plan.ready、task/step.update、task/token.delta、task/artifact.created、
 *             task/permission.request、task/done、task/error、runtime/heartbeat
 */
import * as readline from 'node:readline';
import * as path from 'node:path';
import type {
  ModelId,
  RpcRequest,
  RpcResponse,
  RuntimeNotification,
  RuntimeProviderConfig,
  RuntimeRunParams,
  Task,
  TaskControlRequest,
} from '@wdbuddy/shared-types';
import { FsGuard, policyFor } from '@wdbuddy/sandbox';
import { ModelGateway, createCustomProvider } from '@wdbuddy/model-gateway';
import { AgentLoop } from './core/loop.js';
import type { PlanApproval } from './core/loop.js';
import { ToolRegistry } from './tools/registry.js';
import { fsReadTool, fsWriteTool, fsListTool, fsDeleteTool } from './tools/fs.js';
import { shellExecTool } from './tools/shell.js';
import { webSearchTool } from './tools/search.js';
import { officeGenerateTool } from './tools/office.js';
import { buildTemplatesPreview } from './tools/templates-preview.js';
import { ddgSearch } from './tools/search-backend.js';
import { InMemoryCheckpointStore } from './checkpoint/store.js';
import { createLogger } from './logger.js';

const log = createLogger({ pid: process.pid });

// ───────────── JSON-RPC 传输层 ─────────────

function send(obj: RpcResponse | RuntimeNotification): void {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function sendResult(id: number, result: unknown): void {
  send({ jsonrpc: '2.0', id, result });
}

function sendError(id: number, code: number, message: string): void {
  send({ jsonrpc: '2.0', id, error: { code, message } });
}

// ───────────── 运行时状态 ─────────────

let initialized = false;
let gateway: ModelGateway | null = null;
/** modelId → API Key（Main 在 initialize / providers.update 时下发，密钥不落盘） */
const providerKeys = new Map<string, string>();
/** modelId → 模型名覆盖（设置页"模型名"输入框；空值回落 Provider 默认） */
const apiModelOverrides: Partial<Record<ModelId, string>> = {};
const runningLoops = new Map<string, AgentLoop>();
const permissionResolvers = new Map<string, (d: 'allow-once' | 'allow-always-task' | 'deny') => void>();
/** Plan 审批等待器（Loop 阻塞于 plan.ready，Main 转发 approve/reject 唤醒） */
const approvalResolvers = new Map<string, (a: PlanApproval) => void>();
/** taskId → AbortController（用户取消任务时 abort，循环/LLM 流即时中断） */
const cancelControllers = new Map<string, AbortController>();
/** taskId → 等待用户输入的 resolver（user.ask 阻塞中；用户跟进即唤醒继续） */
const askResolvers = new Map<string, (answer: string) => void>();

/** 应用 Main 下发的 Provider 配置：更新密钥表并重建网关（注册自定义 Provider） */
function applyProviderConfig(cfg: Partial<RuntimeProviderConfig>): void {
  for (const [modelId, key] of Object.entries(cfg.providerKeys ?? {})) {
    providerKeys.set(modelId, key ?? '');
  }
  for (const [modelId, apiModel] of Object.entries(cfg.apiModelOverrides ?? {})) {
    const trimmed = (apiModel ?? '').trim();
    if (trimmed) apiModelOverrides[modelId as ModelId] = trimmed;
    else delete apiModelOverrides[modelId as ModelId];
  }
  const gw = new ModelGateway({
    resolveApiKey: (modelId: ModelId) => providerKeys.get(modelId) ?? '',
    apiModelOverrides: { ...apiModelOverrides },
  });
  for (const def of cfg.customProviders ?? []) {
    gw.registerCustom(createCustomProvider(def));
  }
  gateway = gw;
}

/** 网关 LLM 流封装（桥接 ChatStreamRequest → Delta 流） */
function gatewayLlm(g: ModelGateway): import('./core/types.js').LlmCaller {
  return (req) => g.chatStream(req);
}

function makeLoop(task: Task): AgentLoop {
  if (!gateway) throw new Error('runtime 未初始化');
  const tools = new ToolRegistry();
  tools.register(fsReadTool);
  tools.register(fsWriteTool);
  tools.register(fsListTool);
  tools.register(fsDeleteTool);
  tools.register(shellExecTool);
  tools.register(webSearchTool);
  tools.register(officeGenerateTool);

  // 工作空间：任务未指定时分配默认工作空间（Documents/WDBuddy），
  // 避免空 roots 导致所有文件操作被拒（修复"没配工作空间就必失败"）
  const defaultWorkspace = path.join(
    process.env['USERPROFILE'] ?? process.env['HOME'] ?? process.cwd(),
    'Documents', 'WDBuddy'
  );
  const roots = task.workspaceUri ? [task.workspaceUri] : [defaultWorkspace];
  const fsGuard = new FsGuard({ workspaceRoots: roots, permissionLevel: task.permission });

  return new AgentLoop({
    emitter: {
      emit: (e) => {
        // RuntimeOutboundEvent → JSON-RPC 通知包装
        send({ jsonrpc: '2.0' as const, method: e.method, params: e.params });
      },
    },
    llm: gatewayLlm(gateway),
    complete: async (req) => gateway!.chat(req),
    permissionWaiter: (request) =>
      new Promise((resolve) => {
        permissionResolvers.set(request.requestId, resolve);
        send({ jsonrpc: '2.0', method: 'task/permission.request', params: { taskId: request.taskId, request } });
      }),
    approvalWaiter: (taskId) =>
      new Promise<PlanApproval>((resolve) => {
        approvalResolvers.set(taskId, resolve);
      }),
    signalFor: (taskId) => {
      let c = cancelControllers.get(taskId);
      if (!c) {
        c = new AbortController();
        cancelControllers.set(taskId, c);
      }
      return c.signal;
    },
    // search.web 检索后端注入（此前未注入导致"跳过外部搜索"空转）
    webSearch: ddgSearch,
    askWaiter: (taskId, question) =>
      new Promise((resolve) => {
        askResolvers.set(taskId, resolve);
        send({ jsonrpc: '2.0', method: 'task/awaiting.input', params: { taskId, question } });
      }),
    tools,
    policy: policyFor(task.permission, roots),
    fsGuard,
    checkpoint: new InMemoryCheckpointStore(),
    logger: log.child({ taskId: task.id }),
  });
}

// ───────────── 请求处理 ─────────────

async function handleRequest(req: RpcRequest): Promise<void> {
  const { method, id } = req;

  switch (method) {
    case 'runtime/initialize': {
      if (initialized) {
        sendResult(id, { ok: true, note: '已初始化（幂等）' });
        return;
      }
      applyProviderConfig((req.params ?? {}) as Partial<RuntimeProviderConfig>);
      initialized = true;
      // 心跳（5s 间隔）：Main 看门狗超时阈值为 15s，心跳必须显著小于阈值并留出
      // 2~3 个周期余量，否则心跳到达与超时判定存在边界竞态，会周期性误杀重启
      setInterval(() => {
        send({ jsonrpc: '2.0', method: 'runtime/heartbeat', params: { at: Date.now() } });
      }, 5_000).unref?.();
      sendResult(id, { ok: true, version: '1.0.0' });
      return;
    }

    case 'task/run': {
      if (!initialized || !gateway) {
        sendError(id, -32000, 'runtime 未初始化，请先 runtime/initialize');
        return;
      }
      const { task, instruction, history, pendingSteps } = req.params as RuntimeRunParams;
      // 用户跟进唤醒：任务正阻塞于 user.ask 等待输入时，直接把回复交给等待方，
      // 循环在原上下文继续（不重新规划、不丢历史）
      const askResolve = askResolvers.get(task.id);
      if (askResolve) {
        askResolvers.delete(task.id);
        askResolve(instruction);
        sendResult(id, { ok: true, note: '已唤醒等待输入的任务' });
        return;
      }
      try {
        const loop = makeLoop(task);
        runningLoops.set(task.id, loop);
        sendResult(id, { ok: true });
        // 异步执行：不阻塞 RPC 响应（携带完整历史与未完成步骤续跑）
        void loop.run(task, instruction, { history, pendingSteps }).catch((err: unknown) => {
          send({
            jsonrpc: '2.0',
            method: 'task/error',
            params: { taskId: task.id, cause: err instanceof Error ? err.message : String(err) },
          });
        });
      } catch (err) {
        sendError(id, -32001, `任务启动失败：${err instanceof Error ? err.message : String(err)}`);
      }
      return;
    }

    case 'task/control': {
      const { taskId, action, steps } = req.params as TaskControlRequest;
      if (action === 'approve' || action === 'reject') {
        // Plan 审批结果：唤醒 Loop 中阻塞的 approvalWaiter（批准可携带编辑后步骤）
        const resolve = approvalResolvers.get(taskId);
        if (resolve) {
          resolve(
            action === 'approve'
              ? { decision: 'approve', steps }
              : { decision: 'reject' }
          );
          approvalResolvers.delete(taskId);
        }
        sendResult(id, { ok: true, note: `审批信号已接收：${action}` });
        return;
      }
      const loop = runningLoops.get(taskId);
      if (!loop) {
        sendResult(id, { ok: false, note: '任务未在运行' });
        return;
      }
      // 取消：abort 信号即时中断 LLM 流/权限等待，循环捕获后优雅收尾；
      // 暂停/继续仅清理注册表（当前实现无挂起语义，状态由 Main 落库）
      if (action === 'cancel') {
        runningLoops.delete(taskId);
        cancelControllers.get(taskId)?.abort('用户取消');
        cancelControllers.delete(taskId);
      }
      sendResult(id, { ok: true });
      return;
    }

    case 'task/loadCheckpoint': {
      const { taskId } = req.params as { taskId: string };
      const loop = runningLoops.get(taskId);
      const snap = loop ? null : null;
      sendResult(id, { taskId, hasCheckpoint: snap !== null });
      return;
    }

    case 'templates/preview': {
      // 模版库预览：main 经 JSON-RPC 调用（agent-runtime 为 ESM，main 进程
      // 无法直接 require；本进程加载自家渲染引擎零障碍，静态数据缓存复用）
      sendResult(id, buildTemplatesPreview());
      return;
    }

    default:
      sendError(id, -32601, `未知方法：${method satisfies never}`);
  }
}

// ───────────── Main → Runtime 权限应答（非标准 RPC：runtime/permission.respond 通知） ─────────────

function handleNotification(line: string): void {
  try {
    const obj = JSON.parse(line) as {
      method?: string;
      params?: { requestId?: string; decision?: string };
    };
    if (obj.method === 'runtime/permission.respond' && obj.params?.requestId) {
      const resolve = permissionResolvers.get(obj.params.requestId);
      if (resolve) {
        resolve((obj.params.decision ?? 'deny') as 'allow-once' | 'allow-always-task' | 'deny');
        permissionResolvers.delete(obj.params.requestId);
      }
      return;
    }
    // 设置/密钥变更后 Main 推送新配置（无需重启；运行中任务继续用旧网关，新任务用新网关）
    if (obj.method === 'runtime/providers.update') {
      applyProviderConfig((obj.params ?? {}) as Partial<RuntimeProviderConfig>);
    }
  } catch {
    /* 忽略无法解析的通知 */
  }
}

// ───────────── 主循环 ─────────────

const rl = readline.createInterface({ input: process.stdin, terminal: false });

rl.on('line', (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  try {
    const obj = JSON.parse(trimmed) as { id?: number };
    if (typeof obj.id === 'number') {
      void handleRequest(obj as RpcRequest);
    } else {
      handleNotification(trimmed);
    }
  } catch {
    log.warn('无法解析的输入行', { line: trimmed.slice(0, 100) });
  }
});

rl.on('close', () => {
  log.info('stdin 关闭，runtime 退出');
  process.exit(0);
});

log.info('Agent Runtime 已启动（JSON-RPC over stdio）');
