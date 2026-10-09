/**
 * FilesTab — 文件（§4.5）
 * 本任务交付文件列表（文件名/类型/大小/生成时间），点击预览，打开所在目录
 */
import React from 'react';
import { useArtifactsStore } from './store';
import { requireApi } from '../../shared/ipc';

const KIND_ICON: Record<string, string> = {
  'text/html': '🌐',
  'text/markdown': '📝',
  'text/plain': '📄',
  'application/pdf': '📕',
  'application/json': '🧾',
  'image/png': '🖼',
  'image/jpeg': '🖼',
  'image/svg+xml': '🖼',
};

/** 类型显示：直接用文件扩展名（.pptx / .html / .md），最简洁直观 */
function kindLabel(_mimeType: string, path?: string): string {
  const p = path ?? '';
  const dot = p.lastIndexOf('.');
  if (dot >= 0 && dot > p.lastIndexOf('/') && dot > p.lastIndexOf('\\')) {
    return p.slice(dot);
  }
  return '文件';
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function FilesTab(): React.ReactElement {
  const artifacts = useArtifactsStore((s) => s.artifacts);
  const select = useArtifactsStore((s) => s.select);
  const setTab = useArtifactsStore((s) => s.setTab);

  const files = artifacts.filter((a) => a.kind === 'file');

  if (files.length === 0) {
    return (
      <div className="wb-artifacts__empty">
        <div className="wb-empty__icon">📂</div>
        <div className="wb-empty__title">暂无交付文件</div>
        <div className="wb-empty__desc">Agent 生成的文件将出现在这里</div>
      </div>
    );
  }

  return (
    <div className="wb-files">
      <table className="wb-files__table">
        <thead>
          <tr>
            <th>文件名</th>
            <th>类型</th>
            <th>版本</th>
            <th>时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {files.map((a) => (
            <tr key={a.id} className="wb-files__row">
              <td className="wb-files__name" title={a.path}>
                {KIND_ICON[a.mimeType] ?? '📄'} {shortName(a)}
              </td>
              <td>{kindLabel(a.mimeType, a.path)}</td>
              <td>v{a.version}</td>
              <td>{formatTime(a.createdAt)}</td>
              <td>
                <button
                  className="wb-btn wb-btn--ghost wb-btn--small"
                  onClick={() => {
                    select(a.id);
                    setTab('preview');
                  }}
                >
                  预览
                </button>
                <button
                  className="wb-btn wb-btn--ghost wb-btn--small"
                  onClick={() => a.path && void requireApi().fsReveal(a.path)}
                >
                  打开所在目录
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function shortName(a: { path?: string; id: string }): string {
  const p = a.path ?? a.id;
  return p.slice(Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\')) + 1) || p;
}
