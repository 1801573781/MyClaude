/**
 * Agent 循环核心（§10.3，保持此结构以便测试替身注入）
 *
 * while (hasPendingSteps(task)) {
 *   step = nextStep; emit running;
 *   result = await executor.execute(step, { tools: sandbox.policyFor(...), model: gateway.chatStream });
 *   checkpoint.save(task, step, result);
 *   emit done;
 * } catch → replanner.replan（上限 3）→ 回到执行
 * 最后 verifier.selfCheck → emit task/done
 */
import type { Artifact, PlanStep, Task, VerifyReport } from '@wdbuddy/shared-types';
import type { SandboxPolicy, FsGuard } from '@wdbuddy/sandbox';
import type { ToolRegistry } from '../tools/registry.js';
import type { RuntimeEmitter, LlmCaller, PermissionWaiter, LoopContext } from './types.js';
import { Planner } from './planner.js';
import { Executor } from './executor.js';
import { Replanner } from './replanner.js';
import { Verifier } from './verifier.js';
import type { CheckpointStore } from '../checkpoint/store.js';
import type { Logger } from '../logger.js';
import { isAuthError } from '@wdbuddy/model-gateway';
import { TaskCancelledError } from './executor.js';

export interface AgentLoopDeps {
  emitter: RuntimeEmitter;
  llm: LlmCaller;
  complete: import('./types.js').LlmCompleter;
  permissionWaiter: PermissionWaiter;
  /** Plan 审批等待：emit plan.ready 后阻塞，直到 Main 转发 approve/reject（修复未批准即执行） */
  approvalWaiter: (taskId: string) => Promise<PlanApproval>;
  /** 取消信号源（Main 下发 cancel 后 abort，循环/LLM 流/权限等待即时中断） */
  signalFor: (taskId: string) => AbortSignal;
  /** 向用户提问并等待回复（user.ask 工具） */
  askWaiter?: (taskId: string, question: string) => Promise<string>;
  /** 联网检索后端（search.web 工具注入） */
  webSearch?: (query: string) => Promise<string>;
  tools: ToolRegistry;
  policy: SandboxPolicy;
  fsGuard: FsGuard;
  checkpoint: CheckpointStore;
  logger: Logger;
}

/** Plan 审批结果（approve 可携带用户编辑后的步骤） */
export interface PlanApproval {
  decision: 'approve' | 'reject';
  steps?: PlanStep[];
}

export class AgentLoop {
  private readonly planner: Planner;
  private readonly executor: Executor;
  private readonly replanner: Replanner;
  private readonly verifier: Verifier;

  constructor(private readonly deps: AgentLoopDeps) {
    this.planner = new Planner({ complete: deps.complete, emitter: deps.emitter, logger: deps.logger });
    this.executor = new Executor({
      llm: deps.llm,
      emitter: deps.emitter,
      permissionWaiter: deps.permissionWaiter,
      signalFor: deps.signalFor,
      askWaiter: deps.askWaiter,
      webSearch: deps.webSearch,
      tools: deps.tools,
      policy: deps.policy,
      fsGuard: deps.fsGuard,
      backupBeforeWrite: async (taskId, p) => {
        // 占位：实际备份由 fs.write 工具内完成；此处满足接口
        throw new Error(`backupBeforeWrite 未实现（${taskId} ${p}）`);
      },
      logger: deps.logger,
    });
    this.replanner = new Replanner({ complete: deps.complete, emitter: deps.emitter, logger: deps.logger });
    this.verifier = new Verifier(deps.fsGuard, deps.logger);
  }

  /** 是否有待执行步骤 */
  static hasPendingSteps(ctx: LoopContext): boolean {
    return ctx.steps.some((s) => s.status === 'pending' || s.status === 'running');
  }

  static nextStep(ctx: LoopContext): PlanStep | null {
    return ctx.steps.find((s) => s.status === 'pending') ?? ctx.steps.find((s) => s.status === 'running') ?? null;
  }

  /** 从检查点恢复上下文 */
  restoreFromCheckpoint(task: Task, instruction: string): LoopContext | null {
    const snap = this.deps.checkpoint.loadLatest(task.id);
    if (!snap) return null;
    const ctx: LoopContext = {
      task,
      instruction,
      history: snap.history,
      artifacts: [],
      steps: snap.steps,
      replanCount: snap.replanCount,
      taskAllowedTools: new Set<string>(),
      signal: this.deps.signalFor(task.id),
    };
    return ctx;
  }

