/**
 * template.from_image — 图片 → PPT 克隆模板（视觉链路入口工具）
 *
 * 流程：读图（base64）→ 视觉模型提取 DesignSpec（JSON）→ 校验归一化
 * → python-pptx 渲染为模板 pptx → 引擎级自检（scanTemplate 槽位角色）
 * → 注册到用户模板目录（模版库可见、office.generate template 参数可用）。
 *
 * 视觉模型调用经 ToolContext.visionChat 注入（Main 配置 VisionConfig）；
 * 未配置时给出明确指引（引导用户到设置页配置视觉模型）。
 */
import * as path from 'node:path';
import * as fs from 'node:fs';
import * as os from 'node:os';
import { createHash } from 'node:crypto';
import type { AgentTool, ToolContext, ToolResult } from './registry.js';
import { makeArtifact } from './registry.js';
import { buildSpecPrompt, parseSpecFromText } from './pptx/design-spec.js';
import { renderSpecToPptx } from './pptx/spec-renderer.js';
import { registerUserTemplate, resolveCloneTemplate } from './pptx/clone-registry.js';
import { scanTemplate } from './pptx/template-clone.js';
import { invalidateTemplatesPreviewCache } from './templates-preview.js';
import type { DesignSpec } from '@wdbuddy/shared-types';

function sha256(data: Buffer | string): string {
  return createHash('sha256').update(data).digest('hex');
}

/** 工作空间根（响应落盘位置；无工作空间时退化为系统临时目录） */
function workspaceRootOf(ctx: ToolContext): string {
  return ctx.workspaceRoots[0] ?? os.tmpdir();
}

/** 图片 mime 推断（data URL 组装用） */
function imageMime(p: string): string {
  const ext = path.extname(p).toLowerCase();
  if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg';
  if (ext === '.webp') return 'image/webp';
  if (ext === '.gif') return 'image/gif';
  if (ext === '.bmp') return 'image/bmp';
  return 'image/png';
}

