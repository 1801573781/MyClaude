/**
 * fs-guard — 文件守卫（ADR-0004：所有文件 API 必经此层）
 *
 * 职责：
 * 1. 路径规范化（统一分隔符、消解 ..、编码归一）
 * 2. 符号链接解析后必须仍在授权根内（防软链接逃逸）
 * 3. 系统敏感目录黑名单
 * 4. 写入工作空间外触发确认策略（由 policy-engine 裁决）
 */
import * as path from "node:path";
import * as fs from "node:fs";
import type { PermissionLevel } from "@wdbuddy/shared-types";

export interface FsGuardOptions {
  /** 授权工作空间根目录（绝对路径；运行期可经 grantDirectory 追加单次授权目录） */
  workspaceRoots: string[];
  /** 权限级别 */
  permissionLevel: PermissionLevel;
}

export type FsGuardVerdict =
  | { allowed: true; normalizedPath: string }
  | { allowed: false; reason: FsGuardDenyReason; normalizedPath: string };

export type FsGuardDenyReason =
  | 'outside-workspace'
  | 'symlink-escape'
  | 'sensitive-dir'
  | 'invalid-path';

/** 系统敏感目录黑名单（按平台前缀匹配）。
 *  末两项为开发期防护：Agent 运行时严禁写自身实现源码（取证实锤：Agent 曾在
 *  PPT 任务中遇 import 报错后自建"修复模块导入"任务，用 shell 旁路改写
 *  agent-runtime 源码导致"修复被还原"假象——测试中的 Agent 会自我"治疗"，
 *  必须物理隔离）。 */
const SENSITIVE_DIR_PATTERNS: readonly string[] = [
  '/etc',
  '/usr',
  '/bin',
  '/sbin',
  '/var',
  '/boot',
  '/dev',
  '/proc',
  '/sys',
  'c:\\windows',
  'c:\\program files',
  'c:\\program files (x86)',
  'c:\\programdata',
  'c:\\users\\all users',
  'c:\\$recycle.bin',
  'd:\\ai\\myclaude\\wdbuddy\\packages',
  'd:\\ai\\myclaude\\wdbuddy\\apps',
  'd:\\ai\\myclaude\\src',
];

/** 规范化路径：统一为平台原生分隔符并消解相对片段 */
export function normalizePath(p: string): string {
  if (typeof p !== 'string' || p.length === 0) return '';
  const expanded = p.replace(/\//g, path.sep).replace(/\\/g, path.sep);
  return path.normalize(expanded);
}

/** 判断规范化路径是否命中系统敏感目录黑名单（大小写不敏感，仅 Windows） */
export function isSensitiveDir(normalizedPath: string): boolean {
  const lower = normalizedPath.toLowerCase();
  return SENSITIVE_DIR_PATTERNS.some((dir) => lower === dir || lower.startsWith(dir + path.sep));
}

/** 判断目标路径（已是绝对路径）是否落在任一授权根内 */
export function isInsideRoots(absPath: string, roots: readonly string[]): boolean {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  return roots.some((root) => {
    const r = normalizePath(root);
    if (!path.isAbsolute(r)) return false;
    const rel = path.relative(r, absPath);
    return rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel));
  });
}

/**
 * 解析符号链接链：返回真实绝对路径。
 * 若链中任一环节不存在，则解析到最后一个存在的前缀目录后再拼接剩余部分。
 */
export function resolveSymlinks(absPath: string): string {
  let current = absPath;
  // 限制迭代次数防循环链接
  for (let i = 0; i < 40; i++) {
    let stats: fs.Stats;
    try {
      stats = fs.lstatSync(current);
    } catch {
      break; // 不存在 → 保留原路径（后续由写入方决定是否创建）
    }
    if (!stats.isSymbolicLink()) break;
    let linkTarget: string;
    try {
      linkTarget = fs.readlinkSync(current);
    } catch {
      break;
    }
    const resolved = path.isAbsolute(linkTarget) ? linkTarget : path.join(path.dirname(current), linkTarget);
    current = resolved;
  }
  return current;
}

/**
 * 核心裁决：检查目标路径是否允许访问。
 * @param targetPath 待访问路径（相对或绝对）
 * @param opts 守卫选项
 * @param kind 访问类型：read/list 只需在工作空间内或为完全访问；write 额外做符号链接逃逸检查
 */
