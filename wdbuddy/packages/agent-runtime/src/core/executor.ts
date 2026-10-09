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
  const patterns = [/\{[\s\S]*"tool"[\s\S]*\}/];
  for (const re of patterns) {
    const m = text.match(re);
    if (!m) continue;
    try {
      const obj = JSON.parse(m[0]) as { tool?: unknown; args?: unknown };
      if (typeof obj['tool'] === 'string' && typeof obj['args'] === 'object' && obj['args']) {
        return { tool: obj['tool'] as ToolName, args: obj['args'] as Record<string, unknown> };
      }
    } catch {
      /* continue */
    }
  }
  return null;
}

export class Executor {
  constructor(private readonly deps: ExecutorDeps) {}

  async execute(step: PlanStep, ctx: import('./types.js').LoopContext): Promise<StepResult> {
    const { task } = ctx;
    const log = this.deps.logger.child({ stepId: step.id });

    // 1. 构造步骤执行提示
    const system = buildStepSystemPrompt(task, step, ctx);
    let llmText = '';
    // 步骤入口取消检查（运行中点 ⏹ 立即中断本步）
    if (ctx.signal.aborted) throw new TaskCancelledError();
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
      const winStart = Math.max(0, llmText.length - 60);
      let safe = llmText.length;
      for (const mark of ['{', '`']) {
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
    const call = parseToolCall(llmText);
    if (!call) {
      // 无工具调用 → 纯文本回答，视为步骤产出说明
      return { stepId: step.id, ok: true, output: llmText, artifacts: [] };
    }

    // 2.1 向用户提问（user.ask）：在 policy 之前拦截（提问非敏感操作）。
    // 任务暂停等待用户回复，回复写入对话历史后本步骤完成、后续步骤据此继续。
    // 修复：此前无提问机制，LLM"说要确认"却直接自问自答往下跑
    if (call.tool === 'user.ask') {
      const question = String(call.args['question'] ?? '').trim() || llmText.slice(0, 500);
      if (this.deps.askWaiter) {
        const answer = await Promise.race([
          this.deps.askWaiter(task.id, question),
          new Promise<never>((_, rej) => {
            ctx.signal.addEventListener('abort', () => rej(new TaskCancelledError()), { once: true });
          }),
        ]);
        ctx.history.push({ role: 'assistant', content: question });
        ctx.history.push({ role: 'user', content: answer });
        return { stepId: step.id, ok: true, output: `用户回复：${answer}`, artifacts: [] };
      }
      return { stepId: step.id, ok: true, output: question, artifacts: [] };
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
  {"tool": "office.generate", "args": {"path": "输出路径.pptx 或 .html", "title": "总标题", "theme": "主题id或auto", "slides": [...]}}
  【输出格式】.html=网页幻灯片（现代视觉，浏览器放映，优先推荐）；.pptx=可编辑 PowerPoint。
  【主题 theme】（auto=自动匹配）：tech-dark深空科技|business-blue商务蓝|minimal-light极简白|
  warm-energy活力暖橙|academic学术庄重|red-gold汇报红金|fresh-mint清新薄荷|
  purple-elegant优雅紫|sunset-gradient日落渐变|ink-wash水墨中国
  【14 种版式 layout】（混合使用，不要全用 content）：
  cover=封面{title,subtitle} | toc=目录{items:[{label,desc}]} | section=章节页{subtitle:"01",title} |
  content=要点{title,bullets} | two-col=左要点右卡片{bullets,items} | cards=三卡片{cards:[{icon,title,desc}]} |
  timeline=流程{steps:[{label,desc}]} | grid-2x2=四宫格{cards:[{title,desc}]} | quote=金句{quote:{text,author}} |
  compare=对比{compare:{leftLabel,leftPoints,rightLabel,rightPoints}} | big-number=数据大屏{metrics:[{value,label,desc}]} |
  chart=图表{chart:{type:bar/pie/line,series:[{name,labels,values}]}} | image-text=图文{image:{side,caption},bullets} |
  closing=结尾{title,subtitle}
  示例：{"tool":"office.generate","args":{"path":"D:/x.html","title":"产品介绍","theme":"tech-dark","slides":[
    {"layout":"cover","title":"产品名","subtitle":"一句话定位"},
    {"layout":"toc","title":"目录","items":[{"label":"背景","desc":"为什么"}]},
    {"layout":"chart","title":"趋势","chart":{"type":"bar","series":[{"name":"销量","labels":["Q1","Q2"],"values":[100,200]}]}},
    {"layout":"closing","title":"谢谢观看"}]}}
  slides 一次传全部页面；路径在工作空间外没关系（系统会请求确认）。
  生成完成后必须用真实输出汇报文件路径，严禁谎报成功。
- 需要用户补充信息（主题、保存路径、偏好等关键决策）时，先用自然语言向用户清晰说明，
  然后输出：{"tool": "user.ask", "args": {"question": "一句话概括的问题"}}
  任务将暂停等待用户回复后再继续；严禁自行假设用户答案往下执行
- 写入大文件（超过约 200 行）时必须拆分：分多次 fs.write 追加或拆成多个文件，
  避免单次超长输出（极慢且易截断）
- 无需工具时直接输出给用户看的文字说明。`;
}
