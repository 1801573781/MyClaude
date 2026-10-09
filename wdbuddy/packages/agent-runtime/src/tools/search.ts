/**
 * Web 搜索工具（只读；具体后端由 desktop 注入，runtime 不直接依赖网络实现）
 */
import type { AgentTool, ToolResult } from './registry.js';

export const webSearchTool: AgentTool = {
  name: 'search.web',
  description: '联网搜索（深度调研用，注入检索后端）',
  sensitivity: 'read-only',
  async execute(args, ctx): Promise<ToolResult> {
    const query = typeof args['query'] === 'string' ? (args['query'] as string) : '';
    if (!query) return { ok: false, output: '缺少 query 参数', artifacts: [] };
    if (!ctx.webSearch) {
      return { ok: false, output: '搜索后端未注入', artifacts: [] };
    }
    try {
      const result = await ctx.webSearch(query);
      return { ok: true, output: result, artifacts: [] };
    } catch (err) {
      return { ok: false, output: `搜索失败：${String(err)}`, artifacts: [] };
    }
  },
};
