/**
 * remote-command 模块（FR-20 多端联动）
 * POST /v1/remote/commands → 手机下发指令，经推送通道到达在线桌面端
 * GET  /v1/remote/commands?deviceId= → 桌面端轮询拉取待执行指令
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { RouteHandler } from '../../main.js';
import type { RemoteCommand } from '@wdbuddy/shared-types';
import { readBody, sendJson } from '../../http-utils.js';

/** 待执行指令队列（设备维度） */
const queues = new Map<string, RemoteCommand[]>();

/** 设备在线状态（桌面端心跳维护；离线时手机端得到明确提示） */
const desktopOnline = new Map<string, boolean>();

export const remoteCommandModule = {
  routes: [
    {
      method: 'POST',
      path: '/v1/remote/commands',
      async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
        const body = JSON.parse((await readBody(req)) || '{}') as {
          deviceId?: string;
          instruction?: string;
        };
        const deviceId = body.deviceId ?? '';
        const instruction = body.instruction ?? '';

        if (!deviceId || !instruction) {
          sendJson(res, 400, { ok: false, error: '缺少 deviceId 或 instruction' });
          return;
        }

        // 桌面离线时明确提示（FR-20）
        if (desktopOnline.get(deviceId) === false) {
          sendJson(res, 409, { ok: false, error: '桌面端离线，指令无法送达，请确认桌面端已启动' });
          return;
        }

        const cmd: RemoteCommand = {
          id: `cmd-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
          deviceId,
          instruction,
          createdAt: Date.now(),
          status: 'pending',
        };
        const q = queues.get(deviceId) ?? [];
        q.push(cmd);
        queues.set(deviceId, q);
        sendJson(res, 200, { ok: true, command: cmd });
      },
    },
    {
      method: 'GET',
      path: '/v1/remote/commands',
      async handle(req: IncomingMessage, res: ServerResponse, _params: Record<string, string>): Promise<void> {
        const url = new URL(req.url ?? '/', 'http://localhost');
        const deviceId = url.searchParams.get('deviceId') ?? '';
        const q = queues.get(deviceId) ?? [];
        // 拉取即标记 dispatched
        for (const c of q) if (c.status === 'pending') c.status = 'dispatched';
        sendJson(res, 200, { commands: q });
      },
    },
    {
      method: 'POST',
      path: '/v1/remote/heartbeat',
      async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
        const body = JSON.parse((await readBody(req)) || '{}') as { deviceId?: string };
        const deviceId = body.deviceId ?? '';
        if (deviceId) desktopOnline.set(deviceId, true);
        sendJson(res, 200, { ok: true });
      },
    },
  ],
  /** 桌面端离线登记（连接断开时调用） */
  markOffline(deviceId: string): void {
    desktopOnline.set(deviceId, false);
  },
};
