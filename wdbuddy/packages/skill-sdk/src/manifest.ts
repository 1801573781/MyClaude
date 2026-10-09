/**
 * 技能系统（§10.6）
 * 技能 = manifest（id/name/权限声明/触发词）+ entry
 * 声明式权限进入 policy-engine 统一裁决；市场技能签名校验；兼容 OpenClaw 生态。
 */
import type { Skill, ToolName } from '@wdbuddy/shared-types';

/** 技能 manifest（磁盘形态：skills/<id>/manifest.json） */
export interface SkillManifest {
  /** 技能 ID：小写字母数字与连字符 */
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  /** 声明式权限：本技能需要用到的工具 */
  permissions: ToolName[];
  /** 触发词：Composer 中以 / 触发或自然语言匹配 */
  triggers: string[];
  /** 入口文件：相对 manifest 的路径 */
  entry: string;
  /** 来源 */
  source: Skill['source'];
  /** OpenClaw 兼容：生态标识 */
  ecosystem?: 'openclaw' | 'wdbuddy';
  /** 市场技能：签名（Ed25519 base64） */
  signature?: string;
  /** 版本 */
  version: string;
}

/** 内置技能清单（FR-17：分类/搜索/一点即用） */
export const BUILTIN_SKILLS: readonly SkillManifest[] = [
  {
    id: 'weekly-report',
    name: '周报生成',
    description: '把销售/运营数据自动整理成周报 PPT',
    icon: '📊',
    category: '办公',
    permissions: ['fs.read', 'fs.list', 'office.generate'],
    triggers: ['周报', 'weekly report'],
    entry: './entry.md',
    source: 'builtin',
    ecosystem: 'wdbuddy',
    version: '1.0.0',
  },
  {
    id: 'data-analysis',
    name: '数据分析',
    description: '读取表格数据，清洗并生成分析图表',
    icon: '📈',
    category: '办公',
    permissions: ['fs.read', 'fs.write', 'office.generate'],
    triggers: ['数据分析', '图表'],
    entry: './entry.md',
    source: 'builtin',
    ecosystem: 'wdbuddy',
    version: '1.0.0',
  },
  {
    id: 'web-builder',
    name: '网页开发',
    description: '从零构建可运行的响应式网页（PC + 手机）',
    icon: '🌐',
    category: '开发',
    permissions: ['fs.read', 'fs.write', 'shell.exec'],
    triggers: ['网页', '网站', '页面'],
    entry: './entry.md',
    source: 'builtin',
    ecosystem: 'wdbuddy',
    version: '1.0.0',
  },
  {
    id: 'deep-research',
    name: '深度调研',
    description: '多轮检索-阅读-汇总，输出带来源的调研报告',
    icon: '🔍',
    category: '办公',
    permissions: ['search.web', 'fs.write'],
    triggers: ['调研', '研究报告'],
    entry: './entry.md',
    source: 'builtin',
    ecosystem: 'wdbuddy',
    version: '1.0.0',
  },
  {
    id: 'poster-design',
    name: '海报设计',
    description: '生成主视觉海报（品牌/节日/活动）',
    icon: '🎨',
    category: '设计',
    permissions: ['fs.write'],
    triggers: ['海报', '主视觉'],
    entry: './entry.md',
    source: 'builtin',
    ecosystem: 'wdbuddy',
    version: '1.0.0',
  },
];

/** manifest 校验：结构完整性 + ID 规范 */
export function validateManifest(m: unknown): { ok: true; manifest: SkillManifest } | { ok: false; errors: string[] } {
  const errors: string[] = [];
  const o = (m ?? {}) as Partial<SkillManifest>;
  if (typeof o['id'] !== 'string' || !/^[a-z0-9][a-z0-9-]*$/.test(o['id'])) errors.push('id 不合法');
  if (typeof o['name'] !== 'string' || o['name'].length === 0) errors.push('name 不能为空');
  if (typeof o['description'] !== 'string') errors.push('description 必须是字符串');
  if (!Array.isArray(o['permissions'])) errors.push('permissions 必须是数组');
  if (!Array.isArray(o['triggers'])) errors.push('triggers 必须是数组');
  if (typeof o['entry'] !== 'string' || o['entry'].length === 0) errors.push('entry 不能为空');
  if (typeof o['version'] !== 'string') errors.push('version 必须是字符串');
  if (o['source'] && !['builtin', 'market', 'custom'].includes(o['source'])) errors.push('source 不合法');
  if (errors.length > 0) return { ok: false, errors };
  return { ok: true, manifest: m as SkillManifest };
}

/** manifest → UI Skill 视图模型 */
export function toSkill(m: SkillManifest): Skill {
  return {
    id: m.id,
    name: m.name,
    description: m.description,
    icon: m.icon,
    category: m.category,
    permissions: m.permissions,
    source: m.source,
  };
}

/** 市场技能签名校验（占位：真实实现用 Ed25519 公钥验签） */
export function verifyMarketSkillSignature(m: SkillManifest, trustedPublicKeys: readonly string[]): boolean {
  if (m.source !== 'market') return true; // 非市场技能无需验签
  if (!m.signature) return false;
  // 占位校验：签名长度合理（base64 Ed25519 ≈ 88 字符）
  const looksLikeSig = /^[A-Za-z0-9+/=]{80,96}$/.test(m.signature);
  return looksLikeSig && trustedPublicKeys.length > 0;
}
