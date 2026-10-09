/**
 * ModelGateway — 模型网关统一入口（§10.4）
 * chatStream(req): AsyncIterable<Delta>
 * 职责：Provider 注册解析 → Auto 路由 → 超时/退避/降级重试 → 统一流式输出
 */
import type {
  ChatCompletion,
  ChatStreamRequest,
  Delta,
  ModelId,
} from '@wdbuddy/shared-types';
import { ProviderRegistryImpl } from './types.js';
import { routeAuto, type RouteFeatures } from './router.js';
import { DEFAULT_RETRY_OPTIONS, withRetryStream, withTimeout, type RetryOptions } from './retry.js';
import { deepseekProvider } from './providers/deepseek.js';
import { zhipuGlmProvider } from './providers/zhipu.js';
import { kimiProvider } from './providers/kimi.js';
import { minimaxProvider } from './providers/minimax.js';
import { hunyuanProvider } from './providers/hunyuan.js';
import { createOpenAiCompatProvider } from './providers/openai-compat.js';

export interface GatewayOptions {
  /** API Key 解析器：modelId → key（由 desktop 主进程从 safeStorage 钥匙串注入） */
  resolveApiKey: (modelId: ModelId) => string;
  /** 模型名覆盖：modelId → apiModel（设置页"模型名"输入框，未填用 Provider 默认值） */
  apiModelOverrides?: Partial<Record<ModelId, string>>;
  retry?: Partial<RetryOptions>;
}

export class ModelGateway {
  private readonly registry = new ProviderRegistryImpl();
  private readonly retryOpts: RetryOptions;
  private readonly apiModelOverrides: Partial<Record<ModelId, string>>;

  constructor(private readonly opts: GatewayOptions) {
    // 内置 Provider 注册（ADR-0005：新增模型 = 1 个文件 + 此处注册）
    this.registry.register(deepseekProvider);
    this.registry.register(zhipuGlmProvider);
    this.registry.register(kimiProvider);
    this.registry.register(minimaxProvider);
    this.registry.register(hunyuanProvider);
    this.retryOpts = { ...DEFAULT_RETRY_OPTIONS, ...opts.retry };
    this.apiModelOverrides = opts.apiModelOverrides ?? {};
    this.applyApiModelOverrides();
  }

  /**
   * 模型名覆盖：以同 id 重新注册替换内置 Provider。
   * openai-compat 请求体的 model 字段取自 Provider 构造参数（而非 req.model），
   * 故覆盖必须通过重建 Provider 实现。
   */
  private applyApiModelOverrides(): void {
    for (const [modelId, apiModel] of Object.entries(this.apiModelOverrides)) {
      const trimmed = (apiModel ?? '').trim();
      if (!trimmed) continue;
      // 仅覆盖已注册的 Provider（未知 id 忽略，避免构造期抛错）
      if (!this.registry.has(modelId as ModelId)) continue;
      const base = this.registry.resolve(modelId as ModelId);
      this.registry.register(
        createOpenAiCompatProvider({
          id: base.id,
          displayName: base.displayName,
          baseUrl: base.baseUrl,
          apiModel: trimmed,
        })
      );
    }
  }

  /** 注册自定义 Provider（设置页自定义 API） */
  registerCustom(provider: Parameters<ProviderRegistryImpl['register']>[0]): void {
    this.registry.register(provider);
  }

  /** 解析实际模型：auto → 按特征路由；路由目标无密钥时切换到首个已配置模型 */
  resolveModel(req: ChatStreamRequest, features?: RouteFeatures): { model: ModelId; reason: string } {
    if (req.model !== 'auto') return { model: req.model, reason: '用户指定' };
    const routed = routeAuto(
      features ?? { mode: 'ask', longContext: false, codeRelated: false, research: false }
    );
    if (this.opts.resolveApiKey(routed.model)) return routed;
    // Auto 路由目标（如轻量问答→GLM）密钥未配置：按注册顺序切换到首个已配置密钥的
    // 模型，避免"只配置了 A 的 Key 却始终报 B 未配置"的困惑；全部未配置则维持原路由
    for (const p of this.registry.list()) {
      if (this.opts.resolveApiKey(p.id)) {
        return {
          model: p.id,
          reason: `${routed.reason}；${routed.model} 密钥未配置，已切换为 ${p.displayName}`,
        };
      }
    }
    return routed;
  }

  /** 统一流式对话：内部完成 Auto 路由 + 重试 + 降级链 */
  async *chatStream(req: ChatStreamRequest, features?: RouteFeatures): AsyncIterable<Delta> {
    const { model, reason } = this.resolveModel(req, features);
    if (!this.registry.has(model)) {
      throw new Error(`[model-gateway] 模型 ${model} 未注册`);
    }
    // 用户显式选择的模型禁用降级链：失败必须指向该模型（禁止静默换模型，
    // 否则"选 GLM 报 Kimi 的错"这类误导会重演）；Auto 路由保留降级容灾。
    const allowFallback = req.model === 'auto';
    yield* withRetryStream(
      (m, _attempt) => {
        const provider = this.registry.resolve(m);
        const key = this.opts.resolveApiKey(m);
        if (!key) {
          // Auto 模式下注明路由来源，避免"我选了 Auto 怎么报 DeepSeek"的困惑
          const autoNote = req.model === 'auto' ? `（Auto 模式路由：${reason}）` : '';
          throw new Error(
            `[model-gateway] ${provider.displayName} 的 API Key 未配置${autoNote}。请在 设置 → 模型 中填写并保存`
          );
        }
        return provider.chatStream(req, key);
      },
      model,
      { ...this.retryOpts, allowFallback }
    );
  }

  /** 非流式对话（Planner/Verifier 等一次性调用） */
  async chat(req: ChatStreamRequest, features?: RouteFeatures): Promise<ChatCompletion> {
    const { model, reason } = this.resolveModel(req, features);
    const provider = this.registry.resolve(model);
    const key = this.opts.resolveApiKey(model);
    if (!key) {
      const autoNote = req.model === 'auto' ? `（Auto 模式路由：${reason}）` : '';
      throw new Error(
        `[model-gateway] ${provider.displayName} 的 API Key 未配置${autoNote}。请在 设置 → 模型 中填写并保存`
      );
    }
    return withTimeout(provider.chat(req, key), this.retryOpts.timeoutMs, String(model), 1);
  }

  /** 已注册模型列表（ModelPicker 数据源） */
  listModels(): Array<{ id: ModelId; name: string }> {
    return this.registry.list().map((p) => ({ id: p.id, name: p.displayName }));
  }
}