export function checkPath(
  targetPath: string,
  opts: FsGuardOptions,
  kind: 'read' | 'list' | 'write' | 'delete'
): FsGuardVerdict {
  const normalized = normalizePath(targetPath);

  if (normalized === '' || (!path.isAbsolute(normalized) && !targetPath.startsWith('~/'))) {
    return { allowed: false, reason: 'invalid-path', normalizedPath: normalized };
  }

  // 展开家目录前缀
  let absPath = normalized;
  if (absPath.startsWith('~/')) {
    absPath = path.join(getHomeDir(), absPath.slice(2));
  }

  if (!path.isAbsolute(absPath)) {
    return { allowed: false, reason: 'invalid-path', normalizedPath: absPath };
  }

  // 1. 系统敏感目录黑名单
  if (isSensitiveDir(absPath)) {
    return { allowed: false, reason: 'sensitive-dir', normalizedPath: absPath };
  }

  const insideWorkspace = isInsideRoots(absPath, opts.workspaceRoots);
  const fullAccess = opts.permissionLevel === 'full';

  // 2. 工作空间外访问：完全访问放行（写入由 policy-engine 二次裁决），否则拒绝
  if (!insideWorkspace && !fullAccess) {
    return { allowed: false, reason: 'outside-workspace', normalizedPath: absPath };
  }

  // 3. 写/删类操作：符号链接解析后必须仍在授权范围内（防逃逸）
  if (kind === 'write' || kind === 'delete') {
    const realPath = resolveSymlinks(absPath);
    if (!isInsideRoots(realPath, opts.workspaceRoots) && !fullAccess) {
      return { allowed: false, reason: 'symlink-escape', normalizedPath: realPath };
    }
    // 即使完全访问，符号链接解析后也不得落入敏感目录
    if (isSensitiveDir(realPath)) {
      return { allowed: false, reason: 'sensitive-dir', normalizedPath: realPath };
    }
    return { allowed: true, normalizedPath: realPath };
  }

  return { allowed: true, normalizedPath: absPath };
}

/** 家目录（懒加载，避免测试环境无 HOME 报错） */
function getHomeDir(): string {
  return process.env['USERPROFILE'] ?? process.env['HOME'] ?? process.cwd();
}

// ───────────────────── 受保护的文件操作（工具层唯一入口） ─────────────────────

export class FsGuard {
  constructor(private readonly opts: FsGuardOptions) {}

  /**
   * 单次授权目录（用户在权限确认中点了"允许"的目标目录）：
   * 临时加入授权根，后续本任务内对其读写不再被拦。
   * 用于"用户明确指定的输出路径（如 D:/xx.pptx）落在 workspace 外"的场景——
   * policy-engine 弹确认 → 用户允许 → Runtime 调用此方法 → 工具层放行。
   */
  grantDirectory(dir: string): void {
    const normalized = normalizePath(dir);
    if (!normalized || !path.isAbsolute(normalized)) return;
    if (isInsideRoots(normalized, this.opts.workspaceRoots)) return;
    this.opts.workspaceRoots = [...this.opts.workspaceRoots, normalized];
  }

  /** 更新权限级别（如用户在任务中临时提升） */
  withPermissionLevel(level: PermissionLevel): FsGuard {
    return new FsGuard({ ...this.opts, permissionLevel: level });
  }

  private assert(
    targetPath: string,
    kind: 'read' | 'list' | 'write' | 'delete'
  ): string {
    const verdict = checkPath(targetPath, this.opts, kind);
    if (!verdict.allowed) {
      throw new FsGuardError(verdict.reason, verdict.normalizedPath);
    }
    return verdict.normalizedPath;
  }

  readFile(targetPath: string, encoding: BufferEncoding = 'utf-8'): string {
    return fs.readFileSync(this.assert(targetPath, 'read'), encoding);
  }

  writeFile(targetPath: string, data: string | Buffer): void {
    const p = this.assert(targetPath, 'write');
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, data);
  }

  appendFile(targetPath: string, data: string | Buffer): void {
    const p = this.assert(targetPath, 'write');
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.appendFileSync(p, data);
  }

  listDir(targetPath: string): string[] {
    return fs.readdirSync(this.assert(targetPath, 'list'));
  }

  stat(targetPath: string): fs.Stats {
    return fs.statSync(this.assert(targetPath, 'read'));
  }

  exists(targetPath: string): boolean {
    try {
      this.assert(targetPath, 'read');
      return fs.existsSync(normalizePath(targetPath));
    } catch {
      return false;
    }
  }

  deleteFile(targetPath: string): void {
    const p = this.assert(targetPath, 'delete');
    if (fs.existsSync(p)) fs.rmSync(p, { force: true });
  }

  deleteDir(targetPath: string): void {
    const p = this.assert(targetPath, 'delete');
    if (fs.existsSync(p)) fs.rmSync(p, { force: true, recursive: true });
  }
}

export class FsGuardError extends Error {
  constructor(
    public readonly reason: FsGuardDenyReason,
    public readonly path: string
  ) {
    super(`[fs-guard] 访问被拒绝：${reason}（路径：${path}）`);
    this.name = 'FsGuardError';
  }
}
