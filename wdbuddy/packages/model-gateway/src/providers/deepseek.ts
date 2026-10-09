import { createOpenAiCompatProvider } from './openai-compat.js';

/** DeepSeek Provider（OpenAI 兼容协议） */
export const deepseekProvider = createOpenAiCompatProvider({
  id: 'deepseek',
  displayName: 'DeepSeek',
  baseUrl: 'https://api.deepseek.com/v1',
  apiModel: 'deepseek-chat',
});
