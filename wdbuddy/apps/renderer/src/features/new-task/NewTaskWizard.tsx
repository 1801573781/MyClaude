/**
 * NewTaskWizard — 新建任务向导（§4.6）
 * 字段：任务描述（💡增强）/ 任务模式 / 工作空间 / 操作权限（完全访问需二次确认红字）/
 *      模型 / Max 模式 / 触发方式（手动/定时 cron/事件监听）
 */
import React, { useState } from 'react';
import type { ModelId, TaskMode, TaskTrigger } from '@wdbuddy/shared-types';
import { BUILTIN_MODEL_IDS, BUILTIN_MODEL_NAMES } from '@wdbuddy/shared-types';
import { requireApi } from '../../shared/ipc';
import { useAppStore } from '../../shared/store';

export function NewTaskWizard({ onClose }: { onClose: () => void }): React.ReactElement {
  const [description, setDescription] = useState('');
  const [mode, setMode] = useState<TaskMode>('plan');
  const [workspaceUri, setWorkspaceUri] = useState<string | null>(null);
  const [permission, setPermission] = useState<'default' | 'full'>('default');
  const [confirmFull, setConfirmFull] = useState(false);
  // 模型默认取顶栏全局选择（含自定义）；custom:api 为入口占位，不可作为模型值
  const initialModel = useAppStore.getState().currentModel;
  const [modelId, setModelId] = useState<ModelId>(initialModel === 'custom:api' ? 'auto' : initialModel);
  const [maxContext, setMaxContext] = useState(false);
  const [triggerType, setTriggerType] = useState<'manual' | 'cron' | 'event'>('manual');
  const [cronExpr, setCronExpr] = useState('0 9 * * 1');
  const [watchDir, setWatchDir] = useState('');
  const [enhancing, setEnhancing] = useState(false);
  const [creating, setCreating] = useState(false);
  const customProviders = useAppStore((s) => s.customProviders);

  /** 提示词增强（§4.6：输入后可点 💡 增强为标题与结构化指令） */
  const enhance = async (): Promise<void> => {
    if (!description.trim()) return;
    setEnhancing(true);
    try {
      const r = await requireApi().promptEnhance(description);
      setDescription(r.enhanced);
    } finally {
      setEnhancing(false);
    }
  };

  const submit = async (): Promise<void> => {
    if (!description.trim() || creating) return;
    setCreating(true);
    try {
      const trigger: TaskTrigger =
        triggerType === 'cron'
          ? { type: 'cron', expr: cronExpr }
          : triggerType === 'event'
            ? { type: 'event', watch: watchDir ? [watchDir] : [] }
            : { type: 'manual' };

      const api = requireApi();
      const { taskId } = await api.taskCreate({
        description: description.trim(),
        mode,
        workspaceUri,
        permission,
        modelId,
        maxContext,
        trigger,
      });
      await api.taskControl({ taskId, action: 'start' });
      useAppStore.getState().setActiveTaskId(taskId);
      onClose();
    } finally {
      setCreating(false);
    }
  };

  const canSubmit = description.trim().length > 0 && (permission === 'default' || confirmFull);

  return (
    <div className="wb-modal-mask" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wb-modal">
        <div className="wb-modal__header">新建任务</div>
        <div className="wb-modal__body">
          {/* 任务描述 */}
          <div className="wb-field">
            <label className="wb-field__label">任务描述 *</label>
            <textarea
              className="wb-textarea"
              placeholder="例如：帮我做一个环保节日日历网页，兼容电脑和手机"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
            <button className="wb-btn wb-btn--ghost wb-btn--small" onClick={() => void enhance()} disabled={enhancing || !description.trim()}>
              {enhancing ? '增强中…' : '💡 增强为结构化指令'}
            </button>
          </div>

          {/* 任务模式（三选一卡片，FR-02） */}
          <div className="wb-field">
            <label className="wb-field__label">任务模式</label>
            <div className="wb-modecards">
              <ModeCard value="ask" title="Ask" desc="只问答，不操作文件" active={mode === 'ask'} onClick={setMode} />
              <ModeCard value="plan" title="Plan" desc="先出计划，批准后执行" active={mode === 'plan'} onClick={setMode} />
              <ModeCard value="craft" title="Craft" desc="全自主执行交付" active={mode === 'craft'} onClick={setMode} />
            </div>
          </div>

          {/* 工作空间 */}
          <div className="wb-field">
            <label className="wb-field__label">工作空间（授权 Agent 读写的文件夹）</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="wb-input" value={workspaceUri ?? ''} readOnly placeholder="点击右侧选择目录" />
              <button
                className="wb-btn wb-btn--default"
                style={{ flexShrink: 0, whiteSpace: 'nowrap' }}
                onClick={async () => {
                  const r = await requireApi().workspaceGrant();
                  if (r.ok && r.uri) setWorkspaceUri(r.uri);
                }}
              >
                选择目录
              </button>
            </div>
          </div>

          {/* 操作权限（FR-04：完全访问需二次确认+红色警示） */}
          <div className="wb-field">
            <label className="wb-field__label">操作权限</label>
            <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
              <label>
                <input type="radio" checked={permission === 'default'} onChange={() => setPermission('default')} /> 默认权限（敏感操作逐次确认）
              </label>
              <label>
                <input type="radio" checked={permission === 'full'} onChange={() => setPermission('full')} /> 完全访问
              </label>
            </div>
            {permission === 'full' && (
              <div className="wb-field__danger">
                <label>
                  <input type="checkbox" checked={confirmFull} onChange={(e) => setConfirmFull(e.target.checked)} />
                  我已知晓风险：完全访问模式下 Agent 执行命令、写文件均免确认，请谨慎授权
                </label>
              </div>
            )}
          </div>

          {/* 模型 / Max */}
          <div className="wb-field wb-field--row">
            <div style={{ flex: 1 }}>
              <label className="wb-field__label">模型</label>
              <select className="wb-select" value={modelId} onChange={(e) => setModelId(e.target.value as ModelId)}>
                <option value="auto">Auto（自动选择）</option>
                <option value="deepseek">DeepSeek</option>
                <option value="zhipu-glm">智谱 GLM</option>
                <option value="kimi">Kimi</option>
                <option value="minimax">MiniMax</option>
                <option value="hunyuan">混元</option>
              </select>
            </div>
            <div>
              <label className="wb-field__label">Max 模式</label>
              <label className="wb-badge wb-badge--info">
                <input type="checkbox" checked={maxContext} onChange={(e) => setMaxContext(e.target.checked)} /> 增强上下文
              </label>
            </div>
          </div>

          {/* 触发方式（FR-19 规则任务） */}
          <div className="wb-field">
            <label className="wb-field__label">触发方式</label>
            <div style={{ display: 'flex', gap: 16 }}>
              <label><input type="radio" checked={triggerType === 'manual'} onChange={() => setTriggerType('manual')} /> 手动</label>
              <label><input type="radio" checked={triggerType === 'cron'} onChange={() => setTriggerType('cron')} /> 定时</label>
              <label><input type="radio" checked={triggerType === 'event'} onChange={() => setTriggerType('event')} /> 事件</label>
            </div>
            {triggerType === 'cron' && (
              <input className="wb-input" placeholder="cron 表达式，如 0 9 * * 1（每周一 9 点）" value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} />
            )}
            {triggerType === 'event' && (
              <input className="wb-input" placeholder="监听目录（工作空间内相对路径），新文件到达即触发" value={watchDir} onChange={(e) => setWatchDir(e.target.value)} />
            )}
          </div>
        </div>
        <div className="wb-modal__footer">
          <button className="wb-btn wb-btn--ghost" onClick={onClose}>取消</button>
          <button className="wb-btn wb-btn--primary" disabled={!canSubmit || creating} onClick={() => void submit()}>
            {creating ? '创建中…' : '创建任务'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ModeCard({
  value,
  title,
  desc,
  active,
  onClick,
}: {
  value: TaskMode;
  title: string;
  desc: string;
  active: boolean;
  onClick: (v: TaskMode) => void;
}): React.ReactElement {
  return (
    <button
      className={`wb-modecard ${active ? 'wb-modecard--active' : ''}`}
      onClick={() => onClick(value)}
      type="button"
    >
      <div className="wb-modecard__title">{title}</div>
      <div className="wb-modecard__desc">{desc}</div>
    </button>
  );
}
