/**
 * 重试与降级链（§10.4）
 * - 超时 + 指数退避重试
 * - Provider 降级链：主模型失败 → 依次尝试备选
 * - 流中断可续：基于已接收文本续写
 */
import type { ChatStreamRequest, Delta, ModelId } from '@wdbuddy/shared-types';

export interface RetryOptions {
  /** 首次超时毫秒（后续按退避系数递增） */
  timeoutMs: number;
  /** 最大重试次数 */
  maxRetries: number;
  /** 退避基数毫秒 */
  backoffBaseMs: number;
  /** 降级链（按顺序尝试） */
  fallbackChain: readonly ModelId[];
  /** 是否允许模型降级（默认 true）。用户显式指定模型时应为 false：失败须指向该模型，禁止静默换模型 */
  allowFallback?: boolean;
}

export const DEFAULT_RETRY_OPTIONS: RetryOptions = {
  timeoutMs: 30000,
  maxRetries: 3,
  backoffBaseMs: 1000,
  fallbackChain: ['deepseek', 'zhipu-glm', 'kimi'],
};

/** 认证/配置类错误：换模型无济于事（Key 问题），不应触发降级 */
export function isAuthError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /HTTP 40[13]|invalid_authentication|unauthorized|api key|authentication/i.test(msg);
}

export class TimeoutError extends Error {
  constructor(model: string, attempt: number) {
    super(`[model-gateway] ${model} 第 ${attempt} 次调用超时`);
    this.name = 'TimeoutError';
  }
}

export function backoffDelay(attempt: number, baseMs = DEFAULT_RETRY_OPTIONS.backoffBaseMs): number {
  return baseMs * Math.pow(2, Math.max(0, attempt - 1));
}

/** 判断错误是否可重试（网络类/5xx/超时） */
export function isRetryable(err: unknown): boolean {
  if (err instanceof TimeoutError) return true;
  const msg = err instanceof Error ? err.message : String(err);
  return /HTTP 5\d\d|fetch failed|ECONNRESET|ENOTFOUND|network|aborted/i.test(msg);
}

/**
 * 带重试的流式调用封装。
 * callOnce: 单次流式调用，返回 Delta 异步迭代器
 * onDelta:  已接收的增量回调（用于流中断续写时累积）
 */
export async function* withRetryStream(
  callOnce: (modelId: ModelId, attempt: number) => AsyncIterable<Delta>,
  primaryModel: ModelId,
  opts: RetryOptions = DEFAULT_RETRY_OPTIONS
): AsyncIterable<Delta> {
  const allowFallback = opts.allowFallback ?? true;
  const chain: ModelId[] = allowFallback
    ? [primaryModel, ...opts.fallbackChain.filter((m) => m !== primaryModel)]
    : [primaryModel];
  let lastError: unknown = new Error('[model-gateway] 未产生任何调用');
  /** 聚合降级链各模型的失败明细（避免只暴露最后一环、误导诊断） */
  const failures: string[] = [];

  for (const model of chain) {
    for (let attempt = 1; attempt <= opts.maxRetries; attempt++) {
      try {
        const stream = callOnce(model, attempt);
        // 消费流；流中途异常向上抛，由外层退避重试
        for await (const delta of stream) {
          yield delta;
        }
        return; // 成功消费完毕
      } catch (err) {
        lastError = err;
        failures.push(err instanceof Error ? err.message : String(err));
        // 认证/配置类错误：重试与降级都无意义，直接终止（Auto 容灾也不换）
        if (isAuthError(err)) break;
        if (!isRetryable(err) || attempt >= opts.maxRetries) break;
        await sleep(backoffDelay(attempt, opts.backoffBaseMs));
      }
    }
    // 当前模型耗尽，尝试降级链下一个
  }
  if (failures.length > 1) {
    throw new Error(
      `[model-gateway] 主模型与降级链全部失败（尝试 ${failures.length} 次）：\n- ${failures.join('\n- ')}`
    );
  }
  throw lastError;
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** 带超时的单次调用包装 */
export async function withTimeout<T>(p: Promise<T>, timeoutMs: number, model: string, attempt: number): Promise<T> {
  return await Promise.race([
    p,
    new Promise<never>((_, reject) => setTimeout(() => reject(new TimeoutError(model, attempt)), timeoutMs)),
  ]);
}

export { ChatStreamRequest };
