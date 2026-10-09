import { createOpenAiCompatProvider } from './openai-compat.js';

/** MiniMax Provider（OpenAI 兼容协议） */
export const minimaxProvider = createOpenAiCompatProvider({
  id: 'minimax',
  displayName: 'MiniMax',
  baseUrl: 'https://api.minimax.chat/v1',
  apiModel: 'abab6.5s-chat',
});
