/**
 * Composer — 输入区（§4.4）
 * - 多行文本框 + placeholder
 * - 提示词增强按钮 💡：口语 → 结构化提示词，回填且高亮新增（FR-07）
 * - ModeSwitch：Ask | Plan | Craft 三段式（FR-02）
 * - 附件 📎、Max 开关、发送（Enter 发送 / Shift+Enter 换行）
 */
import React, { useEffect, useRef, useState } from 'react';
import type { TaskMode } from '@wdbuddy/shared-types';
import { requireApi } from '../../shared/ipc';
import { useAppStore } from '../../shared/store';
import { useChatStore } from './store';

export function Composer(): React.ReactElement {
  const [text, setText] = useState('');
  const [enhancing, setEnhancing] = useState(false);
  const [enhancedRanges, setEnhancedRanges] = useState<Array<{ start: number; end: number }>>([]);
  const [mode, setMode] = useState<TaskMode>('plan');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const activeTaskId = useAppStore((s) => s.activeTaskId);
  const maxMode = useAppStore((s) => s.maxMode);
  const setMaxMode = useAppStore((s) => s.setMaxMode);
  const task = useChatStore((s) => s.task);
  const awaitingInput = useChatStore((s) => s.awaitingInput);
  // 切换任务时，模式选择器跟随该任务的已存模式（发送时仍可改，随 start 同步）
  useEffect(() => {
    if (task) setMode(task.mode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task?.id]);
  // Agent 提问等待回复时：自动聚焦输入框，引导用户作答
  useEffect(() => {
    if (awaitingInput) textareaRef.current?.focus();
  }, [awaitingInput]);
  // 技能中心"一点即用"注入的指令草稿：填入输入框并聚焦（用户可补充细节后发送）
  const composerDraft = useAppStore((s) => s.composerDraft);
  useEffect(() => {
    if (composerDraft) {
      setText(composerDraft);
      useAppStore.getState().setComposerDraft(null);
      textareaRef.current?.focus();
    }
  }, [composerDraft]);

  /** 提示词增强（FR-07）：结果回填输入框并高亮新增内容 */
  const enhance = async (): Promise<void> => {
    if (!text.trim() || enhancing) return;
    setEnhancing(true);
    try {
      const api = requireApi();
      const result = await api.promptEnhance(text);
      // 以原文本在增强结果中的位置计算高亮区间（新增内容 = 其余部分）
      const idx = result.enhanced.indexOf(text.trim());
      if (idx >= 0) {
        setEnhancedRanges([
          { start: 0, end: idx },
          { start: idx + text.trim().length, end: result.enhanced.length },
        ]);
      }
      setText(result.enhanced);
      textareaRef.current?.focus();
    } finally {
      setEnhancing(false);
    }
  };

  /** 发送：无任务则先创建（用当前 Composer 状态），有任务则追加消息并 start */
  const send = async (): Promise<void> => {
    const instruction = text.trim();
    if (!instruction) return;
    const api = requireApi();

    if (!activeTaskId) {
      // 新任务：NewTaskInput（§4.6 字段集）
      const { taskId } = await api.taskCreate({
        description: instruction,
        mode,
        workspaceUri: null,
        permission: 'default',
        modelId: useAppStore.getState().currentModel,
        maxContext: maxMode,
        trigger: { type: 'manual' },
      });
      useAppStore.getState().setActiveTaskId(taskId);
      await api.taskControl({ taskId, action: 'start' });
    } else {
      // 已有任务：跟进输入先本地即时上屏再继续推进。
      // 修复：loadTask 仅在 activeTaskId 变化时触发，主进程虽已把跟进消息落库，
      // 但渲染层不会重新拉取——表现为"只有第一句显示，后面输的都不显示"。
      // 乐观追加后，后续 token.delta 也会正确开启新的助手气泡（而非拼进上一条）
      useChatStore.getState().appendUserMessage(activeTaskId, instruction);
      // 已回复 Agent 提问：清除等待卡片（任务唤醒后继续）
      useChatStore.getState().clearAwaiting();
      // 同步本轮模式：修复"模式在任务创建时固化，之后切换 Ask/Plan/Craft 无效"
      //（plan 任务切 Ask/Craft 仍要求审批；ask 任务切 Plan 永远不出计划）。
      // 同时清理旧 PlanCard 与旧步骤时间线：新一轮步骤到达前清屏，避免新旧混排、
      // 旧步骤误打 ✓（历史 bug：第二轮执行时第一轮的步骤仍显示为已完成）
      useChatStore.getState().syncTaskMode(mode);
      useChatStore.getState().clearPlan();
      useChatStore.getState().resetSteps();
      // 侧栏任务列表即时刷新：修复"跟进后进行中分组为空"（状态已变 running 但列表不刷新）
      void import('../task-list/store').then((m) => m.useTaskListStore.getState().refresh());
      await api.taskControl({
        taskId: activeTaskId,
        action: 'start',
        modelId: useAppStore.getState().currentModel,
        mode,
        instruction,
      });
    }

    setText('');
    setEnhancedRanges([]);
    useAppStore.getState().setRuntimeState('running');
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const hasHighlight = enhancedRanges.length > 0;

  return (
    <div className="wb-composer">
      {/* 提示词增强标识行 */}
      {hasHighlight && (
        <div className="wb-composer__enhanced-hint">
          💡 已增强为结构化提示词（<mark>高亮</mark>为新增内容，可继续编辑）
        </div>
      )}

      <textarea
        ref={textareaRef}
        className="wb-textarea wb-composer__input"
        placeholder={awaitingInput ? '✍️ 回复上方问题，Agent 将继续执行…' : '说点什么… 例如：把这周的销量数据做成周报'}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        style={{ minHeight: 'var(--wb-composer-min-height)' }}
      />

      <div className="wb-composer__toolbar">
        {/* 任务模式（radio 组）：三态单选，决定"下一条消息"的执行方式——
            非 tab 页（不切换视图），避免"切 tab 换界面"的误导 */}
        <div className="wb-modeswitch" role="radiogroup" aria-label="任务模式">
          <span className="wb-modeswitch__label">模式</span>
          <ModeRadio label="Ask" value="ask" checked={mode === 'ask'} title="只问答，不操作文件" onChange={setMode} />
          <ModeRadio label="Plan" value="plan" checked={mode === 'plan'} title="先出计划，批准后执行" onChange={setMode} />
          <ModeRadio label="Craft" value="craft" checked={mode === 'craft'} title="全自主执行交付" onChange={setMode} />
        </div>

        <div className="wb-composer__spacer" />

        <button
          className="wb-btn wb-btn--ghost"
          onClick={() => void enhance()}
          disabled={enhancing || !text.trim()}
          title="提示词增强：口语 → 结构化提示词（背景/目标/约束/期望输出）"
        >
          {enhancing ? '增强中…' : '💡 提示词增强'}
        </button>

        <button className="wb-btn wb-btn--ghost" title="附件：把文件路径引用进指令" onClick={() => undefined}>
          📎
        </button>

        <label className="wb-badge wb-badge--info" title="Max 模式：长文档/代码/调研类任务使用更大上下文窗口的模型，效果更好但更耗积分">
          Max 模式
          <input type="checkbox" checked={maxMode} onChange={(e) => setMaxMode(e.target.checked)} />
        </label>

        <button className="wb-btn wb-btn--primary" onClick={() => void send()} disabled={!text.trim()}>
          发送 ➤
        </button>
      </div>
    </div>
  );
}

function ModeRadio({
  label,
  value,
  checked,
  title,
  onChange,
}: {
  label: string;
  value: TaskMode;
  checked: boolean;
  title: string;
  onChange: (v: TaskMode) => void;
}): React.ReactElement {
  return (
    <label className={`wb-mode ${checked ? 'wb-mode--active' : ''}`} title={title}>
      <input
        type="radio"
        name="task-mode"
        value={value}
        checked={checked}
        onChange={() => onChange(value)}
      />
      {label}
    </label>
  );
}
