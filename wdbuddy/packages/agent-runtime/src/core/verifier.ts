/**
 * Verifier（§10.3）
 * 网页跑渲染检查（静态语法校验）、代码跑 lint 粗检、文档做结构校验；报告随交付物展示。
 */
import type { Artifact, Task, VerifyCheck, VerifyReport } from '@wdbuddy/shared-types';
import type { FsGuard } from '@wdbuddy/sandbox';
import type { Logger } from '../logger.js';

export class Verifier {
  constructor(
    private readonly fsGuard: FsGuard,
    private readonly logger: Logger
  ) {}

  async selfCheck(task: Task, artifacts: Artifact[]): Promise<VerifyReport> {
    const checks: VerifyCheck[] = [];

    // 1. HTML 渲染检查：基本结构完整性
    const htmlArts = artifacts.filter((a) => a.mimeType === 'text/html' && a.path);
    for (const art of htmlArts) {
      checks.push(this.checkHtml(art));
    }

    // 2. 代码类：括号配对粗检
    const codeExts = ['.js', '.ts', '.css', '.json'];
    for (const art of artifacts.filter((a) => a.path && codeExts.some((e) => a.path!.endsWith(e)))) {
      checks.push(this.checkCode(art));
    }

    // 3. 文档结构：非空检查
    for (const art of artifacts.filter((a) => a.kind === 'file' && !a.path?.endsWith('.html'))) {
      checks.push(this.checkFileNonEmpty(art));
    }

    // 4. 至少一项检查：无产物时给出提示
    if (checks.length === 0) {
      checks.push({ name: '产物检查', passed: true, detail: '本任务无文件产物（问答类任务）' });
    }

    return {
      taskId: task.id,
      checks,
      passed: checks.every((c) => c.passed),
      finishedAt: Date.now(),
    };
  }

  /** 意图-产物匹配（交付前防假成功）：任务要求生成 PPT/演示文稿，但产物中
   *  没有任何 .pptx/.html 幻灯片文件（如只交付了 md 设计稿）时返回缺口描述。
   *  逐条独立判定（标题/指令/各条用户消息按行拆分）：任何一行同时命中"演示
   *  类名词 + 生成动词、且非疑问、非纯文字子任务"即视为交付型诉求。
   *  严禁拼接整段后判定——真实会话中用户回复会引用 Agent 的提问（含"？"），
   *  拼接后 questionLike 会误杀整段，导致补救机制漏触发（假成功复现）。 */
  intentGap(
    task: Task,
    instruction: string,
    artifacts: Artifact[]
  ): { title: string; detail: string } | null {
    const lines = `${task.title}\n${instruction}`
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.length > 0);
    const wantsDeck = (l: string): boolean => /(ppt|幻灯片|演示文稿|slides?)/i.test(l);
    // 生成动词：中英双语（467 事故：英文指令 "create a ppt, please" 因纯中文
    // 动词正则不命中 → 意图缺口漏判 → 补救/兜底均不触发 → 假成功复发）
    const wantsCreate = (l: string): boolean =>
      /(生成|制作|创建|做|写|导出|输出|create|make|generate|build|produce|export|design|draft|write)/i.test(l);
    // 纯文字子任务（大纲/文案/讲稿/文章等）与疑问句排除，避免误伤问答/写作类任务
    const docOnly = (l: string): boolean =>
      /(大纲|文案|讲稿|脚本|素材|模板|目录|标题|文章|博客|论文|blog)/i.test(l);
    const questionLike = (l: string): boolean => /[?？]|怎么|如何|为什么|什么/.test(l);
    const wantsDeckFile = lines.some(
      (l) => wantsDeck(l) && wantsCreate(l) && !docOnly(l) && !questionLike(l)
    );
    if (!wantsDeckFile) return null;
    const deckArts = artifacts.filter((a) => a.path !== undefined && /\.(pptx|html)$/i.test(a.path));
    if (deckArts.length > 0) return null;
    const files = artifacts.filter((a) => a.path !== undefined).map((a) => a.path as string);
    const fileList =
      files.length > 0 ? `（当前仅有：${files.slice(0, 5).join('、')}）` : '（当前没有任何文件产物）';
    return {
      title: '生成幻灯片文件',
      detail: `任务要求生成 PPT/演示文稿，但交付产物中没有 .pptx 或 .html 幻灯片文件${fileList}`,
    };
  }

  private readArt(art: Artifact): string {
    if (!art.path) return '';
    try {
      return this.fsGuard.readFile(art.path);
    } catch {
      return '';
    }
  }

  private checkHtml(art: Artifact): VerifyCheck {
    const html = this.readArt(art);
    const name = `HTML 渲染检查：${art.path ?? ''}`;
    if (!html) return { name, passed: false, detail: '无法读取文件内容' };
    const hasHtml = /<html[\s>]/i.test(html);
    const hasBody = /<body[\s>]/i.test(html);
    // 逐标签开放/闭合计数配对（旧实现先剥离全部闭合标签再匹配开放标签，
    // 任何合法 HTML 都会误报"未闭合"）
    const blockTags = ['div', 'section', 'body', 'html'];
    const unclosed = blockTags.filter((t) => {
      const open = (html.match(new RegExp(`<${t}(\\s[^>]*)?>`, 'gi')) ?? []).length;
      const close = (html.match(new RegExp(`</${t}>`, 'gi')) ?? []).length;
      return open !== close;
    });
    const problems: string[] = [];
    if (!hasHtml) problems.push('缺少 html 根标签');
    if (!hasBody) problems.push('缺少 body 标签');
    if (unclosed.length > 0) problems.push(`存在未闭合的块级标签：${unclosed.join('/')}`);
    return {
      name,
      passed: problems.length === 0,
      detail: problems.length === 0 ? '结构完整' : problems.join('；'),
    };
  }

  private checkCode(art: Artifact): VerifyCheck {
    const code = this.readArt(art);
    const name = `代码检查：${art.path ?? ''}`;
    if (!code) return { name, passed: false, detail: '无法读取文件内容' };
    const open = (code.match(/{/g) ?? []).length;
    const close = (code.match(/}/g) ?? []).length;
    const balanced = open === close;
    return {
      name,
      passed: balanced,
      detail: balanced ? `括号配对正常（${open} 对）` : `花括号不配对：{ × ${open} vs } × ${close}`,
    };
  }

  private checkFileNonEmpty(art: Artifact): VerifyCheck {
    const content = this.readArt(art);
    const name = `产物非空：${art.path ?? art.id}`;
    const ok = content.trim().length > 0;
    return { name, passed: ok, detail: ok ? `${content.trim().length} 字符` : '文件内容为空' };
  }
}
