/**
 * Planner（§10.3）
 * 任务调度器：先分流，后拆解。咨询/问答类输入（问能力、问原理、材料未附上）
 * 直接产出 chat 回复，由循环层透传给用户后收尾，不进执行循环（修复：咨询
 * 被强拆成"分析图片风格"等幻觉步骤、对不存在的图片空跑数分钟）。
 * 任务拆解产出 PlanStep[]；Plan 模式先 emit task/plan.ready 等待审批。
 * 输出格式约定：咨询直答 {"type":"chat","reply":"..."}；任务步骤 [{"title":"..."}]；
 * 解析失败时回退为单步计划（保持旧行为，不误伤执行类任务）。
 */
import type { PlanStep, Task } from '@wdbuddy/shared-types';
import type { PlannerDeps } from './types.js';
import { randomId } from '../tools/registry.js';

const PLANNER_SYSTEM_PROMPT = `你是 WDBuddy 的任务调度器。先判断用户输入属于哪种情况，再严格按对应格式输出：

情况一（咨询/问答，直接回答，不拆步骤）：
用户在询问能力、原理、方法，或只是聊天确认，并未下达立即执行的指令。
典型特征：疑问句式（能不能、可以吗、怎么做、是什么、支持吗）；或想做某事但必要材料未提供（如提到图片、文件却未附上）。
此时严禁编造任何执行步骤，严禁分析不存在的材料。只输出：
{"type": "chat", "reply": "给用户的完整回答：友好、具体；若用户想做某事但材料未提供，说明可以做什么并请对方提供材料"}

情况二（可执行任务，拆解步骤）：
用户明确下达执行指令且材料具备（如根据 474.txt 生成PPT、把这些照片整理成相册网页）。
拆解为 2~6 个可执行步骤，只输出 JSON 数组：
[{"title": "步骤标题（不超过20字）"}]

只输出以上两种 JSON 之一，不要输出其他任何文字。`;

/** 规划结果：任务步骤 或 咨询直答（循环层据此分流） */
export type PlanOutcome =
  | { kind: 'steps'; steps: PlanStep[] }
  | { kind: 'answer'; reply: string };

export class Planner {
  constructor(private readonly deps: PlannerDeps) {}

  /** 调度：先咨询分流，后任务拆解。LLM 调用失败回退单步（不误判为咨询）。 */
  async makePlan(task: Task, instruction: string): Promise<PlanOutcome> {
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
      return { kind: 'steps', steps: toSteps(['执行用户指令']) };
    }

    const chat = extractChatReply(raw);
    if (chat !== null) {
      this.deps.logger.info('调度判定：咨询/问答，直接回答（不进执行循环）', { taskId: task.id });
      return { kind: 'answer', reply: chat };
    }
    return { kind: 'steps', steps: toSteps(parsePlanTitles(raw)) };
  }
}

function toSteps(titles: string[]): PlanStep[] {
  return titles.map((title, i) => ({
    id: `step-${randomId()}`,
    seq: i + 1,
    title,
    status: 'pending' as const,
    artifactIds: [],
  }));
}

/** 提取咨询直答对象 {"type":"chat","reply":"..."}。
 *  含 chat 标记才尝试；从 "type" 前最近的 "{" 起向后逐个 "}" 试解析，
 *  容错 reply 内含花括号/引号/换行；type=chat 但 reply 缺失时给兜底话术
 *  （已判定为咨询就不许回落到拆步骤，防止幻觉步骤复发）。 */
export function extractChatReply(raw: string): string | null {
  if (!raw || !/"type"\s*:\s*"chat"/.test(raw)) return null;
  const typeIdx = raw.indexOf('"type"');
  if (typeIdx < 0) return null;
  const start = raw.lastIndexOf('{', typeIdx);
  if (start < 0) return null;
  let end = raw.indexOf('}', typeIdx);
  while (end > 0) {
    try {
      const obj = JSON.parse(raw.slice(start, end + 1)) as { type?: unknown; reply?: unknown };
      if (obj['type'] === 'chat') {
        const reply = typeof obj['reply'] === 'string' ? (obj['reply'] as string).trim() : '';
        return reply.length > 0 ? reply : '好的，已收到您的消息。请提供更多细节或材料，我来为您处理。';
      }
      return null;
    } catch {
      end = raw.indexOf('}', end + 1);
    }
  }
  return null;
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
