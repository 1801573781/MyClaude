/**
 * Agent Runtime 内部类型
 * 面向接口 + 依赖注入（可测试性 NFR-04）：Planner/Executor/工具全部可注入 mock。
 */
import type {
  Artifact,
  ChatMessage,
  ChatStreamRequest,
  Delta,
  PermissionRequest,
  PlanStep,
  Task,
  VerifyReport,
} from '@wdbuddy/shared-types';

/** LLM 调用抽象（注入 ModelGateway 或 mock-model-server） */
export type LlmCaller = (req: ChatStreamRequest) => AsyncIterable<Delta>;

/** 单次完整调用（Planner/Verifier 用，非流式） */
export type LlmCompleter = (req: ChatStreamRequest) => Promise<{ content: string }>;

/** Runtime 事件发射器（转 JSON-RPC 通知） */
export interface RuntimeEmitter {
  emit(event: RuntimeOutboundEvent): void;
}

export type RuntimeOutboundEvent =
  | { method: 'task/plan.ready'; params: { taskId: string; steps: PlanStep[] } }
  | { method: 'task/step.update'; params: { taskId: string; step: PlanStep } }
  | {
      method: 'task/token.delta';
      params: { taskId: string; text: string; usage?: { promptTokens: number; completionTokens: number } };
    }
  | { method: 'task/artifact.created'; params: { taskId: string; artifact: Artifact } }
  | { method: 'task/permission.request'; params: { taskId: string; request: PermissionRequest } }
  | { method: 'task/done'; params: { taskId: string; report: VerifyReport; artifacts: Artifact[] } }
  | { method: 'task/error'; params: { taskId: string; stepId?: string; cause: string } }
  | { method: 'task/rejected'; params: { taskId: string; reason: string } }
  | { method: 'task/cancelled'; params: { taskId: string; reason: string } }
  | { method: 'task/awaiting.input'; params: { taskId: string; question: string } }
  | { method: 'runtime/heartbeat'; params: { at: number } };

/** 权限请求的异步等待句柄（Executor 阻塞等待用户决定） */
export type PermissionWaiter = (
  request: PermissionRequest
) => Promise<'allow-once' | 'allow-always-task' | 'deny'>;

/** 步骤执行结果 */
export interface StepResult {
  stepId: string;
  ok: boolean;
  output: string;
  artifacts: Artifact[];
  error?: string;
}

/** Agent 循环运行时上下文 */
export interface LoopContext {
  task: Task;
  instruction: string;
  history: ChatMessage[];
  artifacts: Artifact[];
  steps: PlanStep[];
  replanCount: number;
  /** 每任务权限缓存：allow-always-task 的工具集合 */
  taskAllowedTools: Set<string>;
  /** 取消信号（Main 下发 cancel 后触发，循环与 LLM 流即时中断） */
  signal: AbortSignal;
}

export interface ExecutorDeps {
  llm: LlmCaller;
  emitter: RuntimeEmitter;
  permissionWaiter: PermissionWaiter;
  /** 取消信号注入：LLM 流与权限等待随 signal 中断 */
  signalFor?: (taskId: string) => AbortSignal;
  /** 向用户提问并等待回复（user.ask 工具；任务暂停直至用户跟进输入） */
  askWaiter?: (taskId: string, question: string) => Promise<string>;
  /** 联网检索后端（search.web 工具注入；缺省时该工具返回不可用） */
  webSearch?: (query: string) => Promise<string>;
  /** 工具注册表 */
  tools: import('../tools/registry.js').ToolRegistry;
  /** 权限策略（sandbox policyFor 产物） */
  policy: import('@wdbuddy/sandbox').SandboxPolicy;
  /** fs-guard 实例 */
  fsGuard: import('@wdbuddy/sandbox').FsGuard;
  /** artifact-store 备份回调（回滚零丢失，§12） */
  backupBeforeWrite: (taskId: string, path: string) => Promise<Artifact>;
  logger: import('../logger.js').Logger;
}

export interface PlannerDeps {
  complete: LlmCompleter;
  emitter: RuntimeEmitter;
  logger: import('../logger.js').Logger;
}
