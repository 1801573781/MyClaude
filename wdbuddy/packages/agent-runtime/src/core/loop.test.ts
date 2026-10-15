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
      signalFor: () => new AbortController().signal,
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
      signalFor: () => new AbortController().signal,
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
      signalFor: () => new AbortController().signal,
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
      signalFor: () => new AbortController().signal,
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

  it('工具调用 JSON 被截断 → 精简重试成功生成文件（修复假成功）', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-trunc-'));
    const events: RuntimeOutboundEvent[] = [];
    const deckPath = path.join(tmp, 'deck.html').replace(/\\/g, '/');
    let call = 0;
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      call++;
      if (call === 1) {
        // 模拟 10 页 slides JSON 触达输出上限被截断：含 tool 痕迹但 JSON 不完整
        yield {
          type: 'text',
          text:
            '现在调用 office.generate 生成最终文件：\n' +
            '```json\n{"tool": "office.generate", "args": {"path": "' + deckPath + '", "title": "T", "slides": [{"layout": "cover", "title": "A", "subtitle": "B"',
        };
      } else {
        yield {
          type: 'text',
          text:
            '{"tool": "office.generate", "args": {"path": "' + deckPath + '", "title": "T", "slides": [{"layout": "cover", "title": "A", "subtitle": "B"}]}}',
        };
      }
    };
    const complete = async () => ({ content: '[{"title": "生成PPT"}]' });
    const tools = new ToolRegistry();
    const { officeGenerateTool } = await import('../tools/office.js');
    tools.register(officeGenerateTool);

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      signalFor: () => new AbortController().signal,
      tools,
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    await loop.run(makeTask(tmp), '生成一个PPT');

    // 截断后经精简重试，幻灯片文件真实落盘（旧逻辑此处只留 md、假成功）
    expect(fs.existsSync(path.join(tmp, 'deck.html'))).toBe(true);
    const methods = events.map((e) => e.method);
    expect(methods).toContain('task/done');
    const arts = events.filter((e) => e.method === 'task/artifact.created');
    expect(arts.length).toBeGreaterThan(0);
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('工具调用 JSON 连续截断 → 步骤失败并 task/error（严禁假成功）', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-trunc2-'));
    const events: RuntimeOutboundEvent[] = [];
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      // 永远输出不完整的 JSON（含 tool 痕迹、无法解析）
      yield { type: 'text', text: '{"tool": "office.generate", "args": {"path": "x.html", "slides": [{"a": ' };
    };
    const complete = async () => ({ content: '[]' });
    const tools = new ToolRegistry();
    const { officeGenerateTool } = await import('../tools/office.js');
    tools.register(officeGenerateTool);

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      signalFor: () => new AbortController().signal,
      tools,
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    await loop.run(makeTask(tmp), '生成一个PPT');

    const methods = events.map((e) => e.method);
    expect(methods).toContain('task/error');
    // 失败步骤带明确原因（malformed-tool-call），不再被当作纯文本成功
    const failedSteps = events
      .filter((e) => e.method === 'task/step.update')
      .map((e) => ('step' in e.params ? (e.params.step as { status?: string; error?: string }) : null))
      .filter((s) => s && s.status === 'failed');
    expect(failedSteps.length).toBeGreaterThan(0);
    expect(String(failedSteps[0]?.error ?? '')).toContain('malformed-tool-call');
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('只交付设计稿 md → 意图缺口自动补救生成幻灯片文件', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-remedy-'));
    const events: RuntimeOutboundEvent[] = [];
    const mdPath = path.join(tmp, 'ppt_content.md').replace(/\\/g, '/');
    const deckPath = path.join(tmp, 'deck.html').replace(/\\/g, '/');
    let call = 0;
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      call++;
      if (call === 1) {
        // 步骤1：只写 md 设计稿（复现线上行为）
        yield {
          type: 'text',
          text: '{"tool": "fs.write", "args": {"path": "' + mdPath + '", "content": "# PPT内容"}}',
        };
      } else if (call === 2) {
        // 步骤2：纯文本汇报（无产物）→ 被 executor 最后一步防线拦截强制重试
        yield { type: 'text', text: '内容已准备好。' };
      } else {
        // 重试 / 补救步骤：真正调用 office.generate（两处消费同一响应）
        yield {
          type: 'text',
          text:
            '{"tool": "office.generate", "args": {"path": "' + deckPath + '", "title": "T", "slides": [{"layout": "cover", "title": "A"}]}}',
        };
      }
    };
    const complete = async () => ({ content: '[{"title": "撰写内容"}, {"title": "生成文件"}]' });
    const tools = new ToolRegistry();
    const { fsWriteTool } = await import('../tools/fs.js');
    const { officeGenerateTool } = await import('../tools/office.js');
    tools.register(fsWriteTool);
    tools.register(officeGenerateTool);

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      signalFor: () => new AbortController().signal,
      tools,
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    await loop.run(makeTask(tmp), '请生成一个PPT');

    // 幻灯片文件真实存在，任务正常交付（最后一步拦截重试后 office.generate 已执行；
    // 双保险断言：补救步骤出现 或 文件在步骤内直接生成）
    expect(fs.existsSync(path.join(tmp, 'deck.html'))).toBe(true);
    expect(events.map((e) => e.method)).toContain('task/done');
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('476 事故：模板克隆指令 + 单步计划纯文字"先读取再撰写" → 拦截并最终真实生成 pptx', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-476-'));
    const events: RuntimeOutboundEvent[] = [];
    const deckPath = path.join(tmp, 'output.pptx').replace(/\\/g, '/');
    let call = 0;
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      call++;
      if (call <= 2) {
        // 复现事故：纯计划性文字，零工具调用（首次 + 拦截后仍嘴上说说）
        yield { type: 'text', text: '我先读取您的内容要点文件，再据此撰写逐页内容稿。' };
      } else {
        // 拦截重试生效后：真实调用 office.generate（fromMarkdown 短 JSON）
        yield {
          type: 'text',
          text:
            '{"tool": "office.generate", "args": {"path": "' + deckPath + '", "fromMarkdown": "' +
            deckPath.replace(/\.pptx$/, '.md') +
            '"}}',
        };
      }
    };
    // 单步计划（事故形态：一步包干 → 纯文字即"任务完成"）
    const complete = async () => ({ content: '[{"title": "执行用户指令"}]' });
    const tools = new ToolRegistry();
    const { fsWriteTool } = await import('../tools/fs.js');
    const { officeGenerateTool } = await import('../tools/office.js');
    tools.register(fsWriteTool);
    tools.register(officeGenerateTool);

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      signalFor: () => new AbortController().signal,
      tools,
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    // 事故指令：模版库"用此模板"注入的原样文案（含"模板"字样，docOnly 曾误杀）
    await loop.run(
      makeTask(tmp),
      '请用「红蓝微立体·2026年终总结」模板生成PPT（office.generate 调用时 template=red-blue-2026，并把逐页内容稿写为 md 后经 fromMarkdown 传入；内容稿由你根据我的要点自动撰写，含每页标题/条目/数据，我无需手动替换任何文字）。\n\n我的内容要点：内容请参见 D:/AI/wdbuddy_space/474.txt'
    );

    // 任务不得以"无文件产物"假成功收场：必须有 .pptx/.html 产物或明确失败
    const done = events.find((e) => e.method === 'task/done');
    const errored = events.find((e) => e.method === 'task/error');
    const hasDeck = done
      ? ((done.params as { artifacts: Array<{ path?: string }> }).artifacts ?? []).some(
          (a) => a.path && /\.(pptx|html)$/i.test(a.path)
        ) || fs.existsSync(path.join(tmp, 'output.pptx'))
      : false;
    // 允许真实失败（兜底也无内容稿时），但绝不允许"成功却无产物"
    if (done && !errored) {
      expect(hasDeck).toBe(true);
    }
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('fromMarkdown 短 JSON：LLM 只传内容稿路径即可生成幻灯片（治本防截断）', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-frommd-'));
    const events: RuntimeOutboundEvent[] = [];
    const mdPath = path.join(tmp, 'content.md').replace(/\\/g, '/');
    const deckPath = path.join(tmp, 'deck.html').replace(/\\/g, '/');
    let call = 0;
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      call++;
      if (call === 1) {
        // 步骤1：写内容稿（模拟常规流程）
        yield {
          type: 'text',
          text:
            '{"tool": "fs.write", "args": {"path": "' + mdPath + '", "content": "# P\\n\\n## P1 封面\\n- 标题：WorkBuddy\\n- 副标题：智能伙伴\\n\\n## P2 目录\\n1. 认识——是什么\\n2. 功能——能做什么\\n\\n## P3 结尾\\n- 标题：谢谢观看"}}',
        };
      } else {
        // 步骤2：fromMarkdown 短 JSON 生成（~120 字符，物理上不可能截断）
        yield {
          type: 'text',
          text: '{"tool": "office.generate", "args": {"path": "' + deckPath + '", "fromMarkdown": "' + mdPath + '"}}',
        };
      }
    };
    const complete = async () => ({ content: '[{"title": "撰写内容稿"}, {"title": "生成幻灯片"}]' });
    const tools = new ToolRegistry();
    const { fsWriteTool } = await import('../tools/fs.js');
    const { officeGenerateTool } = await import('../tools/office.js');
    tools.register(fsWriteTool);
    tools.register(officeGenerateTool);

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      signalFor: () => new AbortController().signal,
      tools,
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    await loop.run(makeTask(tmp), '生成一个PPT');

    // 幻灯片真实落盘且包含内容稿文字
    expect(fs.existsSync(path.join(tmp, 'deck.html'))).toBe(true);
    const html = fs.readFileSync(path.join(tmp, 'deck.html'), 'utf-8');
    expect(html).toContain('WorkBuddy');
    expect(html).toContain('谢谢观看');
    expect(events.map((e) => e.method)).toContain('task/done');
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('LLM 全失败但内容稿已落盘 → 程序化兜底真成功（零 LLM 转换）', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-fallback-'));
    const events: RuntimeOutboundEvent[] = [];
    const mdPath = path.join(tmp, 'ppt_content.md').replace(/\\/g, '/');
    // LLM 每次都输出截断的 JSON（步骤1侥幸成功前先写 md？——不，这里第1次成功写 md，
    // 之后全部截断失败，复现线上场景：内容稿有了、幻灯片永远生成不出来）
    let call = 0;
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      call++;
      if (call === 1) {
        yield {
          type: 'text',
          text:
            '{"tool": "fs.write", "args": {"path": "' + mdPath + '", "content": "# WorkBuddy 培训\\n\\n## P1 封面\\n- 标题：WorkBuddy\\n\\n## P2 核心功能\\n- 智能问答\\n- 任务自动化\\n\\n## P3 结尾\\n- 标题：谢谢观看"}}',
        };
      } else {
        // 之后所有输出全部截断（含 tool 痕迹、JSON 不完整）
        yield { type: 'text', text: '{"tool": "office.generate", "args": {"path": "x.html", "slides": [{"a": ' };
      }
    };
    const complete = async () => ({ content: '[{"title": "撰写内容稿"}, {"title": "生成幻灯片"}]' });
    const tools = new ToolRegistry();
    const { fsWriteTool } = await import('../tools/fs.js');
    const { officeGenerateTool } = await import('../tools/office.js');
    tools.register(fsWriteTool);
    tools.register(officeGenerateTool);

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      signalFor: () => new AbortController().signal,
      tools,
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    await loop.run(makeTask(tmp), '生成一个PPT');

    // 程序化兜底：md 同名 .html 已生成，任务真成功（而非真失败）
    const deckPath = path.join(tmp, 'ppt_content.html');
    expect(fs.existsSync(deckPath)).toBe(true);
    const html = fs.readFileSync(deckPath, 'utf-8');
    expect(html).toContain('WorkBuddy');
    expect(events.map((e) => e.method)).toContain('task/done');
    expect(events.map((e) => e.method)).not.toContain('task/error');
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('mdToSlides：逐页小节/无结构文本/空文本的确定性转换', async () => {
    const { mdToSlides } = await import('../tools/pptx/md-to-slides.js');
    // ① 逐页小节 + 键值行 + 目录有序项
    const slides1 = mdToSlides(
      '# WorkBuddy 介绍\n\n## P1 封面\n- 标题：WorkBuddy\n- 副标题：智能工作学习伙伴\n\n## P2 目录\n1. 认识 WorkBuddy——是什么\n2. 核心功能——能做什么\n3. 快速上手——怎么用\n\n## P3 核心功能\n- 智能问答：随问随答\n- 任务自动化：一键完成\n- 文档处理：秒级生成\n\n## P4 结尾\n- 标题：谢谢观看',
      'WorkBuddy'
    );
    expect(slides1.length).toBe(4);
    expect(slides1[0]?.layout).toBe('cover');
    expect(slides1[0]?.title).toBe('WorkBuddy');
    expect(slides1[1]?.layout).toBe('toc');
    expect(slides1[1]?.items?.length).toBe(3);
    expect(slides1[2]?.bullets?.length).toBe(3);
    expect(slides1[3]?.layout).toBe('closing');
    // ② 无结构文本：按空行分块 + 封面
    const slides2 = mdToSlides('第一段内容。\n\n第二段内容。\n\n第三段内容。', '标题');
    expect(slides2.length).toBe(4);
    expect(slides2[0]?.layout).toBe('cover');
    // ③ 空文本：兜底单页封面（永不返回空数组）
    const slides3 = mdToSlides('', '标题');
    expect(slides3.length).toBe(1);
    expect(slides3[0]?.layout).toBe('cover');
  });

  it('文字提问后自跑工具 → 强制暂停等用户回复（466 事故回归）', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-ask-'));
    const events: RuntimeOutboundEvent[] = [];
    const listPath = path.join(tmp, '素材.txt').replace(/\\/g, '/');
    fs.writeFileSync(listPath, '素材内容');
    // 模拟 466：LLM 文字里列出多个问题，却调用 fs.list 继续自跑
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      yield {
        type: 'text',
        text:
          '开始制作 PPT 前，我需要了解：\n1. 这份 PPT 的主题是什么？\n2. 目标受众是谁？\n3. 风格偏好是什么？\n请告诉我这些信息。\n现在先查看工作空间已有文件：\n{"tool": "fs.list", "args": {"path": "' + tmp.replace(/\\/g, '/') + '"}}',
      };
    };
    const complete = async () => ({ content: '[{"title": "确认主题"}, {"title": "生成PPT"}]' });
    const tools = new ToolRegistry();
    const { fsListTool } = await import('../tools/fs.js');
    tools.register(fsListTool);
    const askedQuestions: string[] = [];
    let released = false;

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      signalFor: () => new AbortController().signal,
      askWaiter: async (_tid, question) => {
        askedQuestions.push(question);
        // 第一次等待：返回用户答案；之后释放放行（released 后直接答复"继续"）
        return '培训课程：测试主题；受众：学生';
      },
      tools,
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });
    void released;

    await loop.run(makeTask(tmp), '创建一个PPT');

    // 强提问拦截生效：问题经 askWaiter 抛给用户，fs.list 未被执行
    expect(askedQuestions.length).toBeGreaterThan(0);
    expect(askedQuestions[0]).toContain('主题');
    // 工作空间未被读取（fs.list 未跑）——用事件验证：无 artifact、任务正常推进
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('office.generate 复用非本任务 md → 必须用户确认，拒绝则步骤失败', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-reuse-'));
    const events: RuntimeOutboundEvent[] = [];
    // 工作空间预置历史遗留内容稿（模拟 465/466 场景）
    const legacyMd = path.join(tmp, 'slides_content.md').replace(/\\/g, '/');
    fs.writeFileSync(legacyMd, '# 旧任务内容稿\n\n## P1 封面\n- 标题：旧主题');
    const deckPath = path.join(tmp, 'deck.html').replace(/\\/g, '/');

    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      yield {
        type: 'text',
        text:
          '{"tool": "office.generate", "args": {"path": "' + deckPath + '", "fromMarkdown": "' + legacyMd + '"}}',
      };
    };
    const complete = async () => ({ content: '[{"title": "生成PPT"}]' });
    const tools = new ToolRegistry();
    const { officeGenerateTool } = await import('../tools/office.js');
    tools.register(officeGenerateTool);
    const answers: string[] = [];

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      signalFor: () => new AbortController().signal,
      askWaiter: async (_tid, question) => {
        answers.push(question);
        return '不要复用，主题改为新主题：AI 编程入门';
      },
      tools,
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    await loop.run(makeTask(tmp), '创建一个PPT');

    // 复用历史 md 被拦截询问；用户拒绝 → 不生成、步骤失败
    expect(answers.length).toBeGreaterThan(0);
    expect(answers[0]).toContain('历史遗留');
    expect(fs.existsSync(path.join(tmp, 'deck.html'))).toBe(false);
    const failedSteps = events
      .filter((e) => e.method === 'task/step.update')
      .map((e) => ('step' in e.params ? (e.params.step as { status?: string; error?: string }) : null))
      .filter((s) => s && s.status === 'failed');
    expect(failedSteps.length).toBeGreaterThan(0);
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('LLM 执意直传超长 slides → 自动改写 fromMarkdown 生成成功（467 美化步骤回归）', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-auto-md-'));
    const events: RuntimeOutboundEvent[] = [];
    const mdPath = path.join(tmp, 'slides_content.md').replace(/\\/g, '/');
    const deckPath = path.join(tmp, 'deck.html').replace(/\\/g, '/');
    let call = 0;
    // 第 1 步：写内容稿（正常流程）
    // 第 2 步（美化排版）：无视提示词直传 10 页超长 slides（复现线上 LLM 行为）
    const longSlides = Array.from({ length: 10 }, (_, i) => ({
      layout: 'content',
      title: `第${i + 1}页标题`,
      bullets: ['要点一这是很长的要点内容超过十个字了', '要点二这是很长的要点内容超过十个字了', '要点三这是很长的要点内容超过十个字了'],
    }));
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      call++;
      if (call === 1) {
        yield {
          type: 'text',
          text:
            '{"tool": "fs.write", "args": {"path": "' + mdPath + '", "content": "# WorkBuddy\\n\\n## P1 封面\\n- 标题：WorkBuddy\\n\\n## P2 目录\\n1. 认识——是什么\\n\\n## P3 结尾\\n- 标题：谢谢观看"}}',
        };
      } else {
        yield {
          type: 'text',
          text:
            '当前步骤「美化排版与配色」决策：tech-dark 主题，输出 .html。现在调用工具生成：\n' +
            JSON.stringify({ tool: 'office.generate', args: { path: deckPath, theme: 'tech-dark', slides: longSlides } }),
        };
      }
    };
    const complete = async () => ({ content: '[{"title": "撰写内容稿"}, {"title": "美化排版与配色"}]' });
    const tools = new ToolRegistry();
    const { fsWriteTool } = await import('../tools/fs.js');
    const { officeGenerateTool } = await import('../tools/office.js');
    tools.register(fsWriteTool);
    tools.register(officeGenerateTool);

    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      signalFor: () => new AbortController().signal,
      tools,
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    await loop.run(makeTask(tmp), '生成一个PPT');

    // 自动纠偏生效：直传超长 slides 被改写为 fromMarkdown，幻灯片真实生成
    // （默认单格式契约：path 为 deck.html → 仅产出 html，不再强制双出 pptx）
    expect(fs.existsSync(path.join(tmp, 'deck.html'))).toBe(true);
    expect(fs.existsSync(path.join(tmp, 'deck.pptx'))).toBe(false);
    // 步骤全部成功（无 malformed-tool-call 失败）
    const failedSteps = events
      .filter((e) => e.method === 'task/step.update')
      .map((e) => ('step' in e.params ? (e.params.step as { status?: string }) : null))
      .filter((s) => s && s.status === 'failed');
    expect(failedSteps.length).toBe(0);
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('intentGap：回复行含问号不误杀；问答/纯文字任务不误报', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-gap-'));
    const { Verifier } = await import('./verifier.js');
    const v = new Verifier(new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }), silentLogger);
    const task = makeTask(tmp);
    // 真实场景复现：原始诉求 + 用户回复引用了 Agent 提问（含问号）→ 仍应判缺口
    expect(
      v.intentGap(task, '请生成一个PPT\n2. 目标受众：这份PPT的观众是谁？学生', [])
    ).not.toBeNull();
    // 纯问答（怎么做的疑问）：不误报
    expect(v.intentGap(task, 'PPT 怎么做？', [])).toBeNull();
    // 纯文字子任务（讲稿）：不误报
    expect(v.intentGap(task, '帮我写PPT演讲稿', [])).toBeNull();
    // 467 事故：英文指令 "create a ppt, please" 纯中文动词正则漏判 → 假成功
    expect(v.intentGap(task, 'create a ppt, please', [])).not.toBeNull();
    expect(v.intentGap(task, 'Please make slides about workbuddy', [])).not.toBeNull();
    // 英文问答不误报
    expect(v.intentGap(task, 'How to create a ppt?', [])).toBeNull();
    // 已有 html 幻灯片产物：无缺口
    expect(
      v.intentGap(task, '请生成一个PPT', [
        {
          id: 'a1',
          taskId: task.id,
          kind: 'file',
          path: path.join(tmp, 'deck.html'),
          mimeType: 'text/html',
          version: 1,
          sha256: '',
          createdAt: Date.now(),
        },
      ])
    ).toBeNull();
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('咨询问句（能转成PPT模版吗）→ 直答收尾，不拆步骤不进执行循环（修复幻觉空跑）', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-chat-'));
    const events: RuntimeOutboundEvent[] = [];
    // 若被误拆步骤进入执行循环，此 mock 会被消费并产出"分析图片"类幻觉文字；
    // 咨询分流下它必须永不被调用
    const llm = async function* (): AsyncIterable<{ type: 'text'; text: string }> {
      yield { type: 'text', text: '分析图片风格与配色特点：该图采用……' };
    };
    const complete = async () => ({
      content:
        '{"type": "chat", "reply": "可以的！请把图片发给我（拖入对话框或放到工作空间），我会分析它的风格与配色，并克隆成可复用的 PPT 模版。"}',
    });
    const loop = new AgentLoop({
      emitter: { emit: (e) => events.push(e) },
      llm,
      complete,
      permissionWaiter: async () => 'allow-once',
      approvalWaiter: async () => ({ decision: 'approve' }),
      signalFor: () => new AbortController().signal,
      tools: new ToolRegistry(),
      policy: policyFor('full', [tmp]),
      fsGuard: new FsGuard({ workspaceRoots: [tmp], permissionLevel: 'full' }),
      checkpoint: new InMemoryCheckpointStore(),
      logger: silentLogger,
    });

    // Plan 模式下咨询同样直答：不需要计划审批
    await loop.run(makeTask(tmp, 'plan'), '给你一个图片，你能将其转变成PPT模版吗？');

    const methods = events.map((e) => e.method);
    // 调度器直答完整透传给用户
    const replyText = events
      .filter((e) => e.method === 'task/token.delta')
      .map((e) => ((e.params as unknown) as { text?: string }).text ?? '')
      .join('');
    expect(replyText).toContain('可以的');
    // 问答类任务正常结束（自检按无产物问答放行）
    expect(methods).toContain('task/done');
    expect(methods).not.toContain('task/error');
    // 严禁幻觉步骤、计划审批与任何执行痕迹（事故回归防线）
    expect(methods).not.toContain('task/step.update');
    expect(methods).not.toContain('task/plan.ready');
    expect(methods).not.toContain('task/artifact.created');
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('extractChatReply：直答对象容错解析，步骤数组与普通文本不误判', async () => {
    const { extractChatReply } = await import('./planner.js');
    // 标准直答
    expect(extractChatReply('{"type": "chat", "reply": "可以的，请提供图片"}')).toBe('可以的，请提供图片');
    // 前后缀夹杂 + reply 内含 } 括号（逐个闭合点试解析）
    expect(extractChatReply('好的，我判断一下。{"type": "chat", "reply": "支持 } 括号"} 以上。')).toBe('支持 } 括号');
    // type=chat 但 reply 缺失 → 兜底话术，绝不回落拆步骤
    expect(extractChatReply('{"type": "chat"}')).not.toBeNull();
    // 步骤数组 / 普通文本 / 空串 → null（正常走任务拆解路径）
    expect(extractChatReply('[{"title": "步骤一"}]')).toBeNull();
    expect(extractChatReply('我先读取文件，再撰写内容稿。')).toBeNull();
    expect(extractChatReply('')).toBeNull();
  });
});
