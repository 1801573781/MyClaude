import { createOpenAiCompatProvider } from './openai-compat.js';

/** 智谱 GLM Provider（OpenAI 兼容协议） */
export const zhipuGlmProvider = createOpenAiCompatProvider({
  id: 'zhipu-glm',
  displayName: '智谱 GLM',
  baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
  apiModel: 'glm-4-plus',
});
