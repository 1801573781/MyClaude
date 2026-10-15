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
import { createHash } from 'node:crypto';
import * as path from 'node:path';
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
import { randomId } from '../tools/registry.js';
import { mdToSlides } from '../tools/pptx/md-to-slides.js';
import { generateDeckOutputs } from '../tools/office.js';

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
      let steps: PlanStep[];
      if (hasPending) {
        steps = pending.map((s) => (s.status === 'running' ? { ...s, status: 'pending' as const } : s));
      } else {
        const outcome = await this.planner.makePlan(task, instruction);
        // 咨询/问答：调度器直答，透传给用户后收尾，不进执行循环
        //（修复：咨询被强拆成幻觉步骤、对不存在的图片空跑数分钟）
        if (outcome.kind === 'answer') {
          await this.answerAndFinish(task, outcome.reply);
          return;
        }
        steps = outcome.steps;
      }
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
          // 重规划耗尽：交付型任务（PPT 等）先尝试程序化兜底——步骤失败通常
          // 是 LLM 输出截断等执行层问题，内容稿可能已落盘，零 LLM 兜底转换
          // 仍可交付真成果（修复：此前直接 task/error，兜底代码永远不执行）
          const intentText = `${ctx.instruction}\n${ctx.history
            .filter((h) => h.role === 'user')
            .map((h) => h.content)
            .join('\n')}`;
          const gapEarly = this.verifier.intentGap(task, intentText, ctx.artifacts);
          if (gapEarly) {
            const fb = await this.programmaticDeckFallback(task, ctx, gapEarly.detail);
            if (fb) {
              const report = await this.verifier.selfCheck(task, ctx.artifacts);
              this.deps.checkpoint.clear(task.id);
              this.deps.emitter.emit({
                method: 'task/done',
                params: { taskId: task.id, report, artifacts: ctx.artifacts },
              });
              log.info('重规划耗尽但程序化兜底成功，真成果交付', { artifacts: ctx.artifacts.length });
              return;
            }
          }
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

    // 自检 + 交付（意图-产物缺口 → 自动补救一步；补救后仍缺 → task/error，禁止假成功）
    const report = await this.verifier.selfCheck(task, ctx.artifacts);
    // 意图识别文本：指令 + 全部用户消息（跟进场景下 instruction 可能只是补充回答，
    // 原始"生成PPT"诉求在历史里，必须合并检测，否则补救机制漏触发）
    const intentText = `${ctx.instruction}\n${ctx.history
      .filter((h) => h.role === 'user')
      .map((h) => h.content)
      .join('\n')}`;
    const gap = this.verifier.intentGap(task, intentText, ctx.artifacts);
    if (gap) {
      log.warn('自检发现意图-产物缺口，构造补救步骤自动补执行', { gap: gap.title });
      const remedyStep: PlanStep = {
        id: `step-${randomId()}`,
        seq: ctx.steps.length + 1,
        title: `补救：${gap.title}`,
        status: 'running',
        artifactIds: [],
        startedAt: Date.now(),
      };
      ctx.steps.push(remedyStep);
      this.deps.emitter.emit({ method: 'task/step.update', params: { taskId: task.id, step: remedyStep } });
      ctx.history.push({
        role: 'user',
        content: `[系统反馈] 交付前自检发现：${gap.detail}。请立即真正调用 office.generate 工具生成幻灯片文件（.pptx 或 .html）：内容务必精简（每页要点不超过 4 条、每条不超过 20 字）防止输出被截断；严禁只写设计稿文档、严禁不调用工具、严禁谎报成功。`,
      });
      try {
        const result = await this.executor.execute(remedyStep, ctx);
        remedyStep.endedAt = Date.now();
        remedyStep.status = result.ok ? 'done' : 'failed';
        remedyStep.summary = result.output.slice(0, 200);
        remedyStep.error = result.ok ? undefined : (result.error ?? result.output.slice(0, 500));
        remedyStep.artifactIds.push(...result.artifacts.map((a) => a.id));
      } catch (err) {
        remedyStep.endedAt = Date.now();
        remedyStep.status = 'failed';
        remedyStep.error = err instanceof Error ? err.message : String(err);
        remedyStep.summary = `失败：${remedyStep.error}`;
      }
      this.deps.emitter.emit({ method: 'task/step.update', params: { taskId: task.id, step: remedyStep } });
      // LLM 补救失败 → 程序化兜底：从已落盘产物中找 md 内容稿，用确定性代码
      // （零 LLM，不可能再截断/失败）转成 HTML 幻灯片——把"真失败"兜成"真成功"
      if (this.verifier.intentGap(task, intentText, ctx.artifacts)) {
        const fallback = await this.programmaticDeckFallback(task, ctx, gap.detail);
        if (fallback) {
          const reportAfter = await this.verifier.selfCheck(task, ctx.artifacts);
          this.deps.checkpoint.clear(task.id);
          this.deps.emitter.emit({
            method: 'task/done',
            params: { taskId: task.id, report: reportAfter, artifacts: ctx.artifacts },
          });
          log.info('Agent 循环结束（程序化兜底交付）', { artifacts: ctx.artifacts.length });
          return;
        }
        log.warn('程序化兜底不可用（无 md 内容稿），任务判失败', { taskId: task.id });
        this.deps.emitter.emit({
          method: 'task/error',
          params: {
            taskId: task.id,
            stepId: remedyStep.id,
            cause: `交付自检未通过：${gap.detail}；自动补救与程序化兜底均未生成幻灯片文件，任务标记为失败。请在对话中跟进（如"继续生成PPT文件"）重试。`,
          },
        });
        return;
      }
      // 补救成功：以复检报告交付
      const reportAfter = await this.verifier.selfCheck(task, ctx.artifacts);
      this.deps.checkpoint.clear(task.id);
      this.deps.emitter.emit({
        method: 'task/done',
        params: { taskId: task.id, report: reportAfter, artifacts: ctx.artifacts },
      });
      log.info('Agent 循环结束（补救后交付）', { artifacts: ctx.artifacts.length });
      return;
    }
    this.deps.checkpoint.clear(task.id);
    this.deps.emitter.emit({
      method: 'task/done',
      params: { taskId: task.id, report, artifacts: ctx.artifacts },
    });
    log.info('Agent 循环结束', { passed: report.passed, artifacts: ctx.artifacts.length });
  }

  /** 咨询/问答直答收尾：回答经 token.delta 流式透传给用户，task/done 正常
   *  结束（无文件产物，自检按问答类任务放行）。调度器判定为咨询时不进执行
   *  循环——修复"能否转成PPT模版"类问句被强拆成"分析图片风格"幻觉步骤、
   *  对不存在的图片空跑数分钟的事故。 */
  private async answerAndFinish(task: Task, reply: string): Promise<void> {
    const log = this.deps.logger.child({ taskId: task.id });
    this.deps.emitter.emit({ method: 'task/token.delta', params: { taskId: task.id, text: reply } });
    const report = await this.verifier.selfCheck(task, []);
    this.deps.emitter.emit({
      method: 'task/done',
      params: { taskId: task.id, report, artifacts: [] },
    });
    log.info('咨询直答收尾，未进入执行循环');
  }

  /** 程序化兜底（零 LLM）：md 内容稿 → 幻灯片（.html + .pptx 双格式）。
   *  LLM 补救失败时，用确定性代码把已写好的内容稿转换成幻灯片文件，
   *  复用 office.ts 的双引擎共享函数。写失败返回 false 由上层判失败。 */
  private async programmaticDeckFallback(
    task: Task,
    ctx: LoopContext,
    gapDetail: string
  ): Promise<boolean> {
    const log = this.deps.logger.child({ taskId: task.id });
    // 候选内容稿：① 本任务产物 md（最可信，含历史多版本取最后写入者）
    // ② 工作空间中名字含 ppt/幻灯/内容/slides 的最新 md（LLM 曾 fs.read
    //    复用旧稿但未登记产物、或刚写入就失败的场景；466 事故根因之一）
    const mdArts = ctx.artifacts.filter(
      (a) => a.path !== undefined && /\.(md|markdown)$/i.test(a.path)
    );
    let mdPath = mdArts.length > 0 ? (mdArts[mdArts.length - 1]!.path as string) : '';
    if (!mdPath) {
      try {
        const roots = task.workspaceUri ? [task.workspaceUri] : [];
        let best: { p: string; m: number } | null = null;
        for (const root of roots) {
          const entries = this.deps.fsGuard.listDir(root);
          for (const e of entries) {
            if (!/\.(md|markdown)$/i.test(e)) continue;
            if (!/(ppt|幻灯|内容|slides|讲稿|文案)/i.test(e)) continue;
            const full = `${root.replace(/[\/\\]+$/, '')}${path.sep}${e}`;
            try {
              const st = this.deps.fsGuard.stat(full);
              if (st.isFile() && (!best || st.mtimeMs > best.m)) best = { p: full, m: st.mtimeMs };
            } catch {
              /* 跳过不可 stat 的项 */
            }
          }
        }
        if (best) mdPath = best.p;
      } catch {
        /* 工作空间不可列，跳过扫描 */
      }
    }
    if (!mdPath) return false;
    let md: string;
    try {
      md = this.deps.fsGuard.readFile(mdPath);
    } catch {
      return false;
    }
    try {
      const title = task.title || 'WDBuddy 演示文稿';
      const slides = mdToSlides(md, title);
      const base = mdPath.replace(/\.(md|markdown)$/i, '');
      const outputs = await generateDeckOutputs(base, title, 'auto', slides, (fp, data) =>
        this.deps.fsGuard.writeFile(fp, data)
      );
      for (const o of outputs) {
        const art: Artifact = {
          id: `art-${randomId()}`,
          taskId: task.id,
          kind: 'file',
          path: o.path,
          mimeType: o.mime,
          version: 1,
          sha256: createHash('sha256').update(o.buf).digest('hex'),
          createdAt: Date.now(),
        };
        ctx.artifacts.push(art);
        this.deps.emitter.emit({
          method: 'task/artifact.created',
          params: { taskId: task.id, artifact: art },
        });
      }
      log.info('程序化兜底已生成幻灯片（双格式）', { base, pages: slides.length, note: `（LLM 补救失败，由系统从内容稿 ${mdPath} 自动转换；缺口原因：${gapDetail}）` });
      return true;
    } catch (err) {
      log.warn('程序化兜底转换失败', { err: err instanceof Error ? err.message : String(err) });
      return false;
    }
  }
}

export type { VerifyReport, Artifact };
