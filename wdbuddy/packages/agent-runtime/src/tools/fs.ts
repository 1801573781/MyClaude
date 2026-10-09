/**
 * 文件系统工具（全部经 fs-guard，ADR-0004）
 */
import { createHash } from 'node:crypto';
import * as path from 'node:path';
import type { AgentTool, ToolContext, ToolResult } from './registry.js';
import { makeArtifact } from './registry.js';

function sha256(data: string | Buffer): string {
  return createHash('sha256').update(data).digest('hex');
}

function argString(args: Record<string, unknown>, key: string): string {
  const v = args[key];
  return typeof v === 'string' ? v : '';
}

/** 读取文件内容（截断保护：超出 64KB 截断） */
export const fsReadTool: AgentTool = {
  name: 'fs.read',
  description: '读取指定路径的文本文件内容',
  sensitivity: 'read-only',
  async execute(args, ctx: ToolContext): Promise<ToolResult> {
    const p = argString(args, 'path');
    if (!p) return { ok: false, output: '缺少 path 参数', artifacts: [] };
    try {
      let content = ctx.fsGuard.readFile(p);
      if (Buffer.byteLength(content, 'utf-8') > 64 * 1024) {
        content = content.slice(0, 64 * 1024) + '\n…[已截断]';
      }
      return { ok: true, output: content, artifacts: [] };
    } catch (err) {
      return { ok: false, output: `读取失败：${String(err)}`, artifacts: [] };
    }
  },
};

/** 写入文件（写前备份 → artifact 登记 → 写入） */
export const fsWriteTool: AgentTool = {
  name: 'fs.write',
  description: '将内容写入指定路径（工作空间内自动创建目录）',
  sensitivity: 'write',
  async execute(args, ctx: ToolContext): Promise<ToolResult> {
    const p = argString(args, 'path');
    const content = argString(args, 'content');
    if (!p) return { ok: false, output: '缺少 path 参数', artifacts: [] };
    try {
      // 备份原内容（若存在）→ 回滚零丢失（§12）
      const artifacts: import('@wdbuddy/shared-types').Artifact[] = [];
      try {
        const original = ctx.fsGuard.readFile(p);
        const art = makeArtifact(ctx.taskId, 'diff', p, 'text/plain', sha256(original));
        ctx.onArtifact?.(art);
        artifacts.push(art);
      } catch {
        /* 新文件无需备份 */
      }
      ctx.fsGuard.writeFile(p, content);
      const art = makeArtifact(ctx.taskId, 'file', p, guessMime(p), sha256(content));
      ctx.onArtifact?.(art);
      artifacts.push(art);
      return { ok: true, output: `已写入 ${p}（${Buffer.byteLength(content)} 字节）`, artifacts };
    } catch (err) {
      return { ok: false, output: `写入失败：${String(err)}`, artifacts: [] };
    }
  },
};

/** 列出目录内容 */
export const fsListTool: AgentTool = {
  name: 'fs.list',
  description: '列出指定目录的文件与子目录',
  sensitivity: 'read-only',
  async execute(args, ctx: ToolContext): Promise<ToolResult> {
    const p = argString(args, 'path');
    if (!p) return { ok: false, output: '缺少 path 参数', artifacts: [] };
    try {
      const entries = ctx.fsGuard.listDir(p);
      return { ok: true, output: entries.join('\n'), artifacts: [] };
    } catch (err) {
      return { ok: false, output: `列目录失败：${String(err)}`, artifacts: [] };
    }
  },
};

/** 删除文件（敏感操作，policy-engine 默认 require-confirm） */
export const fsDeleteTool: AgentTool = {
  name: 'fs.delete',
  description: '删除指定文件（敏感操作，需确认）',
  sensitivity: 'sensitive',
  async execute(args, ctx: ToolContext): Promise<ToolResult> {
    const p = argString(args, 'path');
    if (!p) return { ok: false, output: '缺少 path 参数', artifacts: [] };
    try {
      // 删前备份，支持回滚
      try {
        const original = ctx.fsGuard.readFile(p);
        const art = makeArtifact(ctx.taskId, 'diff', p, 'text/plain', sha256(original));
        ctx.onArtifact?.(art);
        ctx.fsGuard.deleteFile(p);
        return { ok: true, output: `已删除 ${p}（已备份可回滚）`, artifacts: [art] };
      } catch {
        ctx.fsGuard.deleteFile(p);
        return { ok: true, output: `已删除 ${p}`, artifacts: [] };
      }
    } catch (err) {
      return { ok: false, output: `删除失败：${String(err)}`, artifacts: [] };
    }
  },
};

function guessMime(p: string): string {
  const ext = path.extname(p).toLowerCase();
  const table: Record<string, string> = {
    '.html': 'text/html',
    '.htm': 'text/html',
    '.css': 'text/css',
    '.js': 'text/javascript',
    '.ts': 'text/typescript',
    '.json': 'application/json',
    '.md': 'text/markdown',
    '.txt': 'text/plain',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
    '.pdf': 'application/pdf',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  };
  return table[ext] ?? 'application/octet-stream';
}
