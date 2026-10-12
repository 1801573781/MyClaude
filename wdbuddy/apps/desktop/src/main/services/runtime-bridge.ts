/**
 * runtime-bridge（§10.1）— 唯一持有 Runtime 子进程句柄
 * - 拉起 Agent Runtime 子进程（node agent-runtime）
 * - JSON-RPC 客户端：请求 id 递增，Promise 映射
 * - Runtime 通知转译为 IPC 事件 runtime:event 广播渲染层
 * - 看门狗：心跳超时 15s → kill → 指数退避重启，3 次失败进入 runtime-offline 降级态
 */
import { spawn, type ChildProcess } from 'node:child_process';
import * as path from 'node:path';
import { EventEmitter } from 'node:events';
import type {
  RpcResponse,
  RuntimeEvent,
  RuntimeNotification,
  RuntimeProviderConfig,
  Task,
  TaskControlRequest,
} from '@wdbuddy/shared-types';

const HEARTBEAT_TIMEOUT_MS = 15_000;
const MAX_RESTARTS = 3;

export interface RuntimeBridgeOptions {
  /** agent-runtime 入口（.ts 源或构建产物 .js） */
  runtimeEntry: string;
  /** 心跳超时（测试可调小） */
  heartbeatTimeoutMs?: number;
  /** Provider 配置源（§10.4 密钥注入）：initialize 握手与热更新时取用 */
  getProviderConfig: () => RuntimeProviderConfig;
  /** 通知回调 → 主进程转 IPC */
  onNotification: (n: RuntimeNotification) => void;
  /** 事件回调（转译后的统一事件流） */
  onEvent: (e: RuntimeEvent) => void;
  /** 降级态回调 */
  onOffline: (reason: string) => void;
}

interface Pending {
  resolve: (v: unknown) => void;
  reject: (e: Error) => void;
}

export class RuntimeBridge extends EventEmitter {
  private child: ChildProcess | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private lastHeartbeat = Date.now();
  private restarts = 0;
  private watchdogTimer: NodeJS.Timeout | null = null;
  private stopped = false;
  /** 进行中的惰性启动 Promise（防止并发 ensureStarted 重复拉起子进程） */
  private startPromise: Promise<void> | null = null;

  constructor(private readonly opts: RuntimeBridgeOptions) {
    super();
  }

  get isRunning(): boolean {
    return this.child !== null && !this.child.killed;
  }

  /** 拉起 Runtime 子进程并完成 initialize 握手 */
  async start(): Promise<void> {
    if (this.isRunning) return;
    this.stopped = false;

    const isWin = process.platform === 'win32';
    void isWin;
    // Electron 主进程内 process.execPath 是 electron.exe：
    // 加 ELECTRON_RUN_AS_NODE=1 让其以纯 Node 模式运行子进程
    const args = [this.opts.runtimeEntry];
    this.child = spawn(process.execPath, args, {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
      env: {
        ...process.env,
        ELECTRON_RUN_AS_NODE: '1',
        NODE_ENV: process.env['NODE_ENV'] ?? 'development',
      },
    });

    const child = this.child;
    if (!child.stdout || !child.stdin) throw new Error('Runtime stdio 不可用');

    let buffer = '';
    child.stdout.setEncoding('utf-8');
    child.stdout.on('data', (chunk: string) => {
      buffer += chunk;
      let idx: number;
      while ((idx = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 1);
        if (line) this.handleLine(line);
      }
    });

    child.stderr?.setEncoding('utf-8');
    child.stderr?.on('data', (d: string) => {
      process.stderr.write(`[runtime] ${d}`);
    });

    child.on('exit', (code) => {
      this.emit('exit', code);
      this.clearWatchdog();
      // 进程已退出，句柄立即失效，避免 isRunning 误报“仍在运行”
      if (this.child === child) this.child = null;
      // 释放全部 pending，避免调用方悬挂至 30s 超时
      for (const [, p] of this.pending) p.reject(new Error(`Runtime 进程退出（code=${code}）`));
      this.pending.clear();
      // 异常退出且未主动停止 → 看门狗重启逻辑
      if (!this.stopped && code !== 0) {
        void this.tryRestart(`Runtime 异常退出（code=${code}）`);
      }
    });

    // 初始化握手（携带 Provider 密钥/模型名覆盖/自定义 Provider 完整配置。
    // 修复：此前传空对象，Runtime 每次启动密钥全空，直到用户重新保存设置那一刻
    // 才经 providers.update 推送——表现为"配好 Key 能用，重启后全模型报未配置"）
    try {
      await this.request('runtime/initialize', this.opts.getProviderConfig());
    } catch (err) {
      // 握手失败：清理半启动的子进程，避免残留“假运行”状态
      this.killChild();
      throw err;
    }
    this.startWatchdog();
  }

