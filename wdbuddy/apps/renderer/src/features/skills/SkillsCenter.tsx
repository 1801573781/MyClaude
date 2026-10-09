/**
 * SkillsCenter — 技能中心（FR-17）
 * 内置技能库（分类/搜索/一点即用）、专家智能体（角色化组合）、自定义技能创建
 */
import React, { useMemo, useState } from 'react';
import type { Skill } from '@wdbuddy/shared-types';

/** 内置技能清单（与 skill-sdk BUILTIN_SKILLS 对应的 UI 数据） */
const BUILTIN: Skill[] = [
  { id: 'weekly-report', name: '周报生成', description: '把销售/运营数据自动整理成周报 PPT', icon: '📊', category: '办公', permissions: ['fs.read', 'office.generate'], source: 'builtin' },
  { id: 'data-analysis', name: '数据分析', description: '读取表格数据，清洗并生成分析图表', icon: '📈', category: '办公', permissions: ['fs.read', 'fs.write'], source: 'builtin' },
  { id: 'deep-research', name: '深度调研', description: '多轮检索-阅读-汇总，输出带来源的报告', icon: '🔍', category: '办公', permissions: ['search.web'], source: 'builtin' },
  { id: 'web-builder', name: '网页开发', description: '从零构建可运行的响应式网页', icon: '🌐', category: '开发', permissions: ['fs.read', 'fs.write', 'shell.exec'], source: 'builtin' },
  { id: 'poster-design', name: '海报设计', description: '生成主视觉海报（品牌/节日/活动）', icon: '🎨', category: '设计', permissions: ['fs.write'], source: 'builtin' },
  { id: 'roleplay-script', name: '教学剧本', description: '生成课文角色扮演教学方案', icon: '🎭', category: '教育', permissions: ['fs.write'], source: 'builtin' },
];

export function SkillsCenter({ onClose }: { onClose: () => void }): React.ReactElement {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>('全部');

  const categories = useMemo(() => ['全部', ...new Set(BUILTIN.map((s) => s.category))], []);
  const filtered = useMemo(
    () =>
      BUILTIN.filter(
        (s) =>
          (category === '全部' || s.category === category) &&
          (query.trim() === '' || s.name.includes(query) || s.description.includes(query))
      ),
    [query, category]
  );

  return (
    <div className="wb-modal-mask" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wb-modal" style={{ minWidth: 680 }}>
        <div className="wb-modal__header">🧩 技能中心</div>
        <div className="wb-modal__body">
          {/* 搜索 + 分类 */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
            <input
              className="wb-input"
              placeholder="搜索技能…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select className="wb-select" style={{ width: 120 }} value={category} onChange={(e) => setCategory(e.target.value)}>
              {categories.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </div>

          {/* 技能网格 */}
          <div className="wb-skills__grid">
            {filtered.map((s) => (
              <div key={s.id} className="wb-skill-card">
                <div className="wb-skill-card__icon">{s.icon}</div>
                <div className="wb-skill-card__name">{s.name}</div>
                <div className="wb-skill-card__desc">{s.description}</div>
                <div className="wb-skill-card__perms">
                  {s.permissions.map((p) => (
                    <span key={p} className="wb-badge wb-badge--neutral">{p}</span>
                  ))}
                </div>
                <button
                  className="wb-btn wb-btn--primary wb-btn--small"
                  onClick={() => onClose()}
                  title="在 Composer 中以 / 或点选触发"
                >
                  一点即用
                </button>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 16, fontSize: 12, color: 'var(--wb-text-secondary)' }}>
            支持自定义技能创建与 OpenClaw 生态技能（manifest 声明式权限，经签名校验）
          </div>
        </div>
        <div className="wb-modal__footer">
          <button className="wb-btn wb-btn--ghost" onClick={onClose}>关闭</button>
          <button className="wb-btn wb-btn--default" onClick={() => undefined}>＋ 创建自定义技能</button>
        </div>
      </div>
    </div>
  );
}
