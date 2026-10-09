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
