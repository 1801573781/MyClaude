/**
 * chat feature store（中栏，§4.4）
 * - 订阅 runtime 统一事件流（§10.2）：token 追加、步骤更新、计划待审批、权限请求、完成
 * - 流式 token 用 rAF 批量合并上屏（≤16ms 一帧，NFR-01）
 */
import { create } from 'zustand';
import type {
  Message,
  PermissionRequest,
  PlanStep,
  RuntimeEvent,
  Task,
} from '@wdbuddy/shared-types';
import { useAppStore } from '../../shared/store';
import { requireApi } from '../../shared/ipc';

export interface StreamingState {
  active: boolean;
  taskId: string | null;
  currentStepTitle: string;
  bufferedText: string;
}

interface ChatStore {
  task: Task | null;
  messages: Message[];
  steps: PlanStep[];
  plan: PlanStep[] | null;          // 待审批计划（PlanCard）
  permissionRequest: PermissionRequest | null;
  /** Agent 提问等待用户回复（user.ask）：消息流中醒目卡片展示 */
  awaitingInput: { question: string; options?: string[] } | null;
  streaming: StreamingState;

  loadTask(taskId: string): Promise<void>;
  /** 跟进输入即时上屏（乐观追加；主进程 task:start 已同步落库，切回任务时不重复） */
  appendUserMessage(taskId: string, content: string): void;
  /** 发送时同步模式：任务头徽标立即反映本轮执行模式（主进程已同步落库） */
  syncTaskMode(mode: Task['mode']): void;
  /** 新一轮发送前清空旧步骤时间线（避免新旧步骤混排、旧步骤误打 ✓） */
  resetSteps(): void;
  /** 用户已回复（发送跟进）：清除等待卡片 */
  clearAwaiting(): void;
  clearPlan(): void;
  respondPermission(decision: 'allow-once' | 'allow-always-task' | 'deny'): Promise<void>;
  handleEvent(e: RuntimeEvent): void;
}

/** rAF 批量合并上屏（性能 §13） */
class RafBuffer {
  private pending = '';
  private scheduled = false;

  push(text: string, flush: (combined: string) => void): void {
    this.pending += text;
    if (this.scheduled) return;
    this.scheduled = true;
    requestAnimationFrame(() => {
      this.scheduled = false;
      const out = this.pending;
      this.pending = '';
      flush(out);
    });
  }
}

