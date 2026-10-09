/**
 * mock-model-server — 脚本化假模型（NFR-04 可测试性）
 *
 * 行为：
 * - OpenAI 兼容 /v1/chat/completions（流式 SSE 与非流式）
 * - 响应由"脚本"决定：按调用次序返回预置回复序列
 * - 脚本经 /admin/script 或启动参数注入，E2E 中实现确定性
 *
 * 用法：
 *   WB_MOCK_PORT=8787 node --experimental-strip-types src/server.ts
 *   curl -X POST localhost:8787/admin/script -d '[{"content":"你好"}]'
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';

interface MockTurn {
  content: string;
  delayMs?: number;
  /** 注入 HTTP 状态码模拟失败（重试/降级链测试） */
  failWithStatus?: number;
}

let script: MockTurn[] = [];
let callIndex = 0;

const PORT = Number(process.env['WB_MOCK_PORT'] ?? 8787);

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c: Buffer) => (data += c.toString()));
    req.on('end', () => resolve(data));
  });
}

/** 按当前脚本取下一轮回复 */
function nextTurn(): MockTurn {
  if (script.length === 0) return { content: '（mock：未配置脚本）' };
  const turn = script[Math.min(callIndex, script.length - 1)]!;
  callIndex++;
  return turn;
}

async function handleChat(req: IncomingMessage, res: ServerResponse, stream: boolean): Promise<void> {
  const turn = nextTurn();

  if (turn.failWithStatus) {
    res.writeHead(turn.failWithStatus, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: { message: `mock 注入失败：${turn.failWithStatus}` } }));
    return;
  }

  if (!stream) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(
      JSON.stringify({
        id: 'mock-' + callIndex,
        choices: [{ index: 0, message: { role: 'assistant', content: turn.content }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 10, completion_tokens: Math.ceil(turn.content.length / 4) },
      })
    );
    return;
  }

  // 流式 SSE：按 3 字符分片，模拟 token 到达节奏
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  });
  const chunks = turn.content.match(/.{1,3}/g) ?? [''];
  for (const chunk of chunks) {
    res.write(
      `data: ${JSON.stringify({
        id: 'mock-' + callIndex,
        choices: [{ index: 0, delta: { content: chunk }, finish_reason: null }],
      })}\n\n`
    );
    await sleep(turn.delayMs ?? 5);
  }
  res.write(`data: ${JSON.stringify({ id: 'mock-' + callIndex, choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })}\n\n`);
  res.write('data: [DONE]\n\n');
  res.end();
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

const server = createServer(async (req, res) => {
  const url = req.url ?? '';

  // 管理端点：注入脚本
  if (url === '/admin/script' && req.method === 'POST') {
    const body = await readBody(req);
    try {
      script = JSON.parse(body) as MockTurn[];
      callIndex = 0;
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, turns: script.length }));
    } catch {
      res.writeHead(400);
      res.end('{"ok":false}');
    }
    return;
  }

  if (url === '/admin/reset' && req.method === 'POST') {
    script = [];
    callIndex = 0;
    res.writeHead(200);
    res.end('{"ok":true}');
    return;
  }

  if (url === '/health' && req.method === 'GET') {
    res.writeHead(200);
    res.end('{"ok":true}');
    return;
  }

  if (url === '/v1/chat/completions' && req.method === 'POST') {
    const body = await readBody(req);
    try {
      const parsed = JSON.parse(body) as { stream?: boolean };
      await handleChat(req, res, parsed.stream === true);
    } catch {
      res.writeHead(400);
      res.end('{"ok":false}');
    }
    return;
  }

  res.writeHead(404);
  res.end('{"ok":false}');
});

server.listen(PORT, () => {
  process.stdout.write(`[mock-model-server] listening on http://localhost:${PORT}\n`);
});
