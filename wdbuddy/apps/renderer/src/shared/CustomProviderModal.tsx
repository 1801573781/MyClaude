/**
 * CustomProviderModal — 自定义 OpenAI 兼容 Provider 表单（FR-06）
 * 新增/编辑共用：编辑时 API Key 留空表示保留原密钥。
 * 保存：settings.models.customProviders + providerKey:set（密钥入系统钥匙串），
 * 并同步全局 store；主进程 settings:set 后自动热更新 Runtime 网关。
 */
import React, { useState } from 'react';
import type { CustomProvider } from '@wdbuddy/shared-types';
import { requireApi } from './ipc';
import { useAppStore } from './store';

export interface CustomProviderModalProps {
  /** 编辑已有 Provider；新增时为 null */
  initial: CustomProvider | null;
  onClose: () => void;
  /** 保存成功回调（新增后调用方可据此切换 currentModel） */
  onSaved?: (provider: CustomProvider) => void;
}

/** 名称 → 稳定 id 片段（中文/字母/数字保留，其余折叠为 -） */
function slugify(name: string): string {
  const s = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return s || 'api';
}

export function CustomProviderModal({ initial, onClose, onSaved }: CustomProviderModalProps): React.ReactElement {
  const [name, setName] = useState(initial?.name ?? '');
  const [baseUrl, setBaseUrl] = useState(initial?.baseUrl ?? '');
  const [apiModel, setApiModel] = useState(initial?.apiModel ?? '');
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const editing = initial !== null;
  const canSubmit =
    name.trim().length > 0 &&
    baseUrl.trim().length > 0 &&
    apiModel.trim().length > 0 &&
    (editing || apiKey.trim().length > 0);

  const save = async (): Promise<void> => {
    setError('');
    const url = baseUrl.trim().replace(/\/+$/, '');
    if (!/^https?:\/\//i.test(url)) {
      setError('Base URL 需以 http:// 或 https:// 开头');
      return;
    }
    setSaving(true);
    try {
      const api = requireApi();
      const settings = await api.settingsGet();
      const id = editing
        ? initial.id
        : `custom:${slugify(name)}-${Math.random().toString(36).slice(2, 6)}`;
      const def: CustomProvider = {
        id,
        name: name.trim(),
        baseUrl: url,
        apiModel: apiModel.trim(),
        apiKeyRef: `provider:${id}`,
        protocol: 'openai-compatible',
      };
      const list = editing
        ? settings.models.customProviders.map((p) => (p.id === id ? def : p))
        : [...settings.models.customProviders, def];
      await api.settingsSet({ ...settings, models: { ...settings.models, customProviders: list } });
      // 新增必须录入 Key；编辑留空则保留原密钥
      if (!editing || apiKey.trim().length > 0) {
        await api.providerKeySet(id as `custom:${string}`, apiKey.trim());
      }
      useAppStore.getState().setCustomProviders(list);
      onSaved?.(def);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="wb-modal-mask" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wb-modal" style={{ minWidth: 480 }}>
        <div className="wb-modal__header">{editing ? '编辑自定义 Provider' : '添加自定义 Provider（OpenAI 兼容）'}</div>
        <div className="wb-modal__body">
          <div className="wb-field">
            <label className="wb-field__label">名称 *</label>
            <input
              className="wb-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：通义千问"
            />
          </div>
          <div className="wb-field">
            <label className="wb-field__label">Base URL *</label>
            <input
              className="wb-input"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
            />
          </div>
          <div className="wb-field">
            <label className="wb-field__label">模型名 *</label>
            <input
              className="wb-input"
              value={apiModel}
              onChange={(e) => setApiModel(e.target.value)}
              placeholder="请求体中的 model 字段，例如：qwen-max"
            />
          </div>
          <div className="wb-field">
            <label className="wb-field__label">API Key {editing ? '（留空保留原密钥）' : '*'}</label>
            <input
              className="wb-input"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-…"
              autoComplete="off"
            />
          </div>
          {error.length > 0 && (
            <div style={{ color: '#e5484d', fontSize: 12 }}>保存失败：{error}</div>
          )}
        </div>
        <div className="wb-modal__footer">
          <button className="wb-btn wb-btn--ghost" onClick={onClose}>取消</button>
          <button className="wb-btn wb-btn--primary" disabled={!canSubmit || saving} onClick={() => void save()}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}
