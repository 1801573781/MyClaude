/**
 * 工具注册表（可扩展性 §13）
 * 新工具 = registry 注册 + 权限元数据，单点可见。
 */
import type { ToolName, Artifact } from '@wdbuddy/shared-types';
import type { FsGuard } from '@wdbuddy/sandbox';

export interface ToolContext {
  taskId: string;
  fsGuard: FsGuard;
  /** 工作空间根 */
  workspaceRoots: readonly string[];
  /** 网络搜索注入（search.web） */
  webSearch?: (query: string) => Promise<string>;
  /** artifact 登记（每次写文件前先备份登记） */
  onArtifact?: (artifact: Artifact) => void;
}

export interface ToolResult {
  ok: boolean;
  output: string;
  artifacts: Artifact[];
}

export interface AgentTool {
  name: ToolName;
  description: string;
  /** 权限元数据：该工具默认敏感级别（policy-engine 裁决参考） */
  sensitivity: 'read-only' | 'write' | 'sensitive';
  execute(args: Record<string, unknown>, ctx: ToolContext): Promise<ToolResult>;
}

export class ToolRegistry {
  private readonly tools = new Map<ToolName, AgentTool>();

  register(tool: AgentTool): void {
    this.tools.set(tool.name, tool);
  }

  get(name: ToolName): AgentTool | undefined {
    return this.tools.get(name);
  }

  has(name: ToolName): boolean {
    return this.tools.has(name);
  }

  list(): AgentTool[] {
    return [...this.tools.values()];
  }
}

/** 构造 artifact 记录（sha256 由调用方计算注入） */
export function makeArtifact(
  taskId: string,
  kind: Artifact['kind'],
  path: string | undefined,
  mimeType: string,
  sha256: string,
  version = 1
): Artifact {
  return {
    id: `art-${randomId()}`,
    taskId,
    kind,
    path,
    mimeType,
    version,
    sha256,
    createdAt: Date.now(),
  };
}

export function randomId(prefix = ''): string {
  const rand = Math.random().toString(36).slice(2, 10);
  const ts = Date.now().toString(36);
  return `${prefix}${ts}-${rand}`;
}
