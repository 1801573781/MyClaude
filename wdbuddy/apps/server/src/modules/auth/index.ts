/**
 * auth 模块（FR-21）
 * POST /v1/auth/wechat-qr     → 生成扫码二维码
 * GET  /v1/auth/wechat-qr/:id → 扫码状态轮询（confirmed 时下发 token）
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { RouteHandler } from '../../main.js';
import type { WeChatQrResponse } from '@wdbuddy/shared-types';
import { readBody, sendJson } from '../../http-utils.js';

/** 会话内存存储（生产换 Redis） */
const sessions = new Map<string, { status: 'pending' | 'confirmed'; tokens?: { accessToken: string; refreshToken: string; expiresAt: number } }>();

export const authModule = {
  routes: [
    {
      method: 'POST',
      path: '/v1/auth/wechat-qr',
      async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
        const sceneId = `scene-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
        sessions.set(sceneId, { status: 'pending' });
        const body: WeChatQrResponse = {
          qrUrl: `https://open.weixin.qq.com/connect/confirm?scene=${sceneId}`,
          sceneId,
          expiresIn: 300,
        };
        sendJson(res, 200, body);
      },
    },
    {
      method: 'GET',
      path: /^\/v1\/auth\/wechat-qr\/(?<sceneId>[^/]+)$/,
      async handle(req: IncomingMessage, res: ServerResponse, params: Record<string, string>): Promise<void> {
        const s = sessions.get(params['sceneId'] ?? '');
        if (!s) {
          sendJson(res, 404, { status: 'not-found' });
          return;
        }
        if (s.status === 'pending') {
          // 模拟扫码确认（演示环境自动确认；生产接微信回调）
          s.status = 'confirmed';
          s.tokens = {
            accessToken: `at-${Math.random().toString(36).slice(2)}`,
            refreshToken: `rt-${Math.random().toString(36).slice(2)}`,
            expiresAt: Date.now() + 7 * 24 * 3600_000,
          };
        }
        sendJson(res, 200, { status: s.status, tokens: s.tokens });
      },
    },
  ],
};

// 显式引用避免未使用告警
void readBody;
