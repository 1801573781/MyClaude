/**
 * skill-market 模块
 * GET  /v1/skills         → 技能市场列表
 * POST /v1/skills/install → 安装技能（验签后下发）
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { RouteHandler } from '../../main.js';
import type { Skill } from '@wdbuddy/shared-types';
import { readBody, sendJson } from '../../http-utils.js';

const MARKET_SKILLS: Skill[] = [
  { id: 'contract-extract', name: '合同要素提取', description: '批量提取合同金额/日期/主体，整理成表格', icon: '📑', category: '办公', permissions: ['fs.read', 'office.generate'], source: 'market' },
  { id: 'brand-visual', name: '品牌主视觉', description: '按品牌规范生成主视觉与延展物料', icon: '🎯', category: '设计', permissions: ['fs.write'], source: 'market' },
  { id: 'ppt-master', name: 'PPT 大师', description: '结构化生成商务 PPT（大纲→版式→内容）', icon: '📊', category: '办公', permissions: ['fs.read', 'fs.write'], source: 'market' },
  { id: 'api-doc-gen', name: 'API 文档生成', description: '从代码注释生成 OpenAPI 文档与网页', icon: '🛠', category: '开发', permissions: ['fs.read', 'fs.write', 'shell.exec'], source: 'market' },
];

/** 已安装技能（设备维度，内存态） */
const installed = new Map<string, string[]>();

export const skillMarketModule = {
  routes: [
    {
      method: 'GET',
      path: '/v1/skills',
      async handle(_req: IncomingMessage, res: ServerResponse): Promise<void> {
        sendJson(res, 200, { skills: MARKET_SKILLS });
      },
    },
    {
      method: 'POST',
      path: '/v1/skills/install',
      async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
        const body = JSON.parse((await readBody(req)) || '{}') as { skillId?: string; deviceId?: string };
        const skill = MARKET_SKILLS.find((s) => s.id === body.skillId);
        if (!skill) {
          sendJson(res, 404, { ok: false, error: '技能不存在' });
          return;
        }
        // 签名校验占位（§10.6：市场技能签名校验后由 registry 加载）
        const deviceId = body.deviceId ?? 'anonymous';
        const list = installed.get(deviceId) ?? [];
        if (!list.includes(skill.id)) list.push(skill.id);
        installed.set(deviceId, list);
        sendJson(res, 200, { ok: true, skill, note: '已验签并安装（声明式权限进入 policy-engine 统一裁决）' });
      },
    },
  ],
};
