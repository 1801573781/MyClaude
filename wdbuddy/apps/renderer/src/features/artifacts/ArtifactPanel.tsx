/**
 * ArtifactPanel — 右栏成果区（§4.5，默认 420px 可拖 320~640）
 * 四 Tab：预览 / 文件 / 变更 / 浏览器
 * 底部操作条：[打开目录] [回滚] [✓ 验收] —— 验收是任务闭环最终动作
 */
import React, { useEffect } from 'react';
import { useAppStore } from '../../shared/store';
import { useArtifactsStore, type ArtifactTab } from './store';
import { PreviewTab } from './PreviewTab';
import { FilesTab } from './FilesTab';
import { DiffTab } from './DiffTab';
import { BrowserTab } from './BrowserTab';
import './styles.css';

const TABS: Array<{ key: ArtifactTab; label: string }> = [
  { key: 'preview', label: '预览' },
  { key: 'files', label: '文件' },
  { key: 'diff', label: '变更' },
  { key: 'browser', label: '浏览器' },
];

export function ArtifactPanel(): React.ReactElement {
  const activeTaskId = useAppStore((s) => s.activeTaskId);
  const tab = useArtifactsStore((s) => s.tab);
  const setTab = useArtifactsStore((s) => s.setTab);
  const load = useArtifactsStore((s) => s.load);
  const artifacts = useArtifactsStore((s) => s.artifacts);
  const rollbackAll = useArtifactsStore((s) => s.rollbackAll);
  const accept = useArtifactsStore((s) => s.accept);

  // 事件桥接：artifact.created 实时追加
  useEffect(() => {
    const unsub = useAppStore.getState().subscribeRuntimeEvent((e) => {
      useArtifactsStore.getState().handleEvent(e);
    });
    return unsub;
  }, []);

  useEffect(() => {
    if (activeTaskId) void load(activeTaskId);
  }, [activeTaskId, load]);

  return (
    <div className="wb-artifacts__inner">
      {/* Tab 头 */}
      <div className="wb-artifacts__tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            className={`wb-artifacts__tab ${tab === t.key ? 'wb-artifacts__tab--active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
            {t.key === 'files' && artifacts.length > 0 && (
              <span className="wb-artifacts__tab-count">{artifacts.length}</span>
            )}
          </button>
        ))}
      </div>

      {/* Tab 内容 */}
      <div className="wb-artifacts__content">
        {tab === 'preview' && <PreviewTab />}
        {tab === 'files' && <FilesTab />}
        {tab === 'diff' && <DiffTab />}
        {tab === 'browser' && <BrowserTab />}
      </div>

      {/* 底部操作条（§4.5：验收是任务闭环最终动作） */}
      <div className="wb-artifacts__footer">
        <button
          className="wb-btn wb-btn--default"
          onClick={() => {
            const sel = artifacts.find((a) => a.id === useArtifactsStore.getState().selectedArtifactId);
            if (sel?.path) {
              void import('../../shared/ipc').then(({ requireApi }) => requireApi().fsReveal(sel.path!));
            }
          }}
        >
          打开目录
        </button>
        <button className="wb-btn wb-btn--default" onClick={() => void rollbackAll()} title="回滚本任务全部文件变更">
          回滚
        </button>
        <button className="wb-btn wb-btn--primary" onClick={() => void accept()} title="验收后任务进入已完成">
          ✓ 验收
        </button>
      </div>
    </div>
  );
}
