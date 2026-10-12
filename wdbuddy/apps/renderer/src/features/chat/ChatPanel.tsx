/**
 * ChatPanel — 中栏对话区（§4.4）
 * 任务头 / MessageList / PlanCard / StepTimeline / 权限卡片 / Composer
 */
import React, { useEffect, useRef } from 'react';
import { useAppStore } from '../../shared/store';
import { requireApi } from '../../shared/ipc';
import { useChatStore } from './store';
import { MessageBubble } from './MessageBubble';
import { PlanCard } from './PlanCard';
import { StepTimeline } from './StepTimeline';
import { PermissionRequestCard } from './PermissionRequestCard';
import { Composer } from './Composer';
import './styles.css';

export function ChatPanel(): React.ReactElement {
  const activeTaskId = useAppStore((s) => s.activeTaskId);
  const task = useChatStore((s) => s.task);
  const messages = useChatStore((s) => s.messages);
  const steps = useChatStore((s) => s.steps);
  const plan = useChatStore((s) => s.plan);
  const permissionRequest = useChatStore((s) => s.permissionRequest);
  const awaitingInput = useChatStore((s) => s.awaitingInput);
  const streaming = useChatStore((s) => s.streaming);
  const loadTask = useChatStore((s) => s.loadTask);

  // 事件桥接：chat store 消费统一事件流
  useEffect(() => {
    const unsub = useAppStore.getState().subscribeRuntimeEvent((e) => {
      useChatStore.getState().handleEvent(e);
    });
    return unsub;
  }, []);

  useEffect(() => {
    if (activeTaskId) void loadTask(activeTaskId);
  }, [activeTaskId, loadTask]);

  // 消息列表自动滚底（流式跟随）
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, steps]);

  /** 导出当前任务对话为 .md / .html：主进程弹原生保存对话框并生成文件 */
  /** 选项按钮点击 → 作为回复直接发送（复用跟进链路：task.control start + instruction） */
  const sendOptionReply = async (option: string): Promise<void> => {
    if (!activeTaskId) return;
    const api = requireApi();
    useChatStore.getState().appendUserMessage(activeTaskId, option);
    useChatStore.getState().clearAwaiting();
    useChatStore.getState().resetSteps();
    await api.taskControl({
      taskId: activeTaskId,
      action: 'start',
      modelId: useAppStore.getState().currentModel,
      mode: 'craft',
      instruction: option,
    });
    useAppStore.getState().setRuntimeState('running');
  };

  const exportChat = async (format: 'md' | 'html'): Promise<void> => {
    if (!task) return;
    const api = requireApi();
    const result = await api.chatExport(task.id, format);
    if (result.ok) {
      void api.fsReveal(result.path);
    }
  };

  return (
    <div className="wb-chat__inner">
      {/* 1. 任务头 */}
      <div className="wb-chat__header">
        <div className="wb-chat__title" title={task?.title ?? '未选择任务'}>
          {task?.title ?? 'WDBuddy'}
        </div>
        {task && (
          <div className="wb-chat__badges">
            <span className={`wb-badge wb-badge--${task.mode === 'craft' ? 'running' : task.mode === 'plan' ? 'info' : 'neutral'}`}>
              {task.mode === 'plan' ? 'Plan' : task.mode === 'craft' ? 'Craft' : 'Ask'}
            </span>
            <span className="wb-badge wb-badge--neutral">{task.modelId === 'auto' ? 'Auto' : task.modelId}</span>
            {task.maxContext && <span className="wb-badge wb-badge--info">Max</span>}
          </div>
        )}
        <div className="wb-chat__spacer" />
        {task && (
          <>
            <button
              className="wb-btn wb-btn--ghost wb-btn--small"
              title="导出对话为 Markdown"
              onClick={() => void exportChat('md')}
            >
              ⬇ MD
            </button>
            <button
              className="wb-btn wb-btn--ghost wb-btn--small"
              title="导出对话为 HTML"
              onClick={() => void exportChat('html')}
            >
              ⬇ HTML
            </button>
          </>
        )}
      </div>

      {/* 2. 消息列表（虚拟滚动容器） */}
      <div className="wb-chat__messages" ref={listRef}>
        {messages.length === 0 && !plan && (
          <div className="wb-chat__welcome">
            <div className="wb-chat__welcome-logo">◆</div>
            <div className="wb-chat__welcome-title">你好，我是 WDBuddy</div>
            <div className="wb-chat__welcome-desc">说一句话，我直接把活干了——文档、表格、PPT、网页、调研报告……</div>
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}

        {/* PlanCard（Plan 模式，§4.4） */}
        {plan && <PlanCard steps={plan} />}

        {/* StepTimeline（过程可见，FR-09） */}
        {steps.length > 0 && <StepTimeline steps={steps} />}

        {/* 权限确认卡片（FR-04） */}
        {permissionRequest && <PermissionRequestCard request={permissionRequest} />}

        {/* 等待用户回复卡片（user.ask：Agent 提问后暂停，醒目提示；
            options 存在时渲染可点选项按钮，点击即作为回复发送） */}
        {awaitingInput && (
          <div className="wb-await" role="status">
            <div className="wb-await__header">⏸ 等待你的回复</div>
            {awaitingInput.question && (
              <div className="wb-await__question">{awaitingInput.question}</div>
            )}
            {awaitingInput.options && awaitingInput.options.length > 0 && (
              <div className="wb-await__options">
                {awaitingInput.options.map((opt) => (
                  <button
                    key={opt}
                    className="wb-await__option"
                    onClick={() => void sendOptionReply(opt)}
                  >
                    {opt}
                  </button>
                ))}
              </div>
            )}
            <div className="wb-await__hint">点击选项或在下 方输入框回复，我会接着继续执行</div>
          </div>
        )}
      </div>

      {/* 3. 输入区 */}
      <Composer />
    </div>
  );
}