  /** 确保运行：未运行则惰性拉起（并发安全；拉起成功后重置看门狗退避计数） */
  async ensureStarted(): Promise<void> {
    if (this.isRunning) return;
    if (this.stopped) throw new Error('Runtime 已停止（应用退出中）');
    if (!this.startPromise) {
      this.startPromise = this.start()
        .then(() => {
          this.restarts = 0; // 手动拉起成功，重置退避计数（offline 降级态可被用户操作唤醒）
        })
        .finally(() => {
          this.startPromise = null;
        });
    }
    return this.startPromise;
  }

  /** RPC 请求 */
  async request(method: string, params: object): Promise<unknown> {
    if (!this.isRunning || !this.child?.stdin) {
      throw new Error('Runtime 未运行');
    }
    const id = this.nextId++;
    const json = JSON.stringify({ jsonrpc: '2.0', id, method, params });
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.child!.stdin!.write(json + '\n');
      // 30s 请求超时
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`Runtime 请求超时：${method}`));
        }
      }, 30_000).unref?.();
    });
  }

  /** 权限应答（通知形式下发） */
  respondPermission(requestId: string, decision: 'allow-once' | 'allow-always-task' | 'deny'): void {
    this.notify('runtime/permission.respond', { requestId, decision });
  }

  /** 通知（无需应答） */
  notify(method: string, params: object): void {
    if (this.isRunning && this.child?.stdin) {
      this.child.stdin.write(JSON.stringify({ method, params }) + '\n');
    }
  }

  /** 推送最新 Provider 配置（主进程在设置/密钥变更后调用） */
  updateProviders(): void {
    this.notify('runtime/providers.update', this.opts.getProviderConfig());
  }

  /** 运行任务（carry：跟进时的完整对话历史与上轮未完成步骤） */
  async runTask(
    task: Task,
    instruction: string,
    carry?: {
      history?: Array<{ role: 'user' | 'assistant'; content: string }>;
      pendingSteps?: import('@wdbuddy/shared-types').PlanStep[];
    }
  ): Promise<void> {
    await this.ensureStarted();
    await this.request('task/run', { task, instruction, ...carry });
  }

  /** 任务控制 */
  async controlTask(req: TaskControlRequest): Promise<unknown> {
    await this.ensureStarted();
    // TaskControlRequest 无索引签名，展开为普通对象满足 Record<string, unknown>
    // approve 时 steps（PlanCard 编辑后的步骤）一并转发给 Runtime
    return this.request('task/control', {
      taskId: req.taskId,
      action: req.action,
      ...(req.steps ? { steps: req.steps } : {}),
    });
  }

  private handleLine(line: string): void {
    try {
      const obj = JSON.parse(line) as { id?: number } & Partial<RpcResponse & RuntimeNotification>;
      if (typeof obj.id === 'number' && (obj.result !== undefined || obj.error !== undefined)) {
        // RPC 响应
        const p = this.pending.get(obj.id);
        if (p) {
          this.pending.delete(obj.id);
          if (obj.error) p.reject(new Error(obj.error.message));
          else p.resolve(obj.result);
        }
        return;
      }
      if (obj.method) {
        // 通知
        const n = obj as RuntimeNotification;
        if (n.method === 'runtime/heartbeat') {
          this.lastHeartbeat = Date.now();
          return;
        }
        this.opts.onNotification(n);
        this.translateToEvent(n);
      }
    } catch {
      /* 忽略非 JSON 行 */
    }
  }

  /** 通知 → RuntimeEvent 转译（统一事件流，§11.2） */
  private translateToEvent(n: RuntimeNotification): void {
    const p = n.params as Record<string, unknown>;
    const taskId = typeof p['taskId'] === 'string' ? p['taskId'] : '';
    switch (n.method) {
      case 'task/plan.ready':
        this.opts.onEvent({ type: 'plan.ready', taskId, steps: p['steps'] as never });
        break;
      case 'task/step.update':
        this.opts.onEvent({ type: 'step.update', taskId, step: p['step'] as never });
        break;
      case 'task/token.delta':
        this.opts.onEvent({
          type: 'token.delta',
          taskId,
          text: String(p['text'] ?? ''),
          usage: p['usage'] as never,
        });
        break;
      case 'task/artifact.created':
        this.opts.onEvent({ type: 'artifact.created', taskId, artifact: p['artifact'] as never });
        break;
      case 'task/permission.request':
        this.opts.onEvent({ type: 'permission.request', taskId, request: p['request'] as never });
        break;
      case 'task/done':
        this.opts.onEvent({
          type: 'task.done',
          taskId,
          report: p['report'] as never,
          artifacts: p['artifacts'] as never,
        });
        break;
      case 'task/error':
        this.opts.onEvent({
          type: 'task.error',
          taskId,
          stepId: typeof p['stepId'] === 'string' ? p['stepId'] : undefined,
          cause: String(p['cause'] ?? '未知错误'),
        });
        break;
      case 'task/rejected':
        this.opts.onEvent({
          type: 'task.rejected',
          taskId,
          reason: String(p['reason'] ?? '用户拒绝计划'),
        });
        break;
      case 'task/cancelled':
        this.opts.onEvent({ type: 'task.cancelled', taskId, reason: String(p['reason'] ?? '用户取消') });
        break;
      case 'task/awaiting.input':
        // user.ask：Agent 提问后暂停，渲染层展示醒目等待卡片（options=可点选项）
        this.opts.onEvent({
          type: 'task.awaiting.input',
          taskId,
          question: String(p['question'] ?? ''),
          options: Array.isArray(p['options']) ? (p['options'] as string[]) : undefined,
        });
        break;
      default:
        break;
    }
  }

  // ─────────── 看门狗 ───────────

  private startWatchdog(): void {
    this.lastHeartbeat = Date.now();
    const interval = this.opts.heartbeatTimeoutMs ?? HEARTBEAT_TIMEOUT_MS;
    this.watchdogTimer = setInterval(() => {
      if (Date.now() - this.lastHeartbeat > interval) {
        this.clearWatchdog();
        void this.tryRestart('心跳超时（15s 无心跳）');
      }
    }, Math.max(1000, Math.floor(interval / 2)));
    this.watchdogTimer.unref?.();
  }

  private clearWatchdog(): void {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
    }
  }

  /** 指数退避重启：1s/2s/4s；3 次失败进入 runtime-offline */
  private async tryRestart(reason: string): Promise<void> {
    this.killChild();
    if (this.stopped) return;
    if (this.restarts >= MAX_RESTARTS) {
      this.opts.onOffline(`Runtime 重启 ${MAX_RESTARTS} 次仍失败：${reason}`);
      return;
    }
    const delay = 1000 * Math.pow(2, this.restarts);
    this.restarts++;
    await new Promise((r) => setTimeout(r, delay));
    try {
      await this.start();
      this.restarts = 0; // 成功后重置计数
    } catch {
      await this.tryRestart(reason);
    }
  }

  private killChild(): void {
    if (this.child && !this.child.killed) {
      this.child.removeAllListeners('exit');
      this.child.kill('SIGKILL');
    }
    this.child = null;
    // 释放全部 pending
    for (const [, p] of this.pending) p.reject(new Error('Runtime 进程已终止'));
    this.pending.clear();
  }

  stop(): void {
    this.stopped = true;
    this.clearWatchdog();
    this.killChild();
  }
}