export const useChatStore = create<ChatStore>((set, get) => {
  const raf = new RafBuffer();

  return {
    task: null,
    messages: [],
    steps: [],
    plan: null,
    permissionRequest: null,
    awaitingInput: null,
    streaming: { active: false, taskId: null, currentStepTitle: '', bufferedText: '' },

    loadTask: async (taskId) => {
      const api = requireApi();
      const { tasks } = await api.taskList({ group: 'all' });
      const task = tasks.find((t) => t.id === taskId) ?? null;
      const { messages } = await api.taskMessages(taskId);
      const { steps } = await api.taskSteps(taskId);
      set({ task, messages, steps, plan: null, awaitingInput: null });
    },

    appendUserMessage: (taskId, content) => {
      set((s) => ({
        messages: [
          ...s.messages,
          {
            id: `user-${Date.now()}`,
            taskId,
            seq: (s.messages[s.messages.length - 1]?.seq ?? 0) + 1,
            role: 'user',
            content,
            createdAt: Date.now(),
          },
        ],
      }));
    },

    syncTaskMode: (mode) => {
      set((s) => (s.task ? { task: { ...s.task, mode } } : s));
    },

    resetSteps: () => {
      set({ steps: [] });
    },

    clearAwaiting: () => {
      set({ awaitingInput: null });
    },

    clearPlan: () => set({ plan: null }),

    respondPermission: async (decision) => {
      const req = get().permissionRequest;
      if (!req) return;
      const api = requireApi();
      await api.permissionRespond(req.requestId, decision);
      set({ permissionRequest: null });
    },

    handleEvent: (e) => {
      const activeTaskId = useAppStore.getState().activeTaskId;
      // 只处理当前任务的事件（多任务并存时其余仅刷新列表）
      if ('taskId' in e && e.taskId !== activeTaskId) {
        void useAppStore.getState().dispatchRuntimeEvent(e);
        return;
      }

      switch (e.type) {
        case 'plan.ready':
          set({ plan: e.steps, steps: e.steps });
          break;
        case 'step.update':
          set((s) => {
            // 按 id 替换；找不到（重规划生成新步骤 / 批准时用户编辑过计划）则按序号替换或追加，
            // 避免时间线停留在旧计划上丢失执行进度
            const byId = s.steps.findIndex((st) => st.id === e.step.id);
            let steps: PlanStep[];
            if (byId >= 0) {
              steps = s.steps.map((st) => (st.id === e.step.id ? e.step : st));
            } else {
              const bySeq = s.steps.findIndex((st) => st.seq === e.step.seq);
              if (bySeq >= 0) {
                steps = [...s.steps];
                steps[bySeq] = e.step;
              } else {
                steps = [...s.steps, e.step];
              }
            }
            return { steps, streaming: { ...s.streaming, currentStepTitle: e.step.title } };
          });
          break;
        case 'token.delta':
          raf.push(e.text, (combined) => {
            set((s) => {
              const msgs = [...s.messages];
              const last = msgs[msgs.length - 1];
              if (last && last.role === 'assistant') {
                msgs[msgs.length - 1] = { ...last, content: last.content + combined };
              } else {
                msgs.push({
                  id: `stream-${Date.now()}`,
                  taskId: e.taskId,
                  seq: (last?.seq ?? 0) + 1,
                  role: 'assistant',
                  content: combined,
                  createdAt: Date.now(),
                });
              }
              return { messages: msgs };
            });
          });
          useAppStore.getState().setRuntimeState('running');
          set((s) => ({
            streaming: { ...s.streaming, active: true, taskId: e.taskId },
            awaitingInput: null,
          }));
          break;
        case 'permission.request':
          set({ permissionRequest: e.request });
          break;
        case 'task.done': {
          // 醒目完成提示：列出成果文件路径，告知去哪查看（修复"完成后无感知"）
          const files = [
            ...new Set(e.artifacts.filter((a) => a.kind === 'file' && a.path).map((a) => a.path)),
          ];
          const fileList = files.length > 0 ? files.map((f) => `\n📄 ${f}`).join('') : '';
          set((s) => ({
            messages: [
              ...s.messages,
              {
                id: `done-${Date.now()}`,
                taskId: e.taskId,
                seq: s.messages.length + 1,
                role: 'system',
                content:
                  files.length > 0
                    ? `✅ 任务已完成！生成的成果文件：${fileList}\n\n👉 已同步到右侧"文件"栏，可点击查看/预览；点右栏"打开目录"直达文件位置。`
                    : '✅ 任务已完成！（本次任务无文件产物，回答内容见上方对话）',
                createdAt: Date.now(),
              },
            ],
            awaitingInput: null,
            streaming: { ...s.streaming, active: false, bufferedText: '' },
          }));
          void useTaskListRefresher();
          break;
        }
        case 'task.rejected':
          // 用户拒绝计划：清除 PlanCard，任务回草稿可重新对话
          set((s) => ({
            plan: null,
            awaitingInput: null,
            streaming: { ...s.streaming, active: false, bufferedText: '' },
          }));
          void useTaskListRefresher();
          break;
        case 'task.cancelled':
          // 用户停止任务：终止流式状态，提示已取消
          set((s) => ({
            messages: [
              ...s.messages,
              {
                id: `cancel-${Date.now()}`,
                taskId: e.taskId,
                seq: s.messages.length + 1,
                role: 'system',
                content: '⏹ 任务已停止',
                createdAt: Date.now(),
              },
            ],
            awaitingInput: null,
            streaming: { ...s.streaming, active: false, bufferedText: '' },
          }));
          void useTaskListRefresher();
          break;
        case 'task.awaiting.input':
          // Agent 提问完毕，暂停等待用户回复：醒目卡片展示问题原文，
          // 输入框自动聚焦并更换 placeholder（用户跟进后自动唤醒继续）
          set((s) => ({
            awaitingInput: { question: e.question, options: e.options },
            streaming: { ...s.streaming, active: false, bufferedText: '' },
          }));
          void useTaskListRefresher();
          break;
        case 'task.error':
          set((s) => ({
            messages: [
              ...s.messages,
              {
                id: `err-${Date.now()}`,
                taskId: e.taskId,
                seq: s.messages.length + 1,
                role: 'system',
                content: `⚠ 任务失败：${e.cause}`,
                createdAt: Date.now(),
              },
            ],
            awaitingInput: null,
            streaming: { ...s.streaming, active: false, bufferedText: '' },
          }));
          break;
        default:
          break;
      }
      // 注意：不可在此调用 dispatchRuntimeEvent(e) 回灌全局事件总线——
      // ChatPanel 的订阅即来自该总线，回灌会造成 handleEvent 无限同步递归
      // （历史 bug：task.error 时消息刷屏直至渲染崩溃）。全局侧（状态栏/侧栏）
      // 的事件消费由 App 根部的 useRuntimeEventBridge 统一完成，与此处互不干扰。
    },
  };
});

/** 任务完成后刷新侧栏列表 */
async function useTaskListRefresher(): Promise<void> {
  // 延迟 import 规避 feature 边界（§10.2 禁止跨 feature 直接 import）
  const { useTaskListStore } = await import('../task-list/store');
  await useTaskListStore.getState().refresh();
}
