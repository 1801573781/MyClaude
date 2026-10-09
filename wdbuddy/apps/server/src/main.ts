/**
 * WDBuddy 云端服务入口（§11.3，NestJS 风格模块化——本实现用原生 http 组装模块，结构等价）
 *
 * 模块：
 * - auth          POST /v1/auth/wechat-qr（扫码登录）
 * - account       GET  /v1/account/credits（积分余额与明细）
 * - model-gateway POST /v1/gateway/chat（SSE 代理）
 * - skill-market  GET  /v1/skills、POST /v1/skills/install
 * - remote-command POST /v1/remote/commands（手机指令经推送通道到达桌面）
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { authModule } from './modules/auth/index.js';
import { accountModule } from './modules/account/index.js';
import { modelGatewayModule } from './modules/model-gateway/index.js';
import { skillMarketModule } from './modules/skill-market/index.js';
import { remoteCommandModule } from './modules/remote-command/index.js';

export interface ModuleContext {
  body<T>(req: IncomingMessage): Promise<T>;
  json(res: ServerResponse, status: number, data: unknown): void;
}

export interface RouteHandler {
  method: string;
  path: string | RegExp;
  handle(req: IncomingMessage, res: ServerResponse, params: Record<string, string>): Promise<void>;
}

const routes: RouteHandler[] = [
  ...authModule.routes,
  ...accountModule.routes,
  ...modelGatewayModule.routes,
  ...skillMarketModule.routes,
  ...remoteCommandModule.routes,
];

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c: Buffer) => (data += c.toString()));
    req.on('end', () => resolve(data));
  });
}

function sendJson(res: ServerResponse, status: number, data: unknown): void {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
  const method = req.method ?? 'GET';

  for (const route of routes) {
    const pathMatch = typeof route.path === 'string' ? route.path === url.pathname : route.path.test(url.pathname);
    if (pathMatch && route.method === method) {
      try {
        const params: Record<string, string> = {};
        if (route.path instanceof RegExp) {
          const m = url.pathname.match(route.path);
          if (m?.groups) Object.assign(params, m.groups);
        }
        await route.handle(req, res, params);
      } catch (err) {
        sendJson(res, 500, { error: err instanceof Error ? err.message : String(err) });
      }
      return;
    }
  }

  sendJson(res, 404, { error: 'Not Found' });
});

const PORT = Number(process.env['WB_SERVER_PORT'] ?? 8600);
server.listen(PORT, () => {
  process.stdout.write(`[wdbuddy-server] listening on http://localhost:${PORT}\n`);
});
