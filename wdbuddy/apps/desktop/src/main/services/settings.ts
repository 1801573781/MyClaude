/**
 * settings 服务（§4.7 / §14）
 * AppSettings 持久化于 SQLite settings 表，Schema 即分组结构。
 */
import type { AppSettings } from '@wdbuddy/shared-types';
import { DEFAULT_APP_SETTINGS } from '@wdbuddy/shared-types';
import type { TaskStore } from './task-store.js';

const SETTINGS_KEY = 'app-settings';

export class SettingsService {
  constructor(private readonly store: TaskStore) {}

  get(): AppSettings {
    const saved = this.store.getSetting<Partial<AppSettings>>(SETTINGS_KEY);
    if (!saved) return structuredClone(DEFAULT_APP_SETTINGS);
    // 深合并：保证新增字段有默认值（向后兼容）
    return mergeSettings(DEFAULT_APP_SETTINGS, saved);
  }

  set(next: AppSettings): AppSettings {
    this.store.setSetting(SETTINGS_KEY, next);
    return next;
  }

  patch(partial: Partial<AppSettings>): AppSettings {
    return this.set(mergeSettings(this.get(), partial));
  }
}

function mergeSettings(base: AppSettings, patch: Partial<AppSettings>): AppSettings {
  return {
    general: { ...base.general, ...(patch.general ?? {}) },
    models: { ...base.models, ...(patch.models ?? {}) },
    permissions: { ...base.permissions, ...(patch.permissions ?? {}) },
    workspaces: patch.workspaces ?? base.workspaces,
    composer: { ...base.composer, ...(patch.composer ?? {}) },
    remoteLink: { ...base.remoteLink, ...(patch.remoteLink ?? {}) },
  };
}
