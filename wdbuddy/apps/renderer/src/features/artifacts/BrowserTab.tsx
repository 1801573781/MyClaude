/**
 * BrowserTab — 浏览器（§4.5，FR-15）
 * 内嵌 webview，用于网页类成果的自测演示（可指定本地路径或 URL）
 */
import React from 'react';
import { useArtifactsStore } from './store';

export function BrowserTab(): React.ReactElement {
  const browserUrl = useArtifactsStore((s) => s.browserUrl);
  const browserInput = useArtifactsStore((s) => s.browserInput);
  const setBrowserUrl = useArtifactsStore((s) => s.setBrowserUrl);
  const setBrowserInput = useArtifactsStore((s) => s.setBrowserInput);

  const go = (): void => {
    const url = browserInput.trim();
    if (!url) return;
    // 本地文件路径 → wd-file 协议；URL 原样
    const final = /^(https?|wd-file):/i.test(url) ? url : `wd-file://preview?path=${encodeURIComponent(url)}`;
    setBrowserUrl(final);
  };

  return (
    <div className="wb-browser">
      <div className="wb-browser__bar">
        <input
          className="wb-input"
          placeholder="输入本地路径（如 D:\work\index.html）或 URL"
          value={browserInput}
          onChange={(e) => setBrowserInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && go()}
        />
        <button className="wb-btn wb-btn--primary wb-btn--small" onClick={go}>
          打开
        </button>
      </div>
      {browserUrl ? (
        <iframe
          className="wb-browser__webview"
          src={browserUrl}
          sandbox="allow-same-origin allow-scripts allow-forms"
          title="内嵌浏览器（成果自测）"
        />
      ) : (
        <div className="wb-artifacts__empty">
          <div className="wb-empty__icon">🧭</div>
          <div className="wb-empty__title">内嵌浏览器</div>
          <div className="wb-empty__desc">输入本地路径或 URL，演示网页类成果</div>
        </div>
      )}
    </div>
  );
}
