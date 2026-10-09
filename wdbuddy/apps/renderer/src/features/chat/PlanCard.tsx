/**
 * PlanCard — 计划卡片（§4.4，FR-08）
 * 结构化展示步骤列表（可勾选编辑），底部 [批准执行] [修改计划] [拒绝] 三按钮——仅 Plan 模式出现。
 */
import React, { useState } from 'react';
import type { PlanStep } from '@wdbuddy/shared-types';
import { requireApi } from '../../shared/ipc';
import { useAppStore } from '../../shared/store';
import { useChatStore } from './store';

export function PlanCard({ steps: initialSteps }: { steps: PlanStep[] }): React.ReactElement {
  const [steps, setSteps] = useState<PlanStep[]>(initialSteps);
  const [editing, setEditing] = useState(false);
  const clearPlan = useChatStore((s) => s.clearPlan);
  const activeTaskId = useAppStore((s) => s.activeTaskId);

  /** 批准执行：按（可能修改过的）步骤推进 */
  const approve = async (): Promise<void> => {
    if (!activeTaskId) return;
    const api = requireApi();
    await api.taskApprovePlan({ taskId: activeTaskId, steps, edited: true });
    await api.taskControl({ taskId: activeTaskId, action: 'approve', steps });
    clearPlan();
  };

  /** 拒绝：回到 draft 重新计划（§6 状态机） */
  const reject = async (): Promise<void> => {
    if (!activeTaskId) return;
    const api = requireApi();
    await api.taskControl({ taskId: activeTaskId, action: 'reject' });
    clearPlan();
  };

  const updateStepTitle = (id: string, title: string): void => {
    setSteps((ss) => ss.map((s) => (s.id === id ? { ...s, title } : s)));
  };

  const removeStep = (id: string): void => {
    setSteps((ss) => ss.filter((s) => s.id !== id).map((s, i) => ({ ...s, seq: i + 1 })));
  };

  const addStep = (): void => {
    setSteps((ss) => [
      ...ss,
      { id: `step-new-${Date.now()}`, seq: ss.length + 1, title: '新步骤', status: 'pending', artifactIds: [] },
    ]);
  };

  return (
    <div className="wb-plancard">
      <div className="wb-plancard__header">
        <span className="wb-plancard__icon">📋</span>
        <span className="wb-plancard__title">执行计划</span>
        <span className="wb-badge wb-badge--info">{steps.length} 步</span>
      </div>

      <div className="wb-plancard__steps">
        {steps.map((s) => (
          <div key={s.id} className="wb-plancard__step">
            {editing ? (
              <>
                <span className="wb-plancard__seq">{s.seq}.</span>
                <input
                  className="wb-input"
                  value={s.title}
                  onChange={(e) => updateStepTitle(s.id, e.target.value)}
                  style={{ flex: 1 }}
                />
                <button className="wb-btn wb-btn--ghost wb-btn--small" onClick={() => removeStep(s.id)} title="删除该步骤">
                  ✕
                </button>
              </>
            ) : (
              <>
                <input type="checkbox" defaultChecked aria-label={`保留步骤 ${s.seq}`} />
                <span className="wb-plancard__seq">{s.seq}.</span>
                <span className="wb-plancard__step-title">{s.title}</span>
              </>
            )}
          </div>
        ))}
      </div>

      {editing && (
        <button className="wb-btn wb-btn--ghost wb-btn--small" onClick={addStep}>
          ＋ 添加步骤
        </button>
      )}

      <div className="wb-plancard__footer">
        {editing ? (
          <>
            <button className="wb-btn wb-btn--primary" onClick={() => setEditing(false)}>✓ 完成修改</button>
            <button className="wb-btn wb-btn--ghost" onClick={() => { setSteps(initialSteps); setEditing(false); }}>取消</button>
          </>
        ) : (
          <>
            <button className="wb-btn wb-btn--primary" onClick={() => void approve()}>✓ 批准</button>
            <button className="wb-btn wb-btn--default" onClick={() => setEditing(true)}>✎ 修改</button>
            <button className="wb-btn wb-btn--danger" onClick={() => void reject()}>✕ 拒绝</button>
          </>
        )}
      </div>
    </div>
  );
}
