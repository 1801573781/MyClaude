/**
 * PreferencesPanel — 用户偏好（FR-18，原"记忆"更名）
 * 自动沉淀用户偏好（职业/风格/常用格式），可查看/编辑/删除，注入后续任务上下文
 */
import React, { useState } from 'react';

interface LocalPreferenceItem {
  id: string;
  key: string;
  value: string;
  origin: 'auto' | 'user';
}

const INITIAL: LocalPreferenceItem[] = [
  { id: 'p1', key: '职业', value: '互联网运营，常用数据周报', origin: 'auto' },
  { id: 'p2', key: '输出风格', value: '简洁中文，多用表格与要点', origin: 'auto' },
  { id: 'p3', key: '常用格式', value: '周报 PPT 偏好 16:9 蓝色商务模板', origin: 'user' },
];

export function PreferencesPanel({ onClose }: { onClose: () => void }): React.ReactElement {
  const [items, setItems] = useState<LocalPreferenceItem[]>(INITIAL);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  const save = (id: string): void => {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, value: draft } : it)));
    setEditingId(null);
  };

  return (
    <div className="wb-modal-mask" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wb-modal">
        <div className="wb-modal__header">⭐ 偏好</div>
        <div className="wb-modal__body">
          <div style={{ fontSize: 12, color: 'var(--wb-text-secondary)', marginBottom: 12 }}>
            偏好是 WDBuddy 自动沉淀的你的使用习惯（职业、输出风格、常用格式等），将注入后续任务上下文（可在设置中关闭，关闭后不再注入）
          </div>
          {items.map((it) => (
            <div key={it.id} className="wb-pref__item">
              <span className="wb-badge wb-badge--neutral">{it.origin === 'auto' ? '自动沉淀' : '手动添加'}</span>
              <span className="wb-pref__key">{it.key}</span>
              {editingId === it.id ? (
                <>
                  <input className="wb-input" style={{ flex: 1 }} value={draft} onChange={(e) => setDraft(e.target.value)} />
                  <button className="wb-btn wb-btn--primary wb-btn--small" onClick={() => save(it.id)}>保存</button>
                </>
              ) : (
                <>
                  <span style={{ flex: 1 }}>{it.value}</span>
                  <button
                    className="wb-btn wb-btn--ghost wb-btn--small"
                    onClick={() => {
                      setEditingId(it.id);
                      setDraft(it.value);
                    }}
                  >
                    编辑
                  </button>
                  <button
                    className="wb-btn wb-btn--ghost wb-btn--small"
                    onClick={() => setItems((prev) => prev.filter((x) => x.id !== it.id))}
                  >
                    删除
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
        <div className="wb-modal__footer">
          <button className="wb-btn wb-btn--ghost" onClick={onClose}>关闭</button>
          <button
            className="wb-btn wb-btn--default"
            onClick={() =>
              setItems((prev) => [
                ...prev,
                { id: `p${Date.now()}`, key: '新偏好', value: '（待编辑）', origin: 'user' },
              ])
            }
          >
            ＋ 添加
          </button>
        </div>
      </div>
    </div>
  );
}
