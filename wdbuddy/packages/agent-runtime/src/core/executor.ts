/**
 * Executor（§10.3）
 * 每步 = 选工具 → policy-engine 检查（默认权限下敏感操作 emit task/permission.request 并阻塞等待）→ 沙箱执行 → 观察结果。
 *
 * 工具选择：解析 LLM 输出中的工具调用（JSON 代码块约定）：
 *   {"tool": "fs.write", "args": {...}}
 */
import * as path from 'node:path';
import type {
  Artifact,
  PermissionRequest,
  PlanStep,
  Task,
  ToolName,
} from '@wdbuddy/shared-types';
import type { ExecutorDeps, StepResult } from './types.js';
import { randomId } from '../tools/registry.js';
import { hasDeckFileIntent } from './verifier.js';

/** 中断异常：任务取消时抛出，由循环层捕获并优雅收尾 */
export class TaskCancelledError extends Error {
  constructor() {
    super('任务已被用户取消');
    this.name = 'TaskCancelledError';
  }
}

export interface ToolCall {
  tool: ToolName;
  args: Record<string, unknown>;
}

/** 从 LLM 输出解析工具调用（支持 ```json 代码块或裸 JSON） */
export function parseToolCall(text: string): ToolCall | null {
  const tryParse = (s: string): ToolCall | null => {
    try {
      const obj = JSON.parse(s) as { tool?: unknown; args?: unknown };
      if (typeof obj['tool'] === 'string' && typeof obj['args'] === 'object' && obj['args']) {
        return { tool: obj['tool'] as ToolName, args: obj['args'] as Record<string, unknown> };
      }
    } catch {
      /* 非法 JSON */
    }
    return null;
  };

  // 三级解析（json 围栏内容 → 全文贪婪匹配 → 逐个 tool 起点兜底），
  // 提升对围栏包裹、正文含杂散花括号、轻微畸形输出的容错
  const fence = new RegExp('`'.repeat(3) + '(?:json)?\\s*(\\{[\\s\\S]*?)' + '`'.repeat(3)).exec(text);
  if (fence && fence[1]!.includes('"tool"')) {
    const c = tryParse(fence[1]!.trim());
    if (c) return c;
  }
  const m = /\{[\s\S]*"tool"[\s\S]*\}/.exec(text);
  if (m) {
    const c = tryParse(m[0]);
    if (c) return c;
  }
  for (let idx = text.indexOf('{"tool"'); idx >= 0; idx = text.indexOf('{"tool"', idx + 1)) {
    const sub = text.slice(idx);
    const last = sub.lastIndexOf('}');
    if (last > 0) {
      const c = tryParse(sub.slice(0, last + 1));
      if (c) return c;
    }
  }
  return null;
}

/** 文本中是否含工具调用痕迹（出现 tool 字段但整体解析失败 → 疑似截断/畸形，
 *  用于区分纯文本回答与想调工具但 JSON 坏了，后者严禁视为步骤成功） */
