/**
 * VisionProvider — 视觉理解模型（OpenAI 兼容多模态协议）
 * 用途：template.from_image 的"图片 → DesignSpec"提取。
 * 与普通 chat provider 的差异：消息携带 image_url，模型必须具备视觉能力
 *（如 GLM-4V / Kimi-vision / 自定义兼容端点）。
 *
 * 独立于 ModelId 联合类型：视觉模型是"能力"而非"路由目标"，经
 * RuntimeProviderConfig.visionConfig 注入（用户在设置页配置 baseUrl/apiModel/apiKey）。
 */
import type { ChatMessage } from '@wdbuddy/shared-types';
import { buildApiMessages } from './providers/openai-compat.js';

export interface VisionConfig {
  /** 兼容端点，如 https://open.bigmodel.cn/api/paas/v4 */
  baseUrl: string;
  /** 视觉模型名，如 glm-4v-plus */
  apiModel: string;
  /** API Key（Main 从钥匙串注入，不落盘） */
  apiKey: string;
}

/** 视觉模型调用（非流式：结构化提取场景无需流式，一次性 JSON 最稳） */
export async function visionChat(
  cfg: VisionConfig,
  messages: ChatMessage[],
  opts: { maxTokens?: number; temperature?: number; signal?: AbortSignal } = {}
): Promise<string> {
  const res = await fetch(`${cfg.baseUrl.replace(/\/+$/, '')}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${cfg.apiKey}`,
    },
    body: JSON.stringify({
      model: cfg.apiModel,
      messages: buildApiMessages(messages),
      max_tokens: opts.maxTokens ?? 4096,
      temperature: opts.temperature ?? 0.2,
    }),
    signal: opts.signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`[vision] ${cfg.apiModel} HTTP ${res.status}: ${text.slice(0, 300)}`);
  }
  const json = (await res.json()) as {
    choices?: Array<{ message?: { content?: string } }>;
  };
  return json.choices?.[0]?.message?.content ?? '';
}

/** 从模型输出提取 JSON（容错：```json 围栏 / 前后杂文） */
export function extractJson(text: string): unknown {
  const fence = /```(?:json)?\s*([\s\S]*?)```/.exec(text);
  const raw = fence ? fence[1]! : text;
  const start = raw.indexOf('{');
  const end = raw.lastIndexOf('}');
  if (start < 0 || end <= start) throw new Error('输出中未找到 JSON 对象');
  return JSON.parse(raw.slice(start, end + 1));
}
