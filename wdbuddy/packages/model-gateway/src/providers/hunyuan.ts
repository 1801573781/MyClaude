import { createOpenAiCompatProvider } from './openai-compat.js';

/** 腾讯混元 Provider（OpenAI 兼容协议） */
export const hunyuanProvider = createOpenAiCompatProvider({
  id: 'hunyuan',
  displayName: '混元',
  baseUrl: 'https://api.hunyuan.cloud.tencent.com/v1',
  apiModel: 'hunyuan-turbo',
});