export function hasToolCallTrace(text: string): boolean {
  return /\{\s*"tool"\s*:/.test(text);
}

/** 强提问意图检测：≥2 个问号 + "请告诉/提供/确认"类引导语。
 *  LLM 在文字里向用户提出关键问题（主题/受众等）却没有输出 user.ask 工具
 *  调用、反而继续自跑——历史事故：Agent 问了主题没等回答，凭工作空间
 *  历史遗留文件自作主张复用并跑失败。命中即应强制暂停等用户回复。 */
export function detectQuestionIntent(text: string): boolean {
  const qCount = (text.match(/？|\?/g) ?? []).length;
  return qCount >= 2 && /请(告诉|提供|确认|说明|回复|补充)/.test(text);
}

export class Executor {
  constructor(private readonly deps: ExecutorDeps) {}

  async execute(step: PlanStep, ctx: import('./types.js').LoopContext): Promise<StepResult> {
    const { task } = ctx;
    const log = this.deps.logger.child({ stepId: step.id });

    // 1. 构造步骤执行提示
    const system = buildStepSystemPrompt(task, step, ctx);
    // 步骤入口取消检查（运行中点 ⏹ 立即中断本步）
    if (ctx.signal.aborted) throw new TaskCancelledError();

    // 截断重试：LLM 输出的工具调用 JSON 超长被截断（或格式非法）时，反馈失败
    // 原因并要求精简后重新输出（本步骤内最多重试 2 次）。
    // 根因：10 页 PPT 的 slides JSON 一次输出极易触达输出上限被截断，
    // parseToolCall 返回 null 后旧逻辑把整段当"纯文本回答"标记步骤成功，
    // 造成"说要调用 office.generate 实际什么都没生成"的假成功。
    const MAX_MALFORMED_RETRIES = 2;
    // 补救步骤（loop 交付前自检构造，标题前缀"补救："）：语义是"必须真正生成
    // 文件"，纯文字计划（"我将写入…"却无任何工具调用）严禁判成功
    const isRemedy = step.title.startsWith('补救：');
    const extraMsgs: Array<{ role: 'assistant' | 'user'; content: string }> = [];
    let call: ToolCall | null = null;
    let llmText = '';

    for (let attempt = 0; attempt <= MAX_MALFORMED_RETRIES; attempt++) {
      llmText = '';
      // 流式外发守卫：工具调用 JSON（{"tool": ...）不发给用户——一旦检测到即永久
      // 截断（连带紧前的 ```json 围栏头一并剔除）；末尾 60 字符内的 { 或 ` 暂扣待判
      //（可能是即将成形的工具调用开头）；流结束确认无工具调用则全文放行。
      // 修复：{"tool": "user.ask", ...} 等裸露在对话气泡里
      let emitted = 0;
      let toolCut = -1;
      const detectCut = (): void => {
        if (toolCut >= 0) return;
        const m = /\{\s*"tool"\s*:/.exec(llmText);
        if (!m) return;
        let cut = m.index;
        const fence = /(```[a-zA-Z]*[ \t]*)$/.exec(llmText.slice(0, cut));
        if (fence) cut -= fence[1]!.length;
        toolCut = Math.max(emitted, cut);
      };
      const safeEnd = (): number => {
        detectCut();
        if (toolCut >= 0) return toolCut;
        // 窗口 120 且逐级匹配围栏标记：覆盖 "```json\n" 围栏头到 {"tool" 的间距
        //（60 偶尔不够，导致孤零零的 ```json 围栏头裸露在对话气泡里）
        const winStart = Math.max(0, llmText.length - 120);
        let safe = llmText.length;
        for (const mark of ['{', '```json', '```', '`']) {
          const idx = llmText.lastIndexOf(mark);
          if (idx >= winStart && idx < safe) safe = idx;
        }
        return safe;
      };
      const emitUpTo = (end: number): void => {
        if (end <= emitted) return;
        this.deps.emitter.emit({
          method: 'task/token.delta',
          params: { taskId: task.id, text: llmText.slice(emitted, end) },
        });
        emitted = end;
      };
      for await (const delta of this.deps.llm({
        model: task.modelId,
        messages: [
          { role: 'system', content: system },
          ...ctx.history.slice(-10),
          { role: 'user', content: `当前步骤：${step.title}\n请决策下一步动作。` },
          ...extraMsgs,
        ],
        signal: ctx.signal,
      })) {
        if (ctx.signal.aborted) throw new TaskCancelledError();
        llmText += delta.text;
        emitUpTo(safeEnd());
      }
      // 流结束：按最终裁剪点补发（无工具调用则放行全文）
      detectCut();
      emitUpTo(toolCut >= 0 ? toolCut : llmText.length);

      // 2. 解析工具调用
      call = parseToolCall(llmText);
      if (call) break;

      // 无任何工具调用痕迹 → 纯文本输出
      if (!hasToolCallTrace(llmText)) {
        // 2a. 强提问意图：LLM 用文字向用户提了关键问题 → 强制暂停等回复
        //（严禁问完自答/问完继续跑）
        if (detectQuestionIntent(llmText)) {
          const answer = await this.askUserAndAwait(task, ctx, llmText.slice(0, 1000));
          if (answer !== null) {
            return {
              stepId: step.id,
              ok: true,
              output: `已向用户提问并收到回复：${answer.slice(0, 200)}。请结合回复在后续步骤决策。`,
              artifacts: [],
            };
          }
        }
        // 2b. 补救步骤禁止"只说不做"：纯文字计划无工具调用 → 重试强制真调用
        if (isRemedy) {
          log.warn('补救步骤仅输出文字计划，未调用工具，强制重试', { attempt });
          if (attempt < MAX_MALFORMED_RETRIES) {
            this.deps.emitter.emit({
              method: 'task/token.delta',
              params: { taskId: task.id, text: '\n\n> ⚠ 补救步骤未调用任何工具（仅文字说明），正在要求实际执行…\n\n' },
            });
            extraMsgs.push(
              { role: 'assistant', content: llmText.slice(0, 1000) },
              {
                role: 'user',
                content:
                  '[系统反馈] 补救步骤必须实际调用工具，纯文字无效。请立即输出工具调用 JSON：先用 fs.write 把逐页文案写入工作空间 md 文件，或直接输出 {"tool": "office.generate", "args": {"path": "输出.html", "fromMarkdown": "md路径"}}。不要再输出计划说明。',
              }
            );
            continue;
          }
          return {
            stepId: step.id,
            ok: false,
            output: '补救步骤连续未调用任何工具（仅输出了文字计划），无法生成交付文件。',
            artifacts: [],
            error: 'remedy-no-tool-call',
          };
        }
        // 2c. 普通步骤的纯文本回答：视为步骤产出说明。
        // 交付型任务最后一步防线（476 事故）：意图要求生成幻灯片文件、本任务
        // 无任何 .pptx/.html 产物、且本步骤之后没有其他待执行步骤时，纯文字 =
        // "只说不做"（如"我先读取文件，再撰写内容稿"的计划性表态），严禁判成功
        const hasPendingAfter = ctx.steps.some(
          (s) => s.id !== step.id && (s.status === 'pending' || s.status === 'running')
        );
        const hasDeckArtifact = ctx.artifacts.some(
          (a) => a.path !== undefined && /\.(pptx|html)$/i.test(a.path)
        );
        if (!hasPendingAfter && !hasDeckArtifact && hasDeckFileIntent(ctx.instruction, task.title)) {
          log.warn('交付型任务最后一步仅输出文字且无幻灯片产物，强制重试', { attempt });
          if (attempt < MAX_MALFORMED_RETRIES) {
            this.deps.emitter.emit({
              method: 'task/token.delta',
              params: {
                taskId: task.id,
                text: '\n\n> ⚠ 任务要求生成幻灯片文件但尚未产出，正在要求实际执行…\n\n',
              },
            });
            extraMsgs.push(
              { role: 'assistant', content: llmText.slice(0, 1000) },
              {
                role: 'user',
                content:
                  '[系统反馈] 任务要求生成 PPT/演示文稿文件，但至今没有任何 .pptx/.html 产物，纯文字说明无效。请立即实际调用工具：先把逐页内容稿用 fs.write 写入 md 文件，再调用 {"tool": "office.generate", "args": {"path": "输出路径.pptx", "fromMarkdown": "md文件路径"}}。不要再输出计划或说明文字。',
              }
            );
            continue;
          }
          return {
            stepId: step.id,
            ok: false,
            output: '交付型任务最后一步未生成幻灯片文件（仅输出了文字说明，无工具调用）。',
            artifacts: [],
            error: 'deck-not-generated',
          };
        }
        return { stepId: step.id, ok: true, output: llmText, artifacts: [] };
      }

      // 有工具调用痕迹但解析失败（截断/畸形）：严禁当作纯文本成功
      log.warn('工具调用 JSON 解析失败（疑似截断）', { attempt, len: llmText.length });
      if (attempt < MAX_MALFORMED_RETRIES) {
        // 重试提示（用户可见）：输出被截断，正在精简重试
        this.deps.emitter.emit({
          method: 'task/token.delta',
          params: { taskId: task.id, text: '\n\n> ⚠ 上次工具调用输出疑似被截断，正在要求精简后重试…\n\n' },
        });
        extraMsgs.push(
          { role: 'assistant', content: llmText.slice(0, 1500) },
          {
            role: 'user',
            content: `[系统反馈] 你上一次输出的工具调用 JSON 解析失败（共 ${llmText.length} 字符，疑似被截断或含非法字符）。请重新输出【完整的】工具调用 JSON。为避免再次被截断，必须大幅精简：office.generate 的 slides 每页 bullets 不超过 3 条、每条不超过 15 字；删除一切解释文字，只输出工具调用 JSON 本身。`,
          }
        );
        continue;
      }
      // 重试耗尽 → 步骤失败（交由上层重规划），并把教训写入历史供后续步骤吸取
      ctx.history.push({
        role: 'user',
        content: `[系统] 步骤「${step.title}」的工具调用 JSON 连续 ${MAX_MALFORMED_RETRIES + 1} 次解析失败（疑似输出超长被截断）。后续执行时请大幅精简工具调用内容。`,
      });
      return {
        stepId: step.id,
        ok: false,
        output: `工具调用 JSON 连续 ${MAX_MALFORMED_RETRIES + 1} 次解析失败（输出 ${llmText.length} 字符，疑似超长被截断）。`,
        artifacts: [],
        error: `malformed-tool-call: 输出 ${llmText.length} 字符未解析出合法工具调用`,
      };
    }

    if (!call) {
      // 理论不可达（循环内必 return 或 break）；防御性兜底
      return { stepId: step.id, ok: false, output: '内部错误：工具调用解析异常', artifacts: [], error: 'internal-parse-error' };
    }

    // 2.0 强提问意图拦截（优先于工具执行）：LLM 文字里向用户提出多个关键问题
    // 却同时/接着调用工具自跑（如提问主题后自己 fs.list 翻旧文件）。命中即
    // 暂停等用户回复；本轮工具调用不执行，答复入历史后由后续步骤重新决策。
    if (call.tool !== 'user.ask' && detectQuestionIntent(llmText)) {
      const answer = await this.askUserAndAwait(task, ctx, llmText.slice(0, 1000));
      if (answer !== null) {
        return {
          stepId: step.id,
          ok: true,
          output: `已向用户提问并收到回复：${answer.slice(0, 200)}。工具调用暂未执行，请结合回复在后续步骤重新决策。`,
          artifacts: [],
        };
      }
    }

    // 2.1 显式 user.ask 工具：在 policy 之前拦截（提问非敏感操作）。
    // 任务暂停等待用户回复，回复写入对话历史后本步骤完成、后续步骤据此继续。
    if (call.tool === 'user.ask') {
      const question = String(call.args['question'] ?? '').trim() || llmText.slice(0, 500);
      const answer = await this.askUserAndAwait(task, ctx, question);
      if (answer !== null) {
        return { stepId: step.id, ok: true, output: `用户回复：${answer}`, artifacts: [] };
      }
      return { stepId: step.id, ok: true, output: question, artifacts: [] };
    }

    // 2.2 复用历史遗留内容稿确认：office.generate 的 fromMarkdown 指向
    // 非本任务产物（工作空间历史遗留 md）时，必须征得用户同意。
    // 修复：Agent fs.list 看到旧文件后幻觉"上一步已产出内容稿"自作主张复用
    if (call.tool === 'office.generate' && String(call.args['fromMarkdown'] ?? '') !== '') {
      const fromMd = String(call.args['fromMarkdown']);
      const norm = (p: string): string => path.normalize(p).replace(/\\/g, '/').toLowerCase();
      const owned = ctx.artifacts.some((a) => a.path !== undefined && norm(a.path) === norm(fromMd));
      if (!owned) {
        const answer = await this.askUserAndAwait(
          task,
          ctx,
          `检测到内容稿 ${fromMd} 不是本任务生成的文件（可能是工作空间历史遗留）。\n- 回复「复用」：直接用它生成幻灯片\n- 回复其他内容（如新主题/新要求）：我将重新撰写内容稿再生成`
        );
        // 判定规则：否定表述优先（“不要复用/重新写”含“复用”字样但语义为拒绝，
        // 旧正则会误判同意）；仅明确的肯定答复才放行复用
        if (answer !== null) {
          const a = answer.trim();
          const negative = /(不要|不用|别|重新|改|换|拒绝|否|new|rewrite)/i.test(a);
          const affirmative = !negative && /^(复用|继续|用它|同意|可以|确定|ok|yes|use it)/i.test(a);
          if (!affirmative) {
            return {
              stepId: step.id,
              ok: false,
              output: `用户未确认复用历史内容稿（回复：${answer.slice(0, 200)}）。请根据用户回复重新撰写内容稿：先用 fs.write 写入新的 md 文件，再调用 office.generate 的 fromMarkdown 生成。`,
              artifacts: [],
              error: 'reuse-not-confirmed',
            };
          }
        }
      }
    }

    // 3. 权限检查（policy-engine 单点裁决）
    const rule = this.deps.policy.evaluate(call.tool, call.args);
    if (rule.decision === 'deny') {
      return {
        stepId: step.id,
        ok: false,
        output: `权限拒绝：${rule.reason}`,
        artifacts: [],
        error: `permission-denied: ${rule.reason}`,
      };
    }
    if (rule.decision === 'require-confirm') {
      // 任务级缓存键：工具 + 目标目录（原为 tool:reason，reason 含完整路径导致
      // 同目录不同文件名每次都弹确认——用户选"本任务都允许"后仍被反复询问）。
      // 按"工具+目录"记忆：同任务内对该目录的同类操作一律放行
      const targetDir = (() => {
        const tp = String(call.args['path'] ?? '');
        try {
          const d = path.dirname(tp);
          return path.isAbsolute(d) ? d : tp || '(none)';
        } catch {
          return '(none)';
        }
      })();
      const cacheKey = `${call.tool}:${targetDir}`;
      if (!ctx.taskAllowedTools.has(cacheKey)) {
        const request: PermissionRequest = {
          requestId: `perm-${randomId()}`,
          taskId: task.id,
          tool: call.tool,
          args: call.args,
          reason: rule.reason,
        };
        this.deps.emitter.emit({
          method: 'task/permission.request',
          params: { taskId: task.id, request },
        });
        // 权限等待与取消竞态：用户点"停止"时同时放行权限等待，避免悬挂
        const decision = await Promise.race([
          this.deps.permissionWaiter(request),
          new Promise<never>((_, rej) => {
            ctx.signal.addEventListener('abort', () => rej(new TaskCancelledError()), { once: true });
          }),
        ]);
        if (decision === 'deny') {
          return {
            stepId: step.id,
            ok: false,
            output: `用户拒绝了敏感操作：${rule.reason}`,
            artifacts: [],
            error: 'user-denied',
          };
        }
        if (decision === 'allow-always-task') {
          ctx.taskAllowedTools.add(cacheKey);
        }
        // 授权放行后：目标目录临时加入 FsGuard 授权根（workspace 外写入/生成的
        // 单次授权），后续同任务内该目录的读写不再被拦截
        const targetPath = String(call.args['path'] ?? '');
        if (targetPath) {
          try {
            const dir = path.dirname(targetPath);
            if (dir && path.isAbsolute(dir)) this.deps.fsGuard.grantDirectory(dir);
          } catch {
            /* 非文件路径工具忽略 */
          }
        }
      }
    }

    // 4. 沙箱执行工具
    const tool = this.deps.tools.get(call.tool);
    if (!tool) {
      return {
        stepId: step.id,
        ok: false,
        output: `未知工具：${call.tool}`,
        artifacts: [],
        error: `unknown-tool: ${call.tool}`,
      };
    }

    const toolCtx = {
      taskId: task.id,
      fsGuard: this.deps.fsGuard,
      workspaceRoots: task.workspaceUri ? [task.workspaceUri] : [],
      webSearch: this.deps.webSearch,
      onArtifact: (a: Artifact) => {
        ctx.artifacts.push(a);
        this.deps.emitter.emit({ method: 'task/artifact.created', params: { taskId: task.id, artifact: a } });
      },
    };
    const result = await tool.execute(call.args, toolCtx);
    log.info('工具执行完成', { tool: call.tool, ok: result.ok });

    // 5. 观察结果：写入历史供下一步决策
    ctx.history.push({
      role: 'assistant',
      content: `步骤「${step.title}」${result.ok ? '完成' : '失败'}。工具输出：\n${result.output.slice(0, 2000)}`,
    });

    return {
      stepId: step.id,
      ok: result.ok,
      output: result.output,
      artifacts: result.artifacts,
      error: result.ok ? undefined : `tool-failed: ${result.output.slice(0, 200)}`,
    };
  }

  /** 向用户提问并等待回复（user.ask 与强提问拦截共用）。
   *  问题与回复写入对话历史供后续步骤决策；无 askWaiter（未注入）返回
   *  null，调用方按无提问能力处理。取消信号触发时抛 TaskCancelledError。 */
  private async askUserAndAwait(
    task: Task,
    ctx: import('./types.js').LoopContext,
    question: string
  ): Promise<string | null> {
    if (!this.deps.askWaiter) return null;
    const answer = await Promise.race([
      this.deps.askWaiter(task.id, question),
      new Promise<never>((_, rej) => {
        ctx.signal.addEventListener('abort', () => rej(new TaskCancelledError()), { once: true });
      }),
    ]);
    ctx.history.push({ role: 'assistant', content: question.slice(0, 1200) });
    ctx.history.push({ role: 'user', content: answer });
    return answer;
  }
}

function buildStepSystemPrompt(
  task: Task,
  step: PlanStep,
  ctx: import('./types.js').LoopContext
): string {
  const toolList = [
    'fs.read', 'fs.write', 'fs.list', 'fs.delete',
    'shell.exec', 'search.web',
    'office.generate',
  ];
  return `你是 WDBuddy Agent，正在执行任务（模式：${task.mode}，权限：${task.permission}）。
工作空间：${task.workspaceUri ?? '（未指定，文件操作将被拒绝）'}
整体指令：${ctx.instruction}
计划步骤：${ctx.steps.map((s) => `${s.seq}. ${s.title} [${s.status}]`).join('；')}
可用工具：${toolList.join(', ')}

输出约定：
- 需要调用工具时，输出一个 JSON 对象（可放在 \`json 代码块中）：
  {"tool": "fs.write", "args": {"path": "绝对路径", "content": "..."}}
- 生成演示文稿【必须】使用 office.generate（秒级完成，禁止用 Python/shell 方案，禁止只写设计稿 md 文件）：
  【推荐·短JSON】先把逐页文案写入 md 内容稿（fs.write 可分多次追加，避免单次超长），
  再调用：{"tool": "office.generate", "args": {"path": "输出路径.html", "fromMarkdown": "内容稿.md路径", "theme": "主题id或auto"}}
  —— fromMarkdown 模式 JSON 极短（约100字符），绝不会被截断；系统自动把 md 转成幻灯片。
  【内容稿格式·必须逐页小节】每页一个 ## 小节，结构如下（示例）：
  ## P1 封面
  - 标题：WorkBuddy 介绍
  - 副标题：你的 AI 智能工作伙伴
  > 备注一句话讲稿，讲解本页要点
  ## P2 目录
  1. 认识产品：它是什么
  2. 核心能力：能做什么
  ## P3 核心能力
  - 徽章：02 · 核心能力
  - 🚀 任务执行：一句话搞定复杂工作
  - 💡 智能建议：结合场景给出最优解
  > 重点讲任务执行，举一个具体例子
  要点行支持行首 emoji 图标（🚀💡🎯 等）；引用行（> 开头）= 讲者备注，每页都写；
  kv 行支持：标题/副标题/徽章/版式/备注；含冒号的要点自动变卡片，4 条→四宫格、
  6 条→六宫格（3x2）、8 条→八宫格（4x2）；"传统 vs 新方式"两组冒号子块自动变对比页。
  【直传模式】小规模（≤5页）也可直接传 slides 数组：
  {"tool": "office.generate", "args": {"path": "输出路径.pptx 或 .html", "title": "总标题", "theme": "主题id或auto", "slides": [...]}}
  【输出格式】path 以 .pptx 结尾 → 仅生成可编辑 PowerPoint；以 .html 结尾 → 仅生成网页放映版。
  需要两种格式时传 formats:"html,pptx"（默认单格式，不额外产出 html）。
  【模板克隆模式】用户指定用某个"模板"（如「红蓝微立体·2026年终总结」模板）时：
  把模板名原样传给 template 参数 + path 必须 .pptx 结尾——系统自动匹配内置模板，
  视觉 100% 复刻模板风格，文字由内容稿填充，theme 参数忽略：
  {"tool": "office.generate", "args": {"path": "输出路径.pptx", "fromMarkdown": "内容稿.md路径", "template": "用户指令中的模板名"}}
  模板克隆模式下内容稿照常按上述 md 格式撰写（逐页 ## 小节），且必须额外精炼：
  【克隆文案红线】模板槽位窄（标题框常只有 2-4 英寸宽），超长文字会破坏模板排版——
  每页标题 ≤ 12 字；条目标题 ≤ 8 字；说明 ≤ 25 字；数值只写"12.6亿元"这类短值（不带长句解释）；
  目录条目每条 ≤ 10 字。宁可拆页也不塞长文。
  【主题 theme】（auto=自动匹配）：tech-dark深空科技|business-blue商务蓝|minimal-light极简白|
  warm-energy活力暖橙|academic学术庄重|red-gold汇报红金|fresh-mint清新薄荷|
  purple-elegant优雅紫|sunset-gradient日落渐变|ink-wash水墨中国
  【16 种版式 layout】（混合使用，不要全用 content）：
  cover=封面{title,subtitle} | toc=目录{items:[{label,desc}]} | section=章节页{subtitle:"01",title} |
  content=要点{title,bullets,notes} | two-col=左要点右卡片{bullets,items} | cards=三卡片{cards:[{icon,title,desc}]} |
  timeline=流程{steps:[{label,desc}]} | grid-2x2=四宫格 | grid-3x2=六宫格 | grid-4x2=八宫格{cards:[{icon,title,desc}]} |
  quote=金句{quote:{text,author}} | compare=对比{compare:{leftLabel,leftPoints,rightLabel,rightPoints}} |
  big-number=数据大屏{metrics:[{value,label,desc}]} |
  chart=图表{chart:{type:bar/pie/line,series:[{name,labels,values}]}} | image-text=图文{image:{side,caption},bullets} |
  closing=结尾{title,subtitle}
  【内容质量红线】严禁空泛口号（如"智能高效、提升效率"无信息量）；每个要点必须有具体信息
  （做什么/怎么做/效果数字）；能力≥4 项用宫格页、能力 3 项用 cards、流程用 timeline、
  新旧方式用 compare、数字效果用 big-number；每页写 notes 讲者备注（未写则系统自动生成）。
  slides 一次传全部页面，但每页内容必须精炼（bullets ≤ 4 条、每条 ≤ 20 字），
  严禁堆砌长句——整段 JSON 超长会被截断导致工具调用失败；
  路径在工作空间外没关系（系统会请求确认）。
  生成完成后必须用真实输出汇报文件路径，严禁谎报成功。
- 需要用户补充信息（主题、受众、偏好等关键决策）时，【必须】输出工具调用：
  {"tool": "user.ask", "args": {"question": "一句话概括的问题"}}
  任务将暂停等待用户回复后再继续；严禁自行假设用户答案往下执行；
  严禁只在文字里提问后继续调用其他工具自跑（系统检测到提问意图会强制暂停等回复）
- 工作空间中已存在的文件可能是历史任务遗留（非本任务产出）。基于此类文件
  生成最终交付物（尤其 office.generate 的 fromMarkdown 复用旧内容稿）前，
  必须先经用户确认（user.ask），严禁自作主张复用；
  本任务自己的产物以对话历史中"已写入 xxx"的记录为准，不可凭空声称某文件是本任务产出
- 写入大文件（超过约 200 行）时必须拆分：分多次 fs.write 追加或拆成多个文件，
  避免单次超长输出（极慢且易截断）
- 无需工具时直接输出给用户看的文字说明。`;
}
