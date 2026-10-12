/**
 * StepTimeline — 步骤时间线（§4.4，FR-09 过程可见）
 * 每步含状态图标（✓ 完成 / ● 运行中 spinner / ○ 待执行 / ✕ 失败 / ⤴ 已重规划）、
 * 耗时、失败原因 tooltip；失败步骤可点击展开执行日志。
 *
 * 【耗时实时刷新】运行中步骤每秒触发一次 state 更新强制重渲染，
 * elapsed() 用 Date.now() 现算。修复"卡在 13ms 直到步骤完成才更新"：
 * React 只在 state 变化时重渲染，运行中步骤若无独立 state 变化，
 * 永远停在首帧值（每步抽出为独立组件 StepRow，tick state 驱动本行重渲染）。
 */
import React, { useEffect, useState } from 'react';
import type { PlanStep } from '@wdbuddy/shared-types';

/** 运行中步骤的耗时实时刷新（每秒一跳；完成后停在真实值） */
function useRunningTick(active: boolean): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
  return tick;
}

function stepIcon(status: PlanStep['status']): React.ReactElement {
  switch (status) {
    case 'done':
      return <span className="wb-tl__icon wb-tl__icon--done">✓</span>;
    case 'running':
      return <span className="wb-spinner wb-tl__icon--running" style={{ width: 12, height: 12 }} />;
    case 'failed':
      return <span className="wb-tl__icon wb-tl__icon--failed">✕</span>;
    case 'skipped':
      return <span className="wb-tl__icon wb-tl__icon--skipped">⏭</span>;
    default:
      return <span className="wb-tl__icon wb-tl__icon--pending">○</span>;
  }
}

function elapsed(s: PlanStep): string {
  if (!s.startedAt) return '';
  const end = s.endedAt ?? Date.now();
  const ms = end - s.startedAt;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/** 单步骤行：独立组件（useRunningTick 的 state 变化仅触发本行重渲染） */
function StepRow({
  s,
  expanded,
  onToggle,
}: {
  s: PlanStep;
  expanded: boolean;
  onToggle: () => void;
}): React.ReactElement {
  useRunningTick(s.status === 'running');
  return (
    <div className="wb-tl__item">
      <button
        className={`wb-tl__row ${s.status === 'failed' ? 'wb-tl__row--failed' : ''}`}
        onClick={() => s.status === 'failed' && onToggle()}
        title={s.status === 'failed' ? '点击展开失败日志' : undefined}
      >
        {stepIcon(s.status)}
        <span className="wb-tl__step-title">
          {s.status === 'failed' ? '✕' : ''} {s.title}
        </span>
        {s.status === 'failed' && <span className="wb-tl__hint">（点击展开日志）</span>}
        {s.startedAt && <span className="wb-tl__elapsed">({elapsed(s)})</span>}
      </button>
      {s.status === 'failed' && expanded && (
        <div className="wb-tl__log">
          <div className="wb-tl__log-cause">失败原因：{s.summary ?? '未知'}</div>
          <pre className="wb-tl__log-detail">{s.summary ?? '无详细日志'}</pre>
        </div>
      )}
      {s.status === 'skipped' && <span className="wb-tl__replan">⤴ 已重规划跳过</span>}
    </div>
  );
}

export function StepTimeline({ steps }: { steps: PlanStep[] }): React.ReactElement {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (id: string): void => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="wb-tl">
      <div className="wb-tl__title">🤖 执行过程</div>
      {steps.map((s) => (
        <StepRow key={s.id} s={s} expanded={expanded.has(s.id)} onToggle={() => toggle(s.id)} />
      ))}
    </div>
  );
}
