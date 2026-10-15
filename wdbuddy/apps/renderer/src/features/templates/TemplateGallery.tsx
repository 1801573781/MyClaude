/**
 * TemplateGallery — 模版库面板（v2）
 * 修复三个交互问题：
 * 1. 主题点击无反应 → 点击卡片预览区弹出全屏放大预览（iframe 内 ←→/滚轮翻页）
 * 2. 预览黑框 → 预览改经 wb-preview:// 自定义协议加载：此前用 iframe srcDoc，
 *    srcdoc 文档会继承页面 CSP（script-src 'self'），deck 内联翻页脚本被阻止
 *    → 所有 .slide 保持 display:none → 只剩 #111 黑底；协议响应是独立文档，
 *    不受父页面 CSP 约束，脚本正常执行
 * 3. 对话框尺寸固定 → 居中浮层 + 最大化/还原 + 右下角拖拽调大小 + 标题栏拖动
 */
import React, { useEffect, useRef, useState } from 'react';
import type { TemplatesPreviewResult } from '@wdbuddy/shared-types';
import { requireApi } from '../../shared/ipc';
import { useAppStore } from '../../shared/store';
import './styles.css';

/** 预览协议 URL：主进程 wb-preview://templates/{key} 返回缓存的 deck HTML */
function previewUrl(key: string): string {
  return `wb-preview://templates/${encodeURIComponent(key)}`;
}

interface DlgRect {
  w: number;
  h: number;
  x: number;
  y: number;
}

