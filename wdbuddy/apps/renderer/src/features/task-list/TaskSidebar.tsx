/**
 * TaskSidebar — 左栏：任务与工作空间（§4.3，240px 可折叠）
 */
import React, { useEffect, useState } from 'react';
import type { Task } from '@wdbuddy/shared-types';
import { useAppStore } from '../../shared/store';
import { useTaskListStore, groupTasks, taskBadge } from './store';

export function TaskSidebar(): React.ReactElement {
  const refresh = useTaskListStore((s) => s.refresh);
  const tasks = useTaskListStore((s) => s.tasks);
  const workspaces = useTaskListStore((s) => s.workspaces);
  const loadWorkspaces = useTaskListStore((s) => s.loadWorkspaces);
  const addWorkspace = useTaskListStore((s) => s.addWorkspace);
  const setOpenPanel = useAppStore((s) => s.setOpenPanel);

  useEffect(() => {
    void refresh();
    void loadWorkspaces();
  }, [refresh, loadWorkspaces]);

  const groups = groupTasks(tasks);

  return (
    <div className="wb-sidebar__inner">
      {/* 新建任务 */}
      <button className="wb-btn wb-btn--primary" style={{ margin: '12px' }} onClick={() => setOpenPanel('new-task')}>
        ＋ 新建任务
      </button>

      {/* 任务分组 */}
      <TaskGroupSection title="进行中" defaultOpen tasks={groups.active} />
      <TaskGroupSection title={`已完成 (${groups.completed.length})`} defaultOpen={false} tasks={groups.completed} />
      <TaskGroupSection title={`规则任务 (${groups.rules.length})`} defaultOpen={false} tasks={groups.rules} />

      <div className="wb-sidebar__divider" />

      {/* 工作空间 */}
      <div className="wb-sidebar__section">
        <div className="wb-sidebar__section-title">💼 工作空间</div>
        {workspaces.map((w) => (
          <div key={w} className="wb-sidebar__ws-item" title={w}>
            📁 {w}
          </div>
        ))}
        <button className="wb-btn wb-btn--ghost wb-btn--small" onClick={() => void addWorkspace()}>
          ＋ 添加文件夹
        </button>
      </div>

      {/* 底部功能入口 */}
      <div className="wb-sidebar__footer">
        <button className="wb-btn wb-btn--ghost" onClick={() => setOpenPanel('preferences')}>⭐ 偏好</button>
        <button className="wb-btn wb-btn--ghost" onClick={() => setOpenPanel('skills')}>🧩 技能中心</button>
        <button className="wb-btn wb-btn--ghost" onClick={() => setOpenPanel('remote-link')}>📱 手机联动</button>
      </div>
    </div>
  );
}

/** 分组区块：进行中默认展开，其余折叠显示计数 */
function TaskGroupSection({
  title,
  tasks,
  defaultOpen,
}: {
  title: string;
  tasks: Task[];
  defaultOpen: boolean;
}): React.ReactElement {
  const [open, setOpen] = useState(defaultOpen);
  const selected = useTaskListStore((s) => s.selectedTaskId);
  const select = useTaskListStore((s) => s.select);
  const setActiveTaskId = useAppStore((s) => s.setActiveTaskId);

  return (
    <div className="wb-sidebar__group">
      <button className="wb-sidebar__group-header" onClick={() => setOpen(!open)}>
        {open ? '▾' : '▸'} {tasks.length > 0 || title.startsWith('进行') ? `${title}${title.startsWith('进行') ? ` (${tasks.length})` : ''}` : title}
      </button>
      {open &&
        tasks.map((t) => {
          const badge = taskBadge(t);
          const isActive = selected === t.id;
          return (
            <div
              key={t.id}
              className={`wb-sidebar__task ${isActive ? 'wb-sidebar__task--active' : ''}`}
              onClick={() => {
                select(t.id);
                setActiveTaskId(t.id);
              }}
            >
              <div className="wb-sidebar__task-title">{t.title}</div>
              <div className="wb-sidebar__task-meta">
                <span className={`wb-badge wb-badge--${badge.tone}`}>{badge.text}</span>
                <span className="wb-sidebar__task-mode">{t.mode === 'plan' ? 'Plan' : t.mode === 'craft' ? 'Craft' : 'Ask'}</span>
              </div>
              {/* hover 快捷操作（§4.3） */}
              <div className="wb-sidebar__task-actions" onClick={(e) => e.stopPropagation()}>
                {(t.status === 'running' || t.status === 'planning') && (
                  <button className="wb-btn wb-btn--ghost wb-btn--small" onClick={() => void useTaskListStore.getState().control(t.id, 'pause')}>⏸</button>
                )}
                {t.status === 'paused' && (
                  <button className="wb-btn wb-btn--ghost wb-btn--small" onClick={() => void useTaskListStore.getState().control(t.id, 'resume')}>▶</button>
                )}
                {t.status === 'interrupted' && (
                  <button className="wb-btn wb-btn--ghost wb-btn--small" onClick={() => void useTaskListStore.getState().control(t.id, 'start')}>⤴ 恢复</button>
                )}
                <button className="wb-btn wb-btn--ghost wb-btn--small" onClick={() => void useTaskListStore.getState().remove(t.id)}>🗑</button>
              </div>
            </div>
          );
        })}
      {open && tasks.length === 0 && <div className="wb-sidebar__empty">（空）</div>}
    </div>
  );
}
