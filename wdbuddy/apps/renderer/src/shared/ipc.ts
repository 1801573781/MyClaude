/**
 * IPC 封装：类型化 window.wdbuddy 访问（preload 暴露的白名单 API）
 */
import type { WdBuddyApi } from '../../../desktop/src/preload/index';

declare global {
  interface Window {
    wdbuddy: WdBuddyApi;
  }
}

/** 获取 preload API（非 Electron 环境返回 null，便于浏览器调试） */
export function getApi(): WdBuddyApi | null {
  if (typeof window !== 'undefined' && window.wdbuddy) {
    return window.wdbuddy;
  }
  return null;
}

export function requireApi(): WdBuddyApi {
  const api = getApi();
  if (!api) throw new Error('preload API 不可用（非 Electron 环境）');
  return api;
}