export function TemplateGallery(): React.ReactElement {
  const [data, setData] = useState<TemplatesPreviewResult | null>(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'themes' | 'layouts' | 'clones'>('themes');
  const [toast, setToast] = useState('');

  // ─────────── 对话框几何：拖动 / 调大小 / 最大化 ───────────
  const [maximized, setMaximized] = useState(false);
  const [dlg, setDlg] = useState<DlgRect>(() => {
    // 默认近全屏（用户反馈：小窗+放大镜交互多余）；仍可拖动/缩放/还原
    const w = Math.min(1440, Math.max(980, window.innerWidth - 120));
    const h = Math.min(920, Math.max(560, window.innerHeight - 90));
    return {
      w,
      h,
      x: Math.round((window.innerWidth - w) / 2),
      y: Math.round((window.innerHeight - h) / 2),
    };
  });
  const dragRef = useRef<{ mode: 'move' | 'resize'; sx: number; sy: number; orig: DlgRect } | null>(null);

  // ─────────── 主题/克隆模板放大预览（null = 未打开） ───────────
  const [zoomId, setZoomId] = useState<string | null>(null);
  /** zoomId 以 clone: 前缀标记克隆模板（预览 key 与主题不同源） */
  const zoomClone =
    zoomId?.startsWith('clone:') && data
      ? data.cloneTemplates?.find((c) => c.id === zoomId.slice(6))
      : undefined;

  // ─────────── 克隆模板：左列表选中项 ───────────
  const [selClone, setSelClone] = useState<string>('');
  const selCloneInfo = data?.cloneTemplates?.find((c) => c.id === (selClone || data?.cloneTemplates?.[0]?.id));

  useEffect(() => {
    void requireApi()
      .templatesPreview()
      .then((r) => setData(r))
      .catch((e: unknown) => setError(String(e)));
  }, []);

  /** “用此主题/模板”：把主题名或模板 id 注入输入框草稿（用户补充任务后发送） */
  const useTheme = (name: string): void => {
    useAppStore.getState().setComposerDraft(`请用「${name}」主题帮我生成PPT：`);
    setToast(`已把「${name}」填入输入框，补充内容后发送即可`);
    setTimeout(() => setToast(''), 2500);
  };
  /** “用此模板”（克隆模式）：关闭模版库 + 填引导式草稿（纯用户视角文案，
   *  不暴露内部实现术语；模板 id 由 Agent 从模板名自行解析） */
  const useCloneTemplate = (_id: string, name: string): void => {
    useAppStore.getState().setOpenPanel(null);
    useAppStore.getState().setComposerDraft(
      `请用「${name}」这个模板帮我制作一套PPT，风格保持模板原样。我的内容要点：`
    );
  };

  /** 标题栏拖动 / 右下角拖拽调大小（参照 WorkbenchLayout 右栏拖宽实现） */
  const startDrag = (mode: 'move' | 'resize') => (e: React.MouseEvent): void => {
    if (maximized) return;
    e.preventDefault();
    dragRef.current = { mode, sx: e.clientX, sy: e.clientY, orig: { ...dlg } };
    const onMove = (ev: MouseEvent): void => {
      const d = dragRef.current;
      if (!d) return;
      if (d.mode === 'move') {
        const x = Math.min(window.innerWidth - 140, Math.max(-d.orig.w + 180, d.orig.x + ev.clientX - d.sx));
        const y = Math.min(window.innerHeight - 60, Math.max(0, d.orig.y + ev.clientY - d.sy));
        setDlg({ ...d.orig, x, y });
      } else {
        const w = Math.min(window.innerWidth - d.orig.x - 12, Math.max(760, d.orig.w + ev.clientX - d.sx));
        const h = Math.min(window.innerHeight - d.orig.y - 12, Math.max(460, d.orig.h + ev.clientY - d.sy));
        setDlg({ ...d.orig, w, h });
      }
    };
    const onUp = (): void => {
      dragRef.current = null;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  /** ESC：先关放大预览，再关模版库 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return;
      if (zoomId !== null) {
        setZoomId(null);
      } else {
        useAppStore.getState().setOpenPanel(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [zoomId]);

  const zoomTheme = zoomId !== null && data ? data.themes.find((t) => t.id === zoomId) : undefined;

  return (
    <>
      <div
        className="wb-gallery-mask"
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) useAppStore.getState().setOpenPanel(null);
        }}
      >
        <div
          className={`wb-gallery${maximized ? ' wb-gallery--max' : ''}`}
          style={maximized ? undefined : { left: dlg.x, top: dlg.y, width: dlg.w, height: dlg.h }}
          role="dialog"
          aria-label="模版库"
        >
          <header className="wb-gallery__header" onMouseDown={startDrag('move')}>
            <h2>🎨 模版库</h2>
            <div className="wb-gallery__tabs" role="tablist" onMouseDown={(e) => e.stopPropagation()}>
              <button
                className={`wb-btn ${tab === 'themes' ? 'wb-btn--primary' : 'wb-btn--ghost'}`}
                onClick={() => setTab('themes')}
                role="tab"
                aria-selected={tab === 'themes'}
              >
                10 套主题
              </button>
              <button
                className={`wb-btn ${tab === 'clones' ? 'wb-btn--primary' : 'wb-btn--ghost'}`}
                onClick={() => setTab('clones')}
                role="tab"
                aria-selected={tab === 'clones'}
              >
                克隆模板{data?.cloneTemplates?.length ? ` ${data.cloneTemplates.length}` : ''}
              </button>
              <button
                className={`wb-btn ${tab === 'layouts' ? 'wb-btn--primary' : 'wb-btn--ghost'}`}
                onClick={() => setTab('layouts')}
                role="tab"
                aria-selected={tab === 'layouts'}
              >
                14 种版式
              </button>
            </div>
            <div className="wb-gallery__spacer" />
            {toast && <span className="wb-gallery__toast">{toast}</span>}
            <button
              className="wb-btn wb-btn--ghost"
              onMouseDown={(e) => e.stopPropagation()}
              onClick={() => setMaximized((m) => !m)}
              title={maximized ? '还原窗口大小' : '最大化窗口'}
            >
              {maximized ? '⤡ 还原' : '⤢ 最大化'}
            </button>
            <button
              className="wb-btn wb-btn--ghost"
              onMouseDown={(e) => e.stopPropagation()}
              onClick={() => useAppStore.getState().setOpenPanel(null)}
            >
              ✕ 关闭
            </button>
          </header>

          {error && <div className="wb-gallery__error">⚠ {error}</div>}
          {!data && !error && <div className="wb-gallery__loading">加载模版预览中…</div>}

          {data && tab === 'themes' && (
            <div className="wb-gallery__grid">
              {data.themes.map((t) => (
                <section key={t.id} className="wb-gallery__card">
                  <header>
                    <h3>
                      {t.name} <code>{t.id}</code>
                    </h3>
                    <p className="wb-gallery__vibe">{t.vibe}</p>
                  </header>
                  <div
                    className="wb-gallery__preview"
                    onClick={() => setZoomId(t.id)}
                    title="点击放大查看完整预览"
                  >
                    <iframe
                      title={`${t.name} 预览`}
                      src={previewUrl(t.id)}
                      sandbox="allow-scripts"
                      loading="lazy"
                    />
                    <span className="wb-gallery__zoom-hint">🔍 点击放大</span>
                  </div>
                  <footer>
                    <span className="wb-gallery__colors" aria-label="主题色板">
                      {t.colors.map((c, i) => (
                        <i key={i} style={{ background: c }} title={c} />
                      ))}
                    </span>
                    <button className="wb-btn wb-btn--small wb-btn--primary" onClick={() => useTheme(t.name)}>
                      用此主题
                    </button>
                  </footer>
                </section>
              ))}
            </div>
          )}

          {data && tab === 'clones' && (
            <div className="wb-clone">
              <aside className="wb-clone__list">
                {(data.cloneTemplates ?? []).map((c, i) => (
                  <button
                    key={c.id}
                    className={`wb-clone__item${selClone === c.id ? ' wb-clone__item--on' : ''}`}
                    onClick={() => setSelClone(c.id)}
                  >
                    <strong>{c.name}</strong>
                    <span>
                      {c.pages} 页 · {c.tags.join(' / ')}
                    </span>
                  </button>
                ))}
                {(data.cloneTemplates ?? []).length === 0 && (
                  <p className="wb-gallery__hint">
                    暂无内置克隆模板。可将第三方 .pptx 放入 agent-runtime/templates/ 并在 clone-registry.ts 登记。
                  </p>
                )}
              </aside>
              <div className="wb-clone__main">
                {selCloneInfo && (
                  <>
                    <div className="wb-clone__bar">
                      <div>
                        <strong>{selCloneInfo.name}</strong>
                        <span className="wb-gallery__vibe">　{selCloneInfo.vibe}</span>
                      </div>
                      <span className="wb-clone__meta">{selCloneInfo.pages} 页</span>
                      <div className="wb-gallery__spacer" />
                      <button
                        className="wb-btn wb-btn--primary"
                        onClick={() => useCloneTemplate(selCloneInfo.id, selCloneInfo.name)}
                      >
                        用此模板生成 PPT
                      </button>
                    </div>
                    <iframe
                      title={`${selCloneInfo.name} 真彩预览`}
                      src={previewUrl(`clone-${selCloneInfo.id}`)}
                      sandbox="allow-scripts"
                      className="wb-clone__frame"
                    />
                  </>
                )}
              </div>
            </div>
          )}

          {data && tab === 'layouts' && (
            <div className="wb-gallery__layouts">
              <p className="wb-gallery__hint">
                生成 PPT 时系统按每页内容自动分配版式（封面/目录/要点/时间线/对比/图表…），
                也可在指令中指定，如“第 3 页用时间线版式”。点击幻灯片后可用 ← → 键或滚轮翻页。
              </p>
              <iframe
                title="14 种版式演示"
                src={previewUrl('layouts')}
                sandbox="allow-scripts"
                className="wb-gallery__layouts-frame"
              />
            </div>
          )}

          {!maximized && (
            <div className="wb-gallery__resizer" onMouseDown={startDrag('resize')} title="拖拽调整窗口大小" />
          )}
        </div>
      </div>

      {/* ─────────── 主题放大预览（全屏层） ─────────── */}
      {zoomTheme && (
        <div
          className="wb-gallery-zoom"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setZoomId(null);
          }}
        >
          <div className="wb-gallery-zoom__bar">
            <span className="wb-gallery-zoom__title">
              🎨 {zoomTheme.name} · {zoomTheme.vibe}
            </span>
            <span className="wb-gallery-zoom__hint">点击幻灯片后可用 ← → 键 / 滚轮翻页 · ESC 退出</span>
            <div className="wb-gallery__spacer" />
            <button className="wb-btn wb-btn--small wb-btn--primary" onClick={() => useTheme(zoomTheme.name)}>
              用此主题
            </button>
            <button className="wb-btn wb-btn--ghost" onClick={() => setZoomId(null)}>
              ✕
            </button>
          </div>
          <div className="wb-gallery-zoom__body">
            <iframe title={`${zoomTheme.name} 放大预览`} src={previewUrl(zoomTheme.id)} sandbox="allow-scripts" />
          </div>
        </div>
      )}

      {zoomClone && (
        <div
          className="wb-gallery-zoom"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setZoomId(null);
          }}
        >
          <div className="wb-gallery-zoom__bar">
            <span className="wb-gallery-zoom__title">
              🧬 {zoomClone.name} · {zoomClone.pages} 页 · {Object.entries(zoomClone.kinds)
                .map(([k, n]) => `${k}×${n}`)
                .join(' ')}
            </span>
            <span className="wb-gallery-zoom__hint">滚动查看全部页面线框 · ESC 退出</span>
            <div className="wb-gallery__spacer" />
            <button
              className="wb-btn wb-btn--small wb-btn--primary"
              onClick={() => useCloneTemplate(zoomClone.id, zoomClone.name)}
            >
              用此模板
            </button>
            <button className="wb-btn wb-btn--ghost" onClick={() => setZoomId(null)}>
              ✕
            </button>
          </div>
          <div className="wb-gallery-zoom__body">
            <iframe
              title={`${zoomClone.name} 放大预览`}
              src={previewUrl(`clone-${zoomClone.id}`)}
              sandbox="allow-scripts"
            />
          </div>
        </div>
      )}
    </>
  );
}
