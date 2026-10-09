/**
 * PreviewTab — 预览（§4.5）
 * 渲染 HTML / 图片 / PDF / Markdown 成果；HTML 在沙箱 iframe 中渲染（NFR-03）
 */
import React from 'react';
import type { Artifact } from '@wdbuddy/shared-types';
import { useArtifactsStore } from './store';

export function PreviewTab(): React.ReactElement {
  const artifacts = useArtifactsStore((s) => s.artifacts);
  const selectedId = useArtifactsStore((s) => s.selectedArtifactId);
  const select = useArtifactsStore((s) => s.select);

  const previewable = artifacts.filter(
    (a) =>
      a.kind === 'file' &&
      (a.mimeType.startsWith('image/') ||
        a.mimeType === 'text/html' ||
        a.mimeType === 'application/pdf' ||
        a.mimeType === 'text/markdown')
  );
  const current: Artifact | undefined =
    previewable.find((a) => a.id === selectedId) ?? previewable[0];

  if (previewable.length === 0) {
    return (
      <div className="wb-artifacts__empty">
        <div className="wb-empty__icon">🖼</div>
        <div className="wb-empty__title">暂无可预览成果</div>
        <div className="wb-empty__desc">HTML / 图片 / PDF / Markdown 成果将在此展示</div>
      </div>
    );
  }

  return (
    <div className="wb-preview">
      {/* 可预览产物切换条 */}
      {previewable.length > 1 && (
        <div className="wb-preview__switcher">
          {previewable.map((a) => (
            <button
              key={a.id}
              className={`wb-preview__chip ${a.id === current?.id ? 'wb-preview__chip--active' : ''}`}
              onClick={() => select(a.id)}
            >
              {shortName(a)}
            </button>
          ))}
        </div>
      )}

      {/* 沙箱渲染：HTML 用 iframe sandbox（禁脚本外逃，NFR-03）；图片/PDF 直渲 */}
      {current?.mimeType === 'text/html' && current.path && (
        <iframe
          className="wb-preview__iframe"
          src={`wd-file://preview?path=${encodeURIComponent(current.path)}`}
          sandbox="allow-same-origin"
          title="HTML 预览（沙箱）"
        />
      )}
      {current?.mimeType.startsWith('image/') && (
        <div className="wb-preview__image-wrap">
          <img
            className="wb-preview__image"
            src={`wd-file://preview?path=${encodeURIComponent(current.path ?? '')}`}
            alt={current.path ?? '预览图'}
          />
        </div>
      )}
      {(current?.mimeType === 'application/pdf' || current?.mimeType === 'text/markdown') && (
        <div className="wb-preview__plain">
          <div className="wb-preview__plain-title">{current?.path}</div>
          <div className="wb-preview__plain-hint">
            {current?.mimeType === 'application/pdf' ? 'PDF 成果，点击底部"打开目录"用系统阅读器查看' : 'Markdown 成果'}
          </div>
        </div>
      )}
    </div>
  );
}

function shortName(a: Artifact): string {
  const p = a.path ?? a.id;
  const idx = p.lastIndexOf('/');
  const idx2 = p.lastIndexOf('\\');
  return p.slice(Math.max(idx, idx2) + 1) || p;
}
