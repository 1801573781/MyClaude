/**
 * PermissionRequestCard — 权限确认卡片（§4.4，FR-04）
 * 默认权限下 Agent 请求敏感操作（shell / 工作空间外写入）时弹出：
 * [允许一次] [本任务总是允许] [拒绝]
 */
import React from 'react';
import type { PermissionRequest } from '@wdbuddy/shared-types';
import { useChatStore } from './store';

const TOOL_LABELS: Record<string, string> = {
  'shell.exec': '执行 Shell 命令',
  'fs.write': '写入文件',
  'fs.delete': '删除文件',
  'fs.read': '读取文件',
  'browser.open': '打开网页',
};

export function PermissionRequestCard({ request }: { request: PermissionRequest }): React.ReactElement {
  const respond = useChatStore((s) => s.respondPermission);

  const toolLabel = TOOL_LABELS[request.tool] ?? request.tool;
  const detail =
    request.tool === 'shell.exec'
      ? String(request.args['command'] ?? '')
      : String(request.args['path'] ?? request.args['url'] ?? '');

  return (
    <div className="wb-perm">
      <div className="wb-perm__header">
        <span className="wb-perm__icon">🛡️</span>
        <span className="wb-perm__title">权限确认</span>
      </div>
      <div className="wb-perm__body">
        <div className="wb-perm__row">
          <span className="wb-perm__label">操作：</span>
          <span className="wb-perm__value">{toolLabel}</span>
        </div>
        {detail && (
          <div className="wb-perm__row">
            <span className="wb-perm__label">目标：</span>
            <code className="wb-perm__detail">{detail}</code>
          </div>
        )}
        <div className="wb-perm__row">
          <span className="wb-perm__label">原因：</span>
          <span className="wb-perm__value">{request.reason}</span>
        </div>
      </div>
      <div className="wb-perm__footer">
        <button className="wb-btn wb-btn--primary" onClick={() => void respond('allow-once')}>
          允许一次
        </button>
        <button className="wb-btn wb-btn--default" onClick={() => void respond('allow-always-task')}>
          本任务总是允许
        </button>
        <button className="wb-btn wb-btn--danger" onClick={() => void respond('deny')}>
          拒绝
        </button>
      </div>
    </div>
  );
}
