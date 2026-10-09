/**
 * model-gateway 模块
 * POST /v1/gateway/chat → SSE 代理（桌面端模型调用经云端转发，统一计费）
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { RouteHandler } from '../../main.js';
import { readBody } from '../../http-utils.js';
import { accountModule } from '../account/index.js';

export const modelGatewayModule = {
  routes: [
    {
      method: 'POST',
      path: '/v1/gateway/chat',
      async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
        const body = JSON.parse((await readBody(req)) || '{}') as {
          model?: string;
          messages?: Array<{ role: string; content: string }>;
          stream?: boolean;
        };

        const auth = req.headers['authorization'] ?? '';
        const userId = auth.replace(/^Bearer\s+/i, '') || 'anonymous';
        const model = body.model ?? 'deepseek';

        // SSE 代理：透传为 OpenAI 兼容 SSE（演示：直接生成占位流；生产转发上游 Provider）
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
        });

        const reply = `[gateway:${model}] 已收到 ${body.messages?.length ?? 0} 条消息（SSE 代理就绪，生产环境转发至上游 Provider）`;
        const chunks = reply.match(/.{1,8}/g) ?? [reply];
        for (const chunk of chunks) {
          res.write(
            `data: ${JSON.stringify({
              id: `gw-${Date.now()}`,
              choices: [{ index: 0, delta: { content: chunk }, finish_reason: null }],
            })}\n\n`
          );
          await new Promise((r) => setTimeout(r, 10));
        }
        res.write('data: [DONE]\n\n');
        res.end();

        // 计费：按消息数扣减积分（演示规则：1 条消息 = 1 积分）
        accountModule.deduct(userId, `gw-${Date.now().toString(36)}`, model, Math.max(1, body.messages?.length ?? 1));
      },
    },
  ],
};
