/**
 * 上下文管理（§9 context/）
 * - 偏好注入：用户偏好（职业/风格/常用格式）注入后续任务上下文（FR-18）
 * - 上下文窗口管理：Max 模式放大窗口，历史裁剪保头尾
 */
import type { ChatMessage, PreferenceItem } from '@wdbuddy/shared-types';

/** 普通模式历史条数上限 / Max 模式上限 */
export const NORMAL_HISTORY_LIMIT = 20;
export const MAX_CONTEXT_HISTORY_LIMIT = 80;

/** 将偏好条目格式化为系统注入文本（无偏好时返回空串不注入） */
export function formatPreferenceInjection(preferences: readonly PreferenceItem[], maxChars = 2000): string {
  if (preferences.length === 0) return '';
  const lines = preferences
    .slice(0, 30)
    .map((p) => `- ${p.key}：${p.value}`)
    .join('\n');
  const text = `用户偏好（长期习惯，供参考）：\n${lines}`;
  return text.length > maxChars ? text.slice(0, maxChars) + '…' : text;
}

/** 窗口管理：保头（system）保尾（近期），中间裁剪 */
export function trimHistory(history: readonly ChatMessage[], maxContext: boolean): ChatMessage[] {
  const limit = maxContext ? MAX_CONTEXT_HISTORY_LIMIT : NORMAL_HISTORY_LIMIT;
  if (history.length <= limit) return [...history];
  const head = history.filter((m) => m.role === 'system').slice(0, 1);
  const rest = history.filter((m) => m.role !== 'system');
  const keep = rest.slice(-(limit - head.length));
  return [...head, ...keep];
}

/** 构造带偏好注入的消息头 */
export function buildContextMessages(
  instruction: string,
  preferences: readonly PreferenceItem[],
  maxContext: boolean
): ChatMessage[] {
  const msgs: ChatMessage[] = [];
  const pref = formatPreferenceInjection(preferences);
  if (pref) msgs.push({ role: 'system', content: pref });
  msgs.push({ role: 'user', content: instruction });
  return msgs;
}
