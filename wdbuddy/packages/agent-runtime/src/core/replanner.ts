/**
 * Replanner（§10.3）
 * 失败 → 重规划（上限 3 次）→ 回到执行；超限返回 null 终止任务。
 */
import type { PlanStep } from '@wdbuddy/shared-types';
import type { PlannerDeps } from './types.js';
import { parsePlanTitles } from './planner.js';
import { randomId } from '../tools/registry.js';

export const MAX_REPLAN_COUNT = 3;

export interface ReplanOutcome {
  replanned: boolean;
  steps: PlanStep[];
  reason: string;
}

export class Replanner {
  constructor(private readonly deps: PlannerDeps) {}

  async replan(
    task: import('@wdbuddy/shared-types').Task,
    failedStep: PlanStep,
    error: unknown,
    currentSteps: PlanStep[],
    replanCount: number
  ): Promise<ReplanOutcome> {
    const cause = error instanceof Error ? error.message : String(error);
    if (replanCount >= MAX_REPLAN_COUNT) {
      return {
        replanned: false,
        steps: currentSteps,
        reason: `重规划次数已达上限（${MAX_REPLAN_COUNT}），任务终止；最后失败原因：${cause}`,
      };
    }
    let raw = '';
    try {
      const res = await this.deps.complete({
        model: task.modelId,
        messages: [
          {
            role: 'system',
            content:
              '你是任务重规划器。原计划执行失败，请输出调整后的新计划。只输出 JSON 数组 [{"title": "..."}]，2~6 步。',
          },
          {
            role: 'user',
            content: `原计划：${currentSteps.map((s) => `${s.seq}. ${s.title}[${s.status}]`).join('；')}
失败步骤：${failedStep.seq}. ${failedStep.title}
失败原因：${cause}
请调整后续步骤，避免同类失败。`,
          },
        ],
      });
      raw = res.content;
    } catch (err) {
      this.deps.logger.warn('Replanner LLM 调用失败，回退简单跳过策略', { err: String(err) });
    }

    const titles = parsePlanTitles(raw);
    const remaining = currentSteps.filter((s) => s.status === 'pending' || s.status === 'running');
    // 新计划：失败步骤之后的 pending 保留语义，用新标题重排
    const steps: PlanStep[] = titles.map((title, i) => ({
      id: `step-${randomId()}`,
      seq: i + 1,
      title,
      status: 'pending' as const,
      artifactIds: [],
    }));
    if (steps.length === 0) {
      // 兜底：跳过失败步骤继续
      for (const s of remaining) s.status = 'skipped';
      return { replanned: true, steps: currentSteps, reason: '回退为跳过失败步骤' };
    }
    return { replanned: true, steps, reason: `第 ${replanCount + 1} 次重规划成功` };
  }
}
