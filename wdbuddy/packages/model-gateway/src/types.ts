/**
 * model-gateway 内部类型（对外类型见 shared-types）
 * ADR-0005：所有 Provider 统一为 OpenAI 兼容协议抽象。
 */
import type { ChatStreamRequest, ChatCompletion, Delta, ModelId } from '@wdbuddy/shared-types';

/** Provider 适配器接口：新增模型 = 实现 1 个文件 + 注册 */
export interface ModelProvider {
  readonly id: ModelId;
  readonly displayName: string;
  /** OpenAI 兼容 base URL（custom provider 必填） */
  readonly baseUrl: string;
  /** 模型名（发往 API 的 model 字段） */
  readonly apiModel: string;
  /** API Key 引用（不直接持有明文，由调用方注入） */
  chatStream(req: ChatStreamRequest, apiKey: string): AsyncIterable<Delta>;
  chat(req: ChatStreamRequest, apiKey: string): Promise<ChatCompletion>;
}

export interface ProviderRegistry {
  register(provider: ModelProvider): void;
  resolve(modelId: ModelId): ModelProvider;
  has(modelId: ModelId): boolean;
  list(): ModelProvider[];
}

/** 流中断可续：网关重试所需的恢复上下文 */
export interface ResumableStreamState {
  requestId: string;
  modelId: ModelId;
  receivedText: string;
  attempt: number;
}

export class ProviderNotFoundError extends Error {
  constructor(modelId: string) {
    super(`[model-gateway] 未找到模型 Provider：${modelId}，请先注册`);
    this.name = 'ProviderNotFoundError';
  }
}

export class ProviderRegistryImpl implements ProviderRegistry {
  private readonly providers = new Map<string, ModelProvider>();

  register(provider: ModelProvider): void {
    this.providers.set(provider.id, provider);
  }

  resolve(modelId: ModelId): ModelProvider {
    const p = this.providers.get(modelId);
    if (!p) throw new ProviderNotFoundError(modelId);
    return p;
  }

  has(modelId: ModelId): boolean {
    return this.providers.has(modelId);
  }

  list(): ModelProvider[] {
    return [...this.providers.values()];
  }
}