  async run(
    task: Task,
    instruction: string,
    carry?: { history?: import('@wdbuddy/shared-types').ChatMessage[]; pendingSteps?: PlanStep[] }
  ): Promise<void> {
    const log = this.deps.logger.child({ taskId: task.id });
    log.info('Agent 循环启动', { mode: task.mode });

    // 恢复或新建上下文（信号由入口注入 LoopDeps，见 index.ts）
    let ctx = this.restoreFromCheckpoint(task, instruction);
    if (!ctx) {
      // 跟进续跑：上轮存在未完成步骤则直接继续（running 复位为 pending），
      // 否则重新规划。对话历史优先用 Main 传入的完整历史（修复：跟进只带
      // 单条指令导致上下文丢失、任务"从头再来一遍"）
      const pending = carry?.pendingSteps ?? [];
      const hasPending = pending.some((s) => s.status === 'pending' || s.status === 'running');
      const steps = hasPending
        ? pending.map((s) => (s.status === 'running' ? { ...s, status: 'pending' as const } : s))
        : await this.planner.makePlan(task, instruction);
      ctx = {
        task,
        instruction,
        history:
          carry?.history && carry.history.length > 0
            ? carry.history
            : [{ role: 'user', content: instruction }],
        artifacts: [],
        steps,
        replanCount: 0,
        taskAllowedTools: new Set<string>(),
        signal: this.deps.signalFor(task.id),
      };
    }

    // Plan 模式：先出计划，阻塞等待用户审批（修复：未点击批准就开始执行）
    if (task.mode === 'plan') {
      this.deps.emitter.emit({
        method: 'task/plan.ready',
        params: { taskId: task.id, steps: ctx.steps },
      });
      const approval = await this.deps.approvalWaiter(task.id);
      if (approval.decision === 'reject') {
        // 拒绝：通知 Main/渲染层复位 UI（任务回草稿），本次循环终止
        log.info('用户拒绝计划，任务终止');
        this.deps.emitter.emit({
          method: 'task/rejected',
          params: { taskId: task.id, reason: '用户拒绝计划' },
        });
        return;
      }
      // 批准：若带回编辑后的步骤（PlanCard 修改过），替换执行计划
      if (approval.steps && approval.steps.length > 0) {
        ctx.steps = approval.steps.map((s) => ({
          ...s,
          status: 'pending' as const,
          artifactIds: Array.isArray(s.artifactIds) ? s.artifactIds : [],
        }));
      }
    }

    // 主循环（每步前检查取消：保证步骤间隙也能即时停止）
    while (AgentLoop.hasPendingSteps(ctx)) {
      if (ctx.signal.aborted) {
        log.info('任务已取消，退出循环', { taskId: task.id });
        this.deps.emitter.emit({
          method: 'task/cancelled',
          params: { taskId: task.id, reason: '用户取消' },
        });
        return;
      }
      const step = AgentLoop.nextStep(ctx);
      if (!step) break;

      step.status = 'running';
      step.startedAt = Date.now();
      this.deps.emitter.emit({ method: 'task/step.update', params: { taskId: task.id, step } });

      try {
        const result = await this.executor.execute(step, ctx);
        step.endedAt = Date.now();
        step.status = result.ok ? 'done' : 'failed';
        step.summary = result.output.slice(0, 200);
        step.error = result.ok ? undefined : (result.error ?? result.output.slice(0, 500));
        step.artifactIds.push(...result.artifacts.map((a) => a.id));
        this.deps.emitter.emit({ method: 'task/step.update', params: { taskId: task.id, step } });

        if (!result.ok) throw new Error(result.error ?? '步骤失败');

        // 步骤级检查点
        this.deps.checkpoint.save(task, step.id, {
          steps: ctx.steps,
          history: ctx.history,
          replanCount: ctx.replanCount,
          artifactIds: ctx.artifacts.map((a) => a.id),
        });
      } catch (err) {
        const cause = err instanceof Error ? err.message : String(err);
        step.endedAt = Date.now();
        step.status = 'failed';
        step.error = cause;
        // 失败摘要也带上原因：时间线折叠态与展开态均可见（修复"无详细日志"）
        step.summary = `失败：${cause}`;
        this.deps.emitter.emit({ method: 'task/step.update', params: { taskId: task.id, step } });

        // 用户取消：不进入重规划，直接收尾（外部已把状态落为 cancelled）
        if (err instanceof TaskCancelledError) {
          log.info('任务已取消，停止执行', { stepId: step.id });
          this.deps.emitter.emit({
            method: 'task/cancelled',
            params: { taskId: task.id, reason: '用户取消' },
          });
          return;
        }

        // 配置/认证类错误（如 API Key 未配置、401）：重规划无意义（新计划仍需
        // 调用同一 LLM），直接终止任务并给出明确指引，避免空转 3 次误导用户
        if (isAuthError(err)) {
          log.warn('配置/认证类错误，跳过重规划直接终止', { stepId: step.id, cause });
          this.deps.emitter.emit({
            method: 'task/error',
            params: { taskId: task.id, stepId: step.id, cause },
          });
          return;
        }

        log.warn('步骤失败，尝试重规划', { stepId: step.id, cause });

        const outcome = await this.replanner.replan(task, step, err, ctx.steps, ctx.replanCount);
        if (!outcome.replanned) {
          this.deps.emitter.emit({
            method: 'task/error',
            params: { taskId: task.id, stepId: step.id, cause: outcome.reason },
          });
          return;
        }
        ctx.steps = outcome.steps;
        ctx.replanCount += 1;
        // 继续循环（回到执行）
      }
    }

    // 自检 + 交付
    const report = await this.verifier.selfCheck(task, ctx.artifacts);
    this.deps.checkpoint.clear(task.id);
    this.deps.emitter.emit({
      method: 'task/done',
      params: { taskId: task.id, report, artifacts: ctx.artifacts },
    });
    log.info('Agent 循环结束', { passed: report.passed, artifacts: ctx.artifacts.length });
  }
}

export type { VerifyReport, Artifact };
