/**
 * account 模块（FR-21）
 * GET /v1/account/credits → 积分余额与用量明细（模型调用扣减积分）
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { RouteHandler } from '../../main.js';
import type { CreditsInfo } from '@wdbuddy/shared-types';
import { sendJson } from '../../http-utils.js';

/** 内存账本（生产换 DB） */
const accounts = new Map<string, CreditsInfo>();

function ensureAccount(userId: string): CreditsInfo {
  let a = accounts.get(userId);
  if (!a) {
    a = { balance: 1000, usage: [] };
    accounts.set(userId, a);
  }
  return a;
}

export const accountModule = {
  routes: [
    {
      method: 'GET',
      path: '/v1/account/credits',
      async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
        const auth = req.headers['authorization'] ?? '';
        const userId = auth.replace(/^Bearer\s+/i, '') || 'anonymous';
        const account = ensureAccount(userId);
        sendJson(res, 200, account);
      },
    },
  ],
  /** 模型网关调用后扣减积分（model-gateway 模块回调） */
  deduct(userId: string, taskId: string, model: string, cost: number): void {
    const a = ensureAccount(userId);
    a.balance = Math.max(0, a.balance - cost);
    a.usage.push({ taskId, model, cost, at: Date.now() });
  },
};
