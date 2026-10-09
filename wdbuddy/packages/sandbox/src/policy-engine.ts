/**
 * policy-engine — 策略引擎（ADR-0004）
 *
 * 输入 (tool, args, permissionLevel) → allow | deny | require-confirm
 * 纯函数、无副作用、100% 单测覆盖。
 * 所有工具的权限检查统一收敛于此，不散落各工具，单点强制、不可绕过。
 */
import type {
  PermissionContext,
  PolicyDecision,
  ToolName,
} from '@wdbuddy/shared-types';

export interface PolicyRule {
  tool: ToolName;
  decision: PolicyDecision;
  reason: string;
}

/** 敏感 shell 命令黑名单（正则，用于 deny） */
const DANGEROUS_SHELL_PATTERNS: readonly RegExp[] = [
  /rm\s+-rf?\s+[/~]/i,          // 递归删除根/家目录
  /mkfs(\.|\s)/i,               // 格式化磁盘
  /dd\s+if=/i,                  // 裸写磁盘
  /:\(\)\{\s*:\|:&\s*\};:/,     // fork 炸弹
  /shutdown|reboot|halt/i,       // 关机重启
  /format\s+[a-z]:/i,           // Windows 格式化
  /del\s+\/[sq]/i,              // Windows 静默删除
  /rd\s+\/s/i,                  // Windows 递归删除
  />\s*\/dev\/sd[a-z]/i,        // 写物理设备
  /chmod\s+-R\s+777\s+\//i,     // 全盘提权
];

/** 只读工具白名单（默认权限下直接放行） */
const READ_ONLY_TOOLS: ReadonlySet<ToolName> = new Set(['fs.read', 'fs.list', 'search.web']);

export interface PolicyEngineOptions {
  /** AppSettings.permissions 镜像 */
  confirmBeforeShell: boolean;
  confirmBeforeWriteOutsideWorkspace: boolean;
}

export const DEFAULT_POLICY_OPTIONS: PolicyEngineOptions = {
  confirmBeforeShell: true,
  confirmBeforeWriteOutsideWorkspace: true,
};

/**
 * 裁决工具调用。
 *
 * 判定顺序：
 * 1. 参数非法 → deny
 * 2. 危险 shell 命令 → deny（任何权限级别都拒绝）
 * 3. 完全访问权限（full）→ 非危险操作全部 allow
 * 4. 默认权限（default）：
 *    - 只读工具 → allow
 *    - shell.exec → confirmBeforeShell 决定 require-confirm / allow
 *    - fs.write 到工作空间外 → require-confirm
 *    - 其余写操作（工作空间内）→ allow
 *    - fs.delete → require-confirm
 */
export function evaluate(
  ctx: PermissionContext,
  opts: PolicyEngineOptions = DEFAULT_POLICY_OPTIONS
): PolicyRule {
  const { tool, args, permissionLevel } = ctx;

  // 1. 参数基本合法性
  if (!args || typeof args !== 'object') {
    return { tool, decision: 'deny', reason: '参数缺失或非法' };
  }

  // 2. 危险 shell 命令：任何权限级别一律拒绝
  if (tool === 'shell.exec') {
    const cmd = typeof args['command'] === 'string' ? (args['command'] as string) : '';
    if (!cmd) {
      return { tool, decision: 'deny', reason: 'shell.exec 缺少 command 参数' };
    }
    if (DANGEROUS_SHELL_PATTERNS.some((re) => re.test(cmd))) {
      return { tool, decision: 'deny', reason: `命中危险命令黑名单：${cmd.slice(0, 80)}` };
    }
  }

  if (tool === 'browser.open') {
    const url = typeof args['url'] === 'string' ? (args['url'] as string) : '';
    if (!/^https?:\/\//i.test(url)) {
      return { tool, decision: 'deny', reason: '仅允许 http(s) 协议的 URL' };
    }
  }

  // 3. 完全访问：非危险操作直接放行
  if (permissionLevel === 'full') {
    return { tool, decision: 'allow', reason: '完全访问权限' };
  }

  // 4. 默认权限分级裁决
  if (READ_ONLY_TOOLS.has(tool)) {
    return { tool, decision: 'allow', reason: '只读工具' };
  }

  switch (tool) {
    case 'shell.exec':
      return opts.confirmBeforeShell
        ? { tool, decision: 'require-confirm', reason: '默认权限下执行 shell 命令需要确认' }
        : { tool, decision: 'allow', reason: '设置已关闭 shell 确认' };

    case 'fs.write': {
      const target = typeof args['path'] === 'string' ? (args['path'] as string) : '';
      const outside = !isInsideWorkspace(target, ctx.workspaceRoots);
      if (outside) {
        return opts.confirmBeforeWriteOutsideWorkspace
          ? {
              tool,
              decision: 'require-confirm',
              reason: '写入工作空间外的文件需要确认',
            }
          : { tool, decision: 'allow', reason: '设置已关闭工作空间外写入确认' };
      }
      return { tool, decision: 'allow', reason: '工作空间内写入' };
    }

    case 'fs.delete':
      return { tool, decision: 'require-confirm', reason: '删除文件属于敏感操作，需要确认' };

    case 'office.generate': {
      // 生成类工具：输出路径在工作空间外时需用户确认（用户明确指定的保存位置，
      // 如 D:/workbuddy_intro.pptx——点允许即临时授权该目录，修复"根目录必被拒"）
      const target = typeof args['path'] === 'string' ? (args['path'] as string) : '';
      const outside = !isInsideWorkspace(target, ctx.workspaceRoots);
      if (outside) {
        return {
          tool,
          decision: 'require-confirm',
          reason: `输出文件位于工作空间外：${target}（允许后将保存到该位置）`,
        };
      }
      return { tool, decision: 'allow', reason: '生成类工具（工作空间内）' };
    }

    case 'search.web':
      return { tool, decision: 'allow', reason: '搜索类工具' };

    default:
      return { tool, decision: 'deny', reason: `未知工具：${String(tool)}` };
  }
}

/** 粗判路径是否在工作空间内（policy-engine 层不解析符号链接，精确检查在 fs-guard） */
function isInsideWorkspace(p: string, roots: readonly string[]): boolean {
  if (!p) return false;
  if (roots.length === 0) return false;
  // 简化判断：以任一 root 为前缀（含分隔符边界），用于决策分派
  const norm = p.replace(/\\/g, '/');
  return roots.some((r) => {
    const rn = r.replace(/\\/g, '/').replace(/\/+$/, '');
    return norm === rn || norm.startsWith(rn + '/');
  });
}

/** 沙箱策略集接口（Agent Runtime Executor 依赖注入用） */
export interface SandboxPolicy {
  evaluate(tool: ToolName, args: Record<string, unknown>): PolicyRule;
}

/** 为指定任务生成沙箱策略集（Agent Runtime Executor 使用） */
export function policyFor(
  permissionLevel: PermissionContext['permissionLevel'],
  workspaceRoots: readonly string[],
  opts: PolicyEngineOptions = DEFAULT_POLICY_OPTIONS
): SandboxPolicy {
  return {
    evaluate: (tool, args) => evaluate({ tool, args, permissionLevel, workspaceRoots }, opts),
  };
}
