import { createOpenAiCompatProvider } from './openai-compat.js';
import type { CustomProvider } from '@wdbuddy/shared-types';

/**
 * 自定义 Provider 工厂。
 * 用户在设置页录入 base URL + API Key（存系统钥匙串），协议必须为 openai-compatible。
 */
export function createCustomProvider(def: CustomProvider): ReturnType<typeof createOpenAiCompatProvider> {
  return createOpenAiCompatProvider({
    id: def.id as `custom:${string}`,
    displayName: def.name,
    baseUrl: def.baseUrl,
    apiModel: def.apiModel,
  });
}
