/**
 * WorkbenchLayout — 三栏工作台骨架（§4.1）
 * 顶部栏 48px / 状态栏 28px / 左栏 240px（可折叠 64px）/ 右栏 420px（320~640 可拖）
 * 三个 feature（task-list / chat / artifacts）分别对应三栏，禁止互相 import。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { ModelId } from '@wdbuddy/shared-types';
import { BUILTIN_MODEL_IDS, BUILTIN_MODEL_NAMES } from '@wdbuddy/shared-types';
import { TaskSidebar } from '../features/task-list/TaskSidebar';
import { ChatPanel } from '../features/chat/ChatPanel';
import { ArtifactPanel } from '../features/artifacts/ArtifactPanel';
import { useAppStore } from '../shared/store';

export function WorkbenchLayout(): React.ReactElement {
  const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed);
  const artifactsHidden = useAppStore((s) => s.artifactsHidden);
  const artifactWidth = useAppStore((s) => s.artifactWidth);
  const runtimeState = useAppStore((s) => s.runtimeState);
  const currentModel = useAppStore((s) => s.currentModel);
  const tokenCount = useAppStore((s) => s.tokenCount);
  const credits = useAppStore((s) => s.credits);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const toggleArtifacts = useAppStore((s) => s.toggleArtifacts);
  const setArtifactWidth = useAppStore((s) => s.setArtifactWidth);
  const openPanel = useAppStore((s) => s.setOpenPanel);
  const customProviders = useAppStore((s) => s.customProviders);
  const maxMode = useAppStore((s) => s.maxMode);

  /** 模型下拉变更：custom:api 拦截 → 弹出自定义表单；其余直接生效 */
  const onModelChange = (value: ModelId): void => {
    if (value === 'custom:api') {
      useAppStore.getState().setCustomProviderModal({ initial: null, switchOnSave: true });
      return;
    }
    useAppStore.getState().setCurrentModel(value);
  };

  // 右栏拖拽调宽（320~640）
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      dragRef.current = { startX: e.clientX, startWidth: artifactWidth };
      const onMove = (ev: MouseEvent): void => {
        if (!dragRef.current) return;
        const delta = dragRef.current.startX - ev.clientX;
        const next = Math.min(640, Math.max(320, dragRef.current.startWidth + delta));
        setArtifactWidth(next);
      };
      const onUp = (): void => {
        dragRef.current = null;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [artifactWidth, setArtifactWidth]
  );

  const cls = [
    'wb-workbench',
    sidebarCollapsed ? 'wb-workbench--sidebar-collapsed' : '',
    artifactsHidden ? 'wb-workbench--artifacts-hidden' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={cls} style={{ gridTemplateColumns: `${sidebarCollapsed ? 'var(--wb-sidebar-collapsed-width)' : 'var(--wb-sidebar-width)'} 1fr ${artifactsHidden ? '0' : artifactWidth + 'px'}` }}>
      {/* ────────── 顶部全局栏 48px ────────── */}
      <header className="wb-topbar">
        <div className="wb-topbar__logo" onClick={() => openPanel('tasks')}>
          <span>◆</span> WDBuddy
        </div>
        <select
          className="wb-select"
          style={{ width: 180 }}
          value={currentModel}
          onChange={(e) => onModelChange(e.target.value as ModelId)}
          aria-label="模型选择"
        >
          <option value="auto">Auto（自动选择）</option>
          {BUILTIN_MODEL_IDS.map((id) => (
            <option key={id} value={id}>
              {BUILTIN_MODEL_NAMES[id] ?? id}
            </option>
          ))}
          {customProviders.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
          <option value="custom:api">自定义 API…</option>
        </select>
        {maxMode && (
          <span className="wb-badge wb-badge--info" title="增强上下文已开启：长文档/代码类任务自动使用更大上下文窗口模型（在输入区 Max 开关中关闭）">
            Max
          </span>
        )}
        <div className="wb-topbar__spacer" />
        <button className="wb-btn wb-btn--ghost" onClick={() => openPanel('templates')} title="10 套主题 × 14 种版式，真实渲染预览">
          🎨 模版库
        </button>
        <button className="wb-btn wb-btn--ghost" onClick={() => openPanel('settings')}>
          ⚙ 设置
        </button>
      </header>

      {/* ────────── 左栏：任务与工作空间 240px ────────── */}
      <aside className="wb-sidebar">
        <TaskSidebar />
      </aside>

      {/* ────────── 中栏：对话区（核心） ────────── */}
      <main className="wb-chat">
        <ChatPanel />
      </main>

      {/* ────────── 右栏：成果区 420px ────────── */}
      {!artifactsHidden && (
        <>
          <div
            className="wb-artifacts__resizer"
            onMouseDown={onDragStart}
            style={{ cursor: 'col-resize', width: 4, background: 'var(--wb-border)' }}
            role="separator"
            aria-orientation="vertical"
          />
          <section className="wb-artifacts">
            <ArtifactPanel />
          </section>
        </>
      )}

      {/* ────────── 状态栏 28px ────────── */}
      <footer className="wb-statusbar">
        <span className={`wb-dot ${runtimeState === 'running' ? '' : runtimeState === 'offline' ? 'wb-dot--offline' : 'wb-dot--busy'}`} />
        <span>
          {runtimeState === 'running' ? 'Agent 运行中' : runtimeState === 'offline' ? 'Runtime 离线（降级）' : '空闲'}
        </span>
        <span>
          ·{' '}
          {currentModel === 'auto'
            ? 'Auto 路由'
            : BUILTIN_MODEL_NAMES[currentModel] ??
              (customProviders.find((p) => p.id === currentModel)?.name ?? currentModel)}
        </span>
        <span>· 本任务 Token {(tokenCount / 1000).toFixed(1)}k</span>
        <span>· 积分 {credits}</span>
        <span className="wb-statusbar__version">v1.0.0</span>
      </footer>
    </div>
  );
}

/** 侧栏折叠按钮快捷键（Ctrl+B，与主流 IDE 一致） */
export function useSidebarHotkey(): void {
  const toggle = useAppStore((s) => s.toggleSidebar);
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.ctrlKey && e.key.toLowerCase() === 'b') {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggle]);
}
