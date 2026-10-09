import { describe, expect, it } from 'vitest';
import type { PlanStep, Task } from '@wdbuddy/shared-types';
import { FsGuard, policyFor } from '@wdbuddy/sandbox';
import { AgentLoop, type PlanApproval } from './loop.js';
import { ToolRegistry } from '../tools/registry.js';
import { InMemoryCheckpointStore } from '../checkpoint/store.js';
import type { RuntimeOutboundEvent } from './types.js';
import type { Logger } from '../logger.js';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

const silentLogger: Logger = {
  debug: () => undefined,
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
  child: () => silentLogger,
};

function makeTask(tmp: string, mode: Task['mode'] = 'craft'): Task {
  return {
    id: 'task-1',
    title: '测试任务',
    mode,
    status: 'running',
    workspaceUri: tmp,
    permission: 'full',
    modelId: 'deepseek',
    maxContext: false,
    trigger: { type: 'manual' },
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

describe('AgentLoop 集成（mock LLM）', () => {
  it('全流程：计划 → 执行（写文件）→ 自检 → task/done', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-loop-'));
    const events: RuntimeOutboundEvent[] = [];

    // mock LLM：第一步返回工具调用，Planner 返回 2 步
    let stepCallCount = 0;
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      stepCallCount++;
      if (stepCallCount === 1) {
        yield {
          type: 'text',
          text: '我先把页面写出来。\n```json\n{"tool": "fs.write", "args": {"path": "' + path.join(tmp, 'index.html').replace(/\\/g, '/') + '", "content": "<html><body>hi</body></html>"}}\n```',
        };
      } else {
        yield { type: 'text', text: '步骤完成。' };
      }
    };
    const complete = async () => ({
      content: '[{"title": "生成页面框架"}, {"title": "自测与交付"}]',
    });

    const tools = new ToolRegistry();
    const { fsWriteTool, fsReadTool, fsListTool } = await import('../tools/fs.js');
    tools.register(fsWriteTool);
    tools.register(fsReadTool);
    tools.register(fsListTool);

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      tools,
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    await loop.run(makeTask(tmp), '做一个网页');

    // 文件确实写出
    expect(fs.existsSync(path.join(tmp, 'index.html'))).toBe(true);
    // 事件序列包含 done
    const methods = events.map((e) => e.method);
    expect(methods).toContain('task/step.update');
    expect(methods).toContain('task/artifact.created');
    expect(methods).toContain('task/done');
    // 自检通过（HTML 结构完整）
    const done = events.find((e) => e.method === 'task/done');
    expect(done && 'params' in done && (done.params as { report: { passed: boolean } }).report.passed).toBe(true);

    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('重规划超限后 emit task/error 并终止', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-loop2-'));
    const events: RuntimeOutboundEvent[] = [];

    // 永远失败的工具调用（写工作空间外）
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      yield {
        type: 'text',
        text: '{"tool": "fs.write", "args": {"path": "E:\\\\outside\\\\x.txt", "content": "x"}}',
      };
    };
    const complete = async () => ({ content: '[{"title": "一步"}]' });

    const tools = new ToolRegistry();
    const { fsWriteTool } = await import('../tools/fs.js');
    tools.register(fsWriteTool);

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'deny',
      approvalWaiter: async () => ({ decision: 'approve' }),
      tools,
      policy: policyFor('default', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'default' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    await loop.run(makeTask(tmp), '写文件到外部');

    const methods = events.map((e) => e.method);
    expect(methods).toContain('task/error');
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('Plan 模式先 emit task/plan.ready，未批准不执行任何步骤', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-loop3-'));
    const events: RuntimeOutboundEvent[] = [];
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      yield { type: 'text', text: '好的。' };
    };
    const complete = async () => ({ content: '[{"title": "步骤一"}]' });
    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      // 审批永不放行：验证 Loop 在 plan.ready 后阻塞、不执行步骤
      approvalWaiter: () => new Promise<PlanApproval>(() => undefined),
      tools: new ToolRegistry(),
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });
    // 不 await：Loop 应阻塞在审批等待，20ms 内不产生任何 step.update
    void loop.run(makeTask(tmp, 'plan'), '整理数据');
    await new Promise((r) => setTimeout(r, 20));
    const methods = events.map((e) => e.method);
    expect(methods).toContain('task/plan.ready');
    expect(methods).not.toContain('task/step.update');
    expect(methods).not.toContain('task/done');
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('Plan 模式批准携带编辑后步骤则替换执行计划', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-loop4-'));
    const events: RuntimeOutboundEvent[] = [];
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      yield { type: 'text', text: '执行完毕。' };
    };
    const complete = async () => ({ content: '[{"title": "原计划步骤"}]' });
    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({
        decision: 'approve',
        steps: [{ id: 'step-user-1', seq: 1, title: '用户改的步骤', status: 'pending', artifactIds: [] }],
      }),
      tools: new ToolRegistry(),
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });
    await loop.run(makeTask(tmp, 'plan'), '整理数据');
    // 用户编辑后的步骤被替换进执行流程
    const stepEvents = events.filter((e) => e.method === 'task/step.update');
    const titles = stepEvents.map(
      (e) => ('step' in e.params ? (e.params.step as { title: string }).title : '')
    );
    expect(titles).toContain('用户改的步骤');
    expect(titles).not.toContain('原计划步骤');
    fs.rmSync(tmp, { recursive: true, force: true });
  });
});
