/**
 * Planner（§10.3）
 * 产出 PlanStep[]；Plan 模式先 emit task/plan.ready 等待审批。
 * 输出格式约定：LLM 返回 JSON 数组 [{"title": "..."}]，解析失败时回退为单步计划。
 */
import type { PlanStep, Task } from '@wdbuddy/shared-types';
import type { PlannerDeps } from './types.js';
import { randomId } from '../tools/registry.js';

const PLANNER_SYSTEM_PROMPT = `你是 WDBuddy 的任务规划器。将用户的任务指令拆解为 2~6 个可执行步骤。
只输出 JSON 数组，每项形如 {"title": "步骤标题（不超过20字）"}，不要输出其他任何文字。`;

export class Planner {
  constructor(private readonly deps: PlannerDeps) {}

  async makePlan(task: Task, instruction: string): Promise<PlanStep[]> {
    let raw = '';
    try {
      const res = await this.deps.complete({
        model: task.modelId,
        messages: [
          { role: 'system', content: PLANNER_SYSTEM_PROMPT },
          {
            role: 'user',
            content: `任务模式：${task.mode}\n工作空间：${task.workspaceUri ?? '（未指定）'}\n指令：${instruction}`,
          },
        ],
      });
      raw = res.content;
    } catch (err) {
      this.deps.logger.warn('Planner LLM 调用失败，回退单步计划', { err: String(err) });
    }

    const steps = parsePlanTitles(raw);
    return steps.map((title, i) => ({
      id: `step-${randomId()}`,
      seq: i + 1,
      title,
      status: 'pending' as const,
      artifactIds: [],
    }));
  }
}

/** 解析 LLM 输出中的步骤标题；失败回退单步 */
export function parsePlanTitles(raw: string): string[] {
  if (!raw) return ['执行用户指令'];
  // 提取第一个 JSON 数组
  const match = raw.match(/\[[\s\S]*\]/);
  if (match) {
    try {
      const arr = JSON.parse(match[0]) as Array<{ title?: unknown }>;
      const titles = arr
        .map((it) => (typeof it?.['title'] === 'string' ? (it['title'] as string).trim() : ''))
        .filter((t) => t.length > 0);
      if (titles.length > 0) return titles.slice(0, 6);
    } catch {
      /* fallthrough */
    }
  }
  // 按行回退解析
  const lines = raw
    .split('\n')
    .map((l) => l.replace(/^\s*(\d+[.、)]|[·•\-\*])\s*/, '').trim())
    .filter((l) => l.length >= 2 && l.length <= 40);
  return lines.length > 0 ? lines.slice(0, 6) : ['执行用户指令'];
}
