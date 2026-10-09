/**
 * SettingsPage — 设置页（§4.7）
 * 分组：通用 / 模型 / 权限与安全 / 积分与账号 / 关于
 */
import React, { useEffect, useState } from 'react';
import type { AppSettings, ModelId, ProviderKeyEntry } from '@wdbuddy/shared-types';
import { BUILTIN_MODEL_DEFAULTS, BUILTIN_MODEL_IDS, BUILTIN_MODEL_NAMES, DEFAULT_APP_SETTINGS } from '@wdbuddy/shared-types';
import { requireApi } from '../../shared/ipc';
import { useAppStore } from '../../shared/store';

type SettingsTab = 'general' | 'models' | 'permissions' | 'account' | 'about';

const TABS: Array<{ key: SettingsTab; label: string }> = [
  { key: 'general', label: '通用' },
  { key: 'models', label: '模型' },
  { key: 'permissions', label: '权限与安全' },
  { key: 'account', label: '积分与账号' },
  { key: 'about', label: '关于' },
];

export function SettingsPage({ onClose }: { onClose: () => void }): React.ReactElement {
  const [tab, setTab] = useState<SettingsTab>('general');
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_APP_SETTINGS);
  const [saved, setSaved] = useState(false);
  const [keyEntries, setKeyEntries] = useState<ProviderKeyEntry[]>([]);
  /** modelId → 模型名覆盖（本地编辑态，保存时写入 settings.models.apiModelOverrides） */
  const [apiModels, setApiModels] = useState<Partial<Record<ModelId, string>>>({});
  /** modelId → 输入中尚未提交的 API Key 草稿（底部"保存"统一提交；关闭前警告，防静默丢失） */
  const [draftKeys, setDraftKeys] = useState<Partial<Record<ModelId, string>>>({});

  useEffect(() => {
    void requireApi()
      .settingsGet()
      .then((s) => {
        setSettings(s);
        setApiModels(s.models.apiModelOverrides ?? {});
      });
    void requireApi()
      .providerKeyList()
      .then((r) => setKeyEntries(r.keys));
  }, []);

  const save = async (): Promise<void> => {
    const api = requireApi();
    // 修复（关键）：底部"保存"此前只写 AppSettings，Key 输入框内容被静默丢弃 →
    // 用户点保存→关闭→重开显示"未配置"。现在先统一提交所有 Key 草稿。
    for (const [id, k] of Object.entries(draftKeys)) {
      const t = (k ?? '').trim();
      if (t) await api.providerKeySet(id as ModelId, t);
    }
    setDraftKeys({});
    void api
      .providerKeyList()
      .then((r) => setKeyEntries(r.keys))
      .catch(() => undefined);
    // 模型名覆盖：剔除空白项后合并进 settings（空 = 恢复默认）
    const overrides: Partial<Record<ModelId, string>> = {};
    for (const [k, v] of Object.entries(apiModels)) {
      const t = (v ?? '').trim();
      if (t) overrides[k as ModelId] = t;
    }
    const next: AppSettings = { ...settings, models: { ...settings.models, apiModelOverrides: overrides } };
    await api.settingsSet(next);
    setSettings(next);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  /** 关闭守卫：存在未提交 Key 草稿时确认，防止静默丢失 */
  const closeWithGuard = (): void => {
    const hasDraft = Object.values(draftKeys).some((k) => (k ?? '').trim().length > 0);
    if (hasDraft && !window.confirm('检测到未保存的 API Key 输入，关闭后将丢失。确定关闭吗？')) return;
    onClose();
  };

  /** 全局弹窗（App 层）保存/删除自定义 Provider 后同步本地 settings，避免旧列表被“保存”回写。
   *  深比较全部字段：旧实现仅比 id——编辑名称/BaseURL/API Key 后 id 不变，
   *  本地列表不刷新，设置页仍显示旧值，用户误以为"保存没生效"。 */
  const storeCustomProviders = useAppStore((s) => s.customProviders);
  useEffect(() => {
    setSettings((prev) => {
      const same = JSON.stringify(prev.models.customProviders) === JSON.stringify(storeCustomProviders);
      return same ? prev : { ...prev, models: { ...prev.models, customProviders: storeCustomProviders } };
    });
    // 弹窗可能写入新密钥 → 刷新脱敏密钥状态（自定义 Provider 行展示"密钥已配置"）
    void requireApi()
      .providerKeyList()
      .then((r) => setKeyEntries(r.keys))
      .catch(() => undefined);
  }, [storeCustomProviders]);

  /** 删除自定义 Provider：移除列表 + 清理密钥（主进程热更新 Runtime 网关） */
  const handleRemove = async (id: string): Promise<void> => {
    const api = requireApi();
    const next: AppSettings = {
      ...settings,
      models: { ...settings.models, customProviders: settings.models.customProviders.filter((p) => p.id !== id) },
    };
    await api.settingsSet(next);
    await api.providerKeySet(id as ModelId, '');
    setDraftKeys((prev) => {
      const n = { ...prev };
      delete n[id as ModelId];
      return n;
    });
    useAppStore.getState().setCustomProviders(next.models.customProviders);
    setSettings(next);
  };

  return (
    <div className="wb-modal-mask" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wb-modal" style={{ minWidth: 640 }}>
        <div className="wb-modal__header">设置</div>
        <div className="wb-settings__body">
          <div className="wb-settings__nav">
            {TABS.map((t) => (
              <button
                key={t.key}
                className={`wb-settings__nav-item ${tab === t.key ? 'wb-settings__nav-item--active' : ''}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="wb-settings__content">
            {tab === 'general' && (
              <>
                <SettingsRow label="语言">
                  <select className="wb-select" value={settings.general.language} onChange={(e) => setSettings({ ...settings, general: { ...settings.general, language: e.target.value as AppSettings['general']['language'] } })}>
                    <option value="zh-CN">简体中文</option>
                    <option value="en-US">English</option>
                  </select>
                </SettingsRow>
                <SettingsRow label="主题">
                  <select className="wb-select" value={settings.general.theme} onChange={(e) => {
                    const theme = e.target.value as AppSettings['general']['theme'];
                    setSettings({ ...settings, general: { ...settings.general, theme } });
                    localStorage.setItem('wb-theme', theme);
                    document.documentElement.setAttribute('data-theme', theme === 'auto' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : theme);
                  }}>
                    <option value="light">浅色</option>
                    <option value="dark">深色</option>
                    <option value="auto">跟随系统</option>
                  </select>
                </SettingsRow>
                <SettingsRow label="开机自启">
                  <input type="checkbox" checked={settings.general.launchAtLogin} onChange={(e) => setSettings({ ...settings, general: { ...settings.general, launchAtLogin: e.target.checked } })} />
                </SettingsRow>
                <SettingsRow label="遥测（默认关闭，NFR-06）">
                  <input type="checkbox" checked={settings.general.telemetry} onChange={(e) => setSettings({ ...settings, general: { ...settings.general, telemetry: e.target.checked } })} />
                </SettingsRow>
              </>
            )}

            {tab === 'models' && (
              <>
                <SettingsRow label="默认模型">
                  <select className="wb-select" value={settings.models.defaultModelId} onChange={(e) => setSettings({ ...settings, models: { ...settings.models, defaultModelId: e.target.value as AppSettings['models']['defaultModelId'] } })}>
                    <option value="auto">Auto（自动选择）</option>
                    {BUILTIN_MODEL_IDS.map((id) => (
                      <option key={id} value={id}>
                        {BUILTIN_MODEL_NAMES[id] ?? id}
                      </option>
                    ))}
                    {settings.models.customProviders.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </SettingsRow>

                {BUILTIN_MODEL_IDS.map((id) => {
                  const entry = keyEntries.find((k) => k.modelId === id);
                  return (
                    <SettingsRow key={id} label={`${BUILTIN_MODEL_NAMES[id] ?? id} API Key / 模型名`}>
                      <ProviderKeyRow
                        masked={entry?.masked ?? ''}
                        value={draftKeys[id] ?? ''}
                        onChange={(v) => setDraftKeys((prev) => ({ ...prev, [id]: v }))}
                        onImmediateSave={async (k) => {
                          await requireApi().providerKeySet(id, k);
                          setDraftKeys((prev) => {
                            const n = { ...prev };
                            delete n[id];
                            return n;
                          });
                          void requireApi()
                            .providerKeyList()
                            .then((r) => setKeyEntries(r.keys))
                            .catch(() => undefined);
                        }}
                      />
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
                        <input
                          className="wb-input"
                          style={{ width: 260 }}
                          value={apiModels[id] ?? ''}
                          placeholder={`模型名（默认 ${BUILTIN_MODEL_DEFAULTS[id] ?? ''}）`}
                          onChange={(e) => setApiModels((prev) => ({ ...prev, [id]: e.target.value }))}
                        />
                      </div>
                    </SettingsRow>
                  );
                })}

                <SettingsRow label="自定义 Provider">
                  <div style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {settings.models.customProviders.length === 0 && (
                      <span style={{ color: 'var(--wb-text-secondary)' }}>
                        暂无自定义 Provider（OpenAI 兼容 API：Base URL + 模型名 + API Key，密钥存系统钥匙串）
                      </span>
                    )}
                    {settings.models.customProviders.map((p) => {
                      const entry = keyEntries.find((k) => k.modelId === p.id);
                      return (
                        <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                          <span>
                            {p.name}（{p.baseUrl} · {p.apiModel}）
                          </span>
                          <span
                            style={{
                              fontSize: 11,
                              color: entry?.hasKey ? 'var(--wb-color-success)' : 'var(--wb-color-warning)',
                            }}
                          >
                            {entry?.hasKey ? `密钥已配置（${entry.masked}）` : '密钥未配置'}
                          </span>
                          <button
                            className="wb-btn wb-btn--ghost wb-btn--small"
                            onClick={() => useAppStore.getState().setCustomProviderModal({ initial: p, switchOnSave: false })}
                          >
                            编辑
                          </button>
                          <button
                            className="wb-btn wb-btn--ghost wb-btn--small"
                            onClick={() => void handleRemove(p.id)}
                          >
                            删除
                          </button>
                        </div>
                      );
                    })}
                    <div>
                      <button
                        className="wb-btn wb-btn--default wb-btn--small"
                        onClick={() => useAppStore.getState().setCustomProviderModal({ initial: null, switchOnSave: false })}
                      >
                        ＋ 添加自定义 Provider
                      </button>
                    </div>
                  </div>
                </SettingsRow>
              </>
            )}

            {tab === 'permissions' && (
              <>
                <SettingsRow label="默认权限级别">
                  <select className="wb-select" value={settings.permissions.defaultPermission} onChange={(e) => setSettings({ ...settings, permissions: { ...settings.permissions, defaultPermission: e.target.value as AppSettings['permissions']['defaultPermission'] } })}>
                    <option value="default">默认权限（敏感操作逐次确认）</option>
                    <option value="full">完全访问（免确认，慎用）</option>
                  </select>
                </SettingsRow>
                <SettingsRow label="shell 执行前确认">
                  <input type="checkbox" checked={settings.permissions.confirmBeforeShell} onChange={(e) => setSettings({ ...settings, permissions: { ...settings.permissions, confirmBeforeShell: e.target.checked } })} />
                </SettingsRow>
                <SettingsRow label="工作空间外写入确认">
                  <input type="checkbox" checked={settings.permissions.confirmBeforeWriteOutsideWorkspace} onChange={(e) => setSettings({ ...settings, permissions: { ...settings.permissions, confirmBeforeWriteOutsideWorkspace: e.target.checked } })} />
                </SettingsRow>
                <SettingsRow label="授权目录">
                  <div style={{ fontSize: 12 }}>
                    {settings.workspaces.length === 0 ? '（无）' : settings.workspaces.map((w) => `📁 ${w}`).join('\n')}
                  </div>
                </SettingsRow>
              </>
            )}

            {tab === 'account' && (
              <>
                <SettingsRow label="登录状态">
                  <button className="wb-btn wb-btn--default" onClick={() => undefined}>微信扫码登录</button>
                </SettingsRow>
                <SettingsRow label="积分余额 / 用量明细">
                  <div style={{ fontSize: 12, color: 'var(--wb-text-secondary)' }}>登录后展示余额与模型调用扣减明细（FR-21）</div>
                </SettingsRow>
                <SettingsRow label="退出登录">
                  <button className="wb-btn wb-btn--ghost" onClick={() => undefined}>退出</button>
                </SettingsRow>
              </>
            )}

            {tab === 'about' && (
              <>
                <SettingsRow label="版本">v1.0.0</SettingsRow>
                <SettingsRow label="检查更新">
                  <button className="wb-btn wb-btn--default" onClick={() => undefined}>检查更新</button>
                </SettingsRow>
                <SettingsRow label="导出诊断日志">
                  <button className="wb-btn wb-btn--default" onClick={() => undefined}>导出（用户主动，日志已脱敏）</button>
                </SettingsRow>
              </>
            )}
          </div>
        </div>
        <div className="wb-modal__footer">
          {saved && <span style={{ color: 'var(--wb-color-success)', marginRight: 8 }}>已保存</span>}
          <button className="wb-btn wb-btn--ghost" onClick={closeWithGuard}>关闭</button>
          <button
            className="wb-btn wb-btn--primary"
            title="保存当前全部设置项（含已输入未提交的 API Key）"
            onClick={() => void save()}
          >
            保存
          </button>
        </div>
      </div>
    </div>
  );
}

function SettingsRow({ label, children }: { label: string; children: React.ReactNode }): React.ReactElement {
  return (
    <div className="wb-settings__row">
      <div className="wb-settings__row-label">{label}</div>
      <div className="wb-settings__row-value">{children}</div>
    </div>
  );
}

/** Provider Key 行：受控草稿输入 + 立即保存（草稿由父组件统一管理，底部"保存"亦会提交） */
function ProviderKeyRow({
  masked,
  value,
  onChange,
  onImmediateSave,
}: {
  masked: string;
  value: string;
  onChange: (v: string) => void;
  onImmediateSave: (apiKey: string) => Promise<void>;
}): React.ReactElement {
  const [saving, setSaving] = useState(false);
  const canSave = value.trim().length > 0;

  const save = async (): Promise<void> => {
    setSaving(true);
    try {
      await onImmediateSave(value.trim());
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
      <input
        className="wb-input"
        style={{ width: 260 }}
        type="password"
        value={value}
        placeholder={masked ? `已配置（${masked}），输入新值覆盖` : '未配置，输入 API Key'}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
      />
      <button className="wb-btn wb-btn--default wb-btn--small" disabled={!canSave || saving} onClick={() => void save()}>
        {saving ? '保存中…' : '立即保存'}
      </button>
    </div>
  );
}
