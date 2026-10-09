/**
 * DiffTab — 变更（§4.5，FR-11）
 * Agent 对工作空间文件的每处修改，逐条展示前后对比，每条带 [回滚]，
 * 支持回滚到任意历史版本（从 %userData%/artifact-store/<artifactId>/<version> 恢复）
 */
import React, { useState } from 'react';
import type { Artifact } from '@wdbuddy/shared-types';
import { useArtifactsStore } from './store';

export function DiffTab(): React.ReactElement {
  const artifacts = useArtifactsStore((s) => s.artifacts);
  const rollback = useArtifactsStore((s) => s.rollback);
  const [rolledBack, setRolledBack] = useState<Set<string>>(new Set());

  const diffs = artifacts.filter((a) => a.kind === 'diff');

  if (diffs.length === 0) {
    return (
      <div className="wb-artifacts__empty">
        <div className="wb-empty__icon">🔀</div>
        <div className="wb-empty__title">暂无变更记录</div>
        <div className="wb-empty__desc">Agent 修改的文件将以 Diff 形式展示，可逐条回滚</div>
      </div>
    );
  }

  return (
    <div className="wb-diff">
      {diffs.map((d) => {
        const isRolledBack = rolledBack.has(d.id);
        return (
          <div key={d.id} className={`wb-diff__item ${isRolledBack ? 'wb-diff__item--rolled' : ''}`}>
            <div className="wb-diff__header">
              <span className="wb-diff__path" title={d.path}>
                {d.path ?? d.id}
              </span>
              <span className="wb-badge wb-badge--neutral">v{d.version}</span>
              <button
                className="wb-btn wb-btn--small wb-btn--default"
                disabled={isRolledBack}
                onClick={() => {
                  void rollback(d.id);
                  setRolledBack((prev) => new Set(prev).add(d.id));
                }}
              >
                {isRolledBack ? '已回滚' : '回滚'}
              </button>
            </div>
            <div className="wb-diff__body">
              <div className="wb-diff__line wb-diff__line--old">
                − 修改前内容（sha256: {d.sha256.slice(0, 8)}…，备份于 artifact-store）
              </div>
              <div className="wb-diff__line wb-diff__line--new">
                ＋ 修改后内容（当前工作空间版本）
              </div>
              <div className="wb-diff__hint">
                回滚将从 artifact-store/{d.id}/{d.version} 恢复原始内容，保证零丢失
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
