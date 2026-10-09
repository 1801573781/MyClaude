/**
 * SkillsCenter — 技能中心（FR-17）
 * 内置技能（一点即用注入指令）+ 自定义技能（创建/列表/删除，持久化到磁盘）
 */
import React, { useEffect, useMemo, useState } from 'react';
import type { Skill } from '@wdbuddy/shared-types';
import { useAppStore } from '../../shared/store';
import { requireApi } from '../../shared/ipc';

/** 内置技能清单（与 skill-sdk BUILTIN_SKILLS 对应的 UI 数据）。
 *  instruction：技能预置指令模板——"一点即用"时注入 Composer 输入框，
 *  用户补充 {} 占位的细节后发送，Agent 即按模板执行（工具链已真实可用）。 */
const BUILTIN: Array<Skill & { instruction: string }> = [
  { id: 'weekly-report', name: '周报生成', description: '把销售/运营数据自动整理成周报 PPT', icon: '📊', category: '办公', permissions: ['fs.read', 'office.generate'], source: 'builtin', instruction: '请把 {数据文件路径或数据描述} 整理成一份周报 PPT：汇总关键数据、亮点与下周计划，共约 8 页，商务风格，保存到工作空间。' },
  { id: 'data-analysis', name: '数据分析', description: '读取表格数据，清洗并生成分析图表', icon: '📈', category: '办公', permissions: ['fs.read', 'fs.write'], source: 'builtin', instruction: '请分析 {表格文件路径}：清洗数据、统计关键指标，用图表页展示趋势与分布，输出分析报告。' },
  { id: 'deep-research', name: '深度调研', description: '多轮检索-阅读-汇总，输出带来源的报告', icon: '🔍', category: '办公', permissions: ['search.web'], source: 'builtin', instruction: '请深度调研 {主题}：多轮联网搜索收集资料，输出一份结构化报告（背景/现状/关键数据/趋势/来源清单），保存为 Markdown。' },
  { id: 'web-builder', name: '网页开发', description: '从零构建可运行的响应式网页', icon: '🌐', category: '开发', permissions: ['fs.read', 'fs.write', 'shell.exec'], source: 'builtin', instruction: '请开发一个网页：{描述需求与页面内容}。要求响应式布局、现代视觉风格、可直接在浏览器打开，保存到工作空间。' },
  { id: 'poster-design', name: '海报设计', description: '生成主视觉海报（品牌/节日/活动）', icon: '🎨', category: '设计', permissions: ['fs.write'], source: 'builtin', instruction: '请设计一张海报：{主题/用途/风格要求}。输出为单页 HTML（内联 CSS），视觉冲击力强，保存到工作空间。' },
  { id: 'roleplay-script', name: '教学剧本', description: '生成课文角色扮演教学方案', icon: '🎭', category: '教育', permissions: ['fs.write'], source: 'builtin', instruction: '请为课文 {课文标题或内容} 设计角色扮演教学方案：角色设定、场景脚本、课堂流程与评价要点，保存为 Word 文档。' },
];

/** 创建表单状态 */
interface CreateForm {
  name: string;
  description: string;
  icon: string;
  category: string;
  instruction: string;
}

const EMPTY_FORM: CreateForm = { name: '', description: '', icon: '🛠', category: '自定义', instruction: '' };