export const templateFromImageTool: AgentTool = {
  name: 'template.from_image',
  description:
    '把一张幻灯片设计参考图（jpg/png/webp）转化为可复用的 PPT 克隆模板：视觉模型分析图片的色板/版式/槽位几何，程序化重建为 .pptx 模板并注册到模版库。参数：image（图片绝对路径）、name（可选，模板名，缺省由视觉模型命名）。产物直接用于 office.generate 的 template 参数。前置条件：设置中已配置视觉模型（Vision Provider）。图片建议为单页或几页的幻灯片设计图',
  sensitivity: 'write',
  async execute(args, ctx: ToolContext): Promise<ToolResult> {
    const image = typeof args['image'] === 'string' ? (args['image'] as string).trim() : '';
    if (!image) {
      return { ok: false, output: '缺少 image 参数（设计参考图的绝对路径）', artifacts: [] };
    }
    // 视觉模型注入检查
    if (!ctx.visionChat) {
      return {
        ok: false,
        output:
          '视觉模型未配置。请在 设置 → 模型 中配置视觉模型（Vision Provider：baseUrl / apiModel / apiKey，需为 OpenAI 兼容的多模态端点，如 GLM-4V / Kimi-vision / GPT-4o）。配置后重启任务即可使用 template.from_image。',
        artifacts: [],
      };
    }

    // 1) 读图（fsGuard 允许 full 权限或工作空间内；default 权限工作空间外读
    //    会被拒——提示用户把图放进工作空间或开 full）
    let buf: Buffer;
    try {
      const data = ctx.fsGuard.readFile(image, 'binary') as unknown as string;
      buf = Buffer.from(data, 'binary');
    } catch (err) {
      return {
        ok: false,
        output: `读取图片失败：${String(err)}。若图片在工作空间外，请把它复制到工作空间内，或使用完全访问权限`,
        artifacts: [],
      };
    }
    if (buf.length < 1000) {
      return { ok: false, output: `图片文件过小（${buf.length} 字节），疑似路径错误或文件损坏`, artifacts: [] };
    }
    if (buf.length > 10 * 1024 * 1024) {
      return { ok: false, output: `图片过大（${Math.round(buf.length / 1024 / 1024)}MB，上限 10MB），请压缩后重试`, artifacts: [] };
    }
    const dataUrl = `data:${imageMime(image)};base64,${buf.toString('base64')}`;

    // 2) 视觉模型提取 DesignSpec（最多 2 次重试：JSON 解析失败反馈重试）。
    //    每次原始响应都落盘存档（workspace/vision-response-*.md）：失败时
    //    Agent/用户可直接读原始输出排查，不再"黑盒报错+去工作空间猜文件"
    //    （10/16 事故：Agent 找不到原始响应，被开发残留 .txt 误导空转 1 小时）
    const userName = typeof args['name'] === 'string' ? (args['name'] as string).trim() : '';
    let spec: DesignSpec | null = null;
    let lastErr = '';
    let lastRawFile = '';
    for (let attempt = 0; attempt < 2; attempt++) {
      const extra = attempt === 0 ? '' : `\n上次输出解析失败：${lastErr.slice(0, 300)}。请严格按 JSON 格式重新输出：直接以 { 开头、} 结尾，不要 markdown 围栏、不要注释、字符串内不要出现未转义引号，不要遗漏必填字段。`;
      const text = await ctx.visionChat(
        [
          { role: 'system', content: buildSpecPrompt() },
          {
            role: 'user',
            content: `请分析这张幻灯片设计图，输出 DesignSpec JSON。${userName ? `模板名固定用「${userName}」。` : ''}${extra}`,
            images: [dataUrl],
          },
        ],
        { maxTokens: 8192 }
      );
      // 落盘存档（工作空间根，一次一份带时间戳；fsGuard 写入失败不阻断主流程）
      const stamp = Date.now().toString(36);
      const dumpPath = path.join(workspaceRootOf(ctx), `vision-response-${stamp}.md`);
      try {
        ctx.fsGuard.writeFile(
          dumpPath,
          `# 视觉模型原始响应（template.from_image 第 ${attempt + 1} 次调用）\n\n图片：${image}\n\n---\n\n${text}\n`
        );
        lastRawFile = dumpPath;
      } catch {
        /* 落盘失败不阻断：报错信息里已含 500 字符预览 */
      }
      try {
        spec = parseSpecFromText(text);
        break;
      } catch (err) {
        lastErr = String(err instanceof Error ? err.message : err);
      }
    }
    if (!spec) {
      return {
        ok: false,
        output: [
          `视觉模型输出解析失败（重试 2 次）：${lastErr}`,
          lastRawFile
            ? `原始响应已存档：${lastRawFile}（可直接 fs.read 查看模型到底输出了什么；若为纯文字描述而非 JSON，说明该模型不适合此任务，请建议用户更换视觉模型，如 glm-4v-plus / gpt-4o / qwen-vl-plus）`
            : '（原始响应落盘失败，见上方 500 字符预览）',
          '排查指引：① 读原始响应确认模型输出形态；② 若模型反复输出长文字说明，属模型能力不匹配，不要在本地反复解析——建议用户换用视觉模型后重试。',
        ].join('\n'),
        artifacts: [],
      };
    }
    if (spec.slides.length === 0) {
      return {
        ok: false,
        output: `视觉模型判定图片非幻灯片设计图（vibe：${spec.vibe ?? '无'}）。请提供单页或多页幻灯片设计参考图`,
        artifacts: [],
      };
    }

    // 3) 渲染为模板 pptx（python-pptx；产物落临时目录，注册后进用户模板目录）
    const tmpOut = path.join(os.tmpdir(), `wd_tplimg_${Date.now()}.pptx`);
    const render = renderSpecToPptx(spec, tmpOut);
    if (!render.ok) {
      return { ok: false, output: `模板渲染失败（python 侧）：${render.output}`, artifacts: [] };
    }

    // 4) 引擎级自检：scanTemplate 确认可被克隆引擎识别
    const scan = scanTemplate(tmpOut);
    if (!scan.ok || scan.pages.length === 0) {
      try { fs.unlinkSync(tmpOut); } catch { /* */ }
      return { ok: false, output: `渲染产物未通过克隆引擎自检：${scan.error ?? '无可用页面'}。${render.output.slice(0, 500)}`, artifacts: [] };
    }

    // 5) 注册到用户模板目录（模版库可见 + template 参数可引用）
    const tags = ['图片生成', '自定义', ...spec.colors.slice(0, 3).map((c) => c.usage ?? '色板')].slice(0, 6);
    const reg = registerUserTemplate({
      pptxPath: tmpOut,
      name: userName || spec.name,
      vibe: spec.vibe ?? '由图片生成的自定义模板',
      tags,
    });
    if (!reg.ok) {
      try { fs.unlinkSync(tmpOut); } catch { /* */ }
      return { ok: false, output: `模板注册失败：${reg.error}`, artifacts: [] };
    }
    invalidateTemplatesPreviewCache();

    // 6) 模板文件本体也作为 artifact 交付（预览/归档）
    const tplBuf = fs.readFileSync(reg.file);
    const art = makeArtifact(
      ctx.taskId,
      'file',
      reg.file,
      'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      sha256(tplBuf)
    );
    ctx.onArtifact?.(art);
    try { fs.unlinkSync(tmpOut); } catch { /* */ }

    // 7) 输出摘要（模板 id / 引用方式 / 版式构成）
    const kinds = [...new Set(scan.pages.map((p) => p.kind))];
    const colors = spec.colors.slice(0, 5).map((c) => c.hex).join(' ');
    return {
      ok: true,
      output: [
        `已从图片生成 PPT 克隆模板「${userName || spec.name}」并注册到模版库：`,
        `  📄 模板文件：${reg.file}（${tplBuf.length} 字节，${scan.pages.length} 页）`,
        `  🎨 色板：${colors}`,
        `  🧩 版式：${kinds.join(' / ')}`,
        `  ▶ 使用方式：office.generate 传 template="${reg.id}"（或模板名），即可用此模板克隆生成演示文稿`,
        `  自检：${render.output.includes('SELF_CHECK PASSED') ? '通过' : '通过（含警告）'}`,
      ].join('\n'),
      artifacts: [art],
    };
  },
};
