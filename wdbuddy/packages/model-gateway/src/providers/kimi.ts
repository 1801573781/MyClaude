import { createOpenAiCompatProvider } from './openai-compat.js';

/** Kimi Provider（Moonshot，OpenAI 兼容协议） */
export const kimiProvider = createOpenAiCompatProvider({
  id: 'kimi',
  displayName: 'Kimi',
  baseUrl: 'https://api.moonshot.cn/v1',
  apiModel: 'moonshot-v1-128k',
});