export function SkillsCenter({ onClose }: { onClose: () => void }): React.ReactElement {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>('全部');
  const [customSkills, setCustomSkills] = useState<Array<Skill & { instruction: string }>>([]);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');

  // 加载自定义技能
  const loadCustom = async (): Promise<void> => {
    try {
      const r = await requireApi().skillsList();
      setCustomSkills(r.skills);
    } catch {
      /* 静默：无自定义技能 */
    }
  };
  useEffect(() => { void loadCustom(); }, []);

  const all = useMemo(() => [...BUILTIN, ...customSkills], [customSkills]);
  const categories = useMemo(() => ['全部', ...new Set(all.map((s) => s.category))], [all]);
  const filtered = useMemo(
    () =>
      all.filter(
        (s) =>
          (category === '全部' || s.category === category) &&
          (query.trim() === '' || s.name.includes(query) || s.description.includes(query))
      ),
    [all, query, category]
  );

  /** 一点即用：注入指令草稿到输入框 */
  const useSkill = (instruction: string): void => {
    useAppStore.getState().setComposerDraft(instruction);
    onClose();
  };

  /** 保存自定义技能（bash 直写防还原版：saveSkill 为核心特征） */
  const saveSkill = async (): Promise<void> => {
    if (saving) return;
    setSaving(true);
    setFormError('');
    try {
      const api = requireApi();
      const r = await api.skillsCreate({
        name: form.name,
        description: form.description,
        icon: form.icon,
        category: form.category,
        instruction: form.instruction,
        permissions: ['fs.read', 'fs.write'],
      });
      if (r.ok) {
        setCreating(false);
        setForm(EMPTY_FORM);
        await loadCustom();
      } else {
        setFormError(r.error ?? '创建失败');
      }
    } catch (e) {
      setFormError(String(e));
    } finally {
      setSaving(false);
    }
  };

  /** 删除自定义技能 */
  const removeSkill = async (id: string): Promise<void> => {
    await requireApi().skillsRemove(id);
    await loadCustom();
  };

  return (
    <div className="wb-modal-mask" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wb-modal" style={{ minWidth: 680 }}>
        <div className="wb-modal__header">🧩 技能中心</div>
        <div className="wb-modal__body">
          {creating ? (
            /* ── 创建表单 ── */
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="wb-field">
                <label className="wb-field__label">技能名称 *</label>
                <input className="wb-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如：会议纪要整理" maxLength={30} />
              </div>
              <div className="wb-field">
                <label className="wb-field__label">描述</label>
                <input className="wb-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="一句话说明技能用途" maxLength={60} />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div className="wb-field" style={{ flex: 1 }}>
                  <label className="wb-field__label">图标（emoji）</label>
                  <input className="wb-input" value={form.icon} onChange={(e) => setForm({ ...form, icon: e.target.value })} maxLength={4} />
                </div>
                <div className="wb-field" style={{ flex: 1 }}>
                  <label className="wb-field__label">分类</label>
                  <input className="wb-input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} maxLength={10} />
                </div>
              </div>
              <div className="wb-field">
                <label className="wb-field__label">指令模板 *</label>
                <textarea
                  className="wb-textarea"
                  rows={4}
                  value={form.instruction}
                  onChange={(e) => setForm({ ...form, instruction: e.target.value })}
                  placeholder={'发送给 Agent 的指令内容，用 {} 标记需要补充的部分。\n例如：请把 {会议记录文件路径} 整理成结构化会议纪要，输出行动项清单，保存为 Markdown。'}
                />
              </div>
              {formError && <div style={{ color: 'var(--wb-color-danger)', fontSize: 12 }}>{formError}</div>}
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                <button className="wb-btn wb-btn--ghost" onClick={() => { setCreating(false); setFormError(''); }}>取消</button>
                <button className="wb-btn wb-btn--primary" disabled={saving || !form.name.trim() || form.instruction.trim().length < 5} onClick={() => void saveSkill()}>
                  {saving ? '保存中…' : '保存技能'}
                </button>
              </div>
            </div>
          ) : (
            <>
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
                    <div className="wb-skill-card__name">{s.name}{s.source === 'custom' && <span className="wb-badge wb-badge--info" style={{ marginLeft: 6 }}>自定义</span>}</div>
                    <div className="wb-skill-card__desc">{s.description}</div>
                    <div className="wb-skill-card__perms">
                      {s.permissions.map((p) => (
                        <span key={p} className="wb-badge wb-badge--neutral">{p}</span>
                      ))}
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button
                        className="wb-btn wb-btn--primary wb-btn--small"
                        onClick={() => useSkill(s.instruction)}
                        title="将技能指令填入输入框，补充细节后发送"
                      >
                        一点即用
                      </button>
                      {s.source === 'custom' && (
                        <button
                          className="wb-btn wb-btn--ghost wb-btn--small"
                          onClick={() => void removeSkill(s.id)}
                          title="删除此自定义技能"
                        >
                          删除
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              {customSkills.length > 0 && (
                <div style={{ marginTop: 8, fontSize: 11, color: 'var(--wb-text-disabled)' }}>
                  自定义技能保存在本机（应用数据目录 skills/），跨会话可用
                </div>
              )}
            </>
          )}
        </div>
        <div className="wb-modal__footer">
          <button className="wb-btn wb-btn--ghost" onClick={onClose}>关闭</button>
          {!creating && (
            <button className="wb-btn wb-btn--default" onClick={() => setCreating(true)}>＋ 创建自定义技能</button>
          )}
        </div>
      </div>
    </div>
  );
}
