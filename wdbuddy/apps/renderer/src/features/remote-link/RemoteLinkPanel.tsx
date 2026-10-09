/**
 * RemoteLinkPanel — 手机联动（FR-20）
 * 手机扫码绑定，远程下发指令，桌面执行，手机收通知；桌面离线时明确提示
 */
import React, { useMemo, useState } from 'react';
import { encodeText } from '../../shared/qrcode';

/** 本机稳定设备标识（localStorage 持久化，重装/清缓存后才会变化） */
function getDeviceId(): string {
  let id = localStorage.getItem('wb-device-id');
  if (!id) {
    id = `desktop-${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem('wb-device-id', id);
  }
  return id;
}

/** 二维码 SVG（含 4 模块静区，rect 逐模块绘制） */
function QrSvg({ text }: { text: string }): React.ReactElement {
  const { size, rects } = useMemo(() => {
    try {
      const qr = encodeText(text);
      const quiet = 4; // 静区
      const total = qr.size + quiet * 2;
      const rs: Array<{ x: number; y: number }> = [];
      for (let y = 0; y < qr.size; y++) {
        for (let x = 0; x < qr.size; x++) {
          if (qr.modules[y]![x]!) rs.push({ x: x + quiet, y: y + quiet });
        }
      }
      return { size: total, rects: rs };
    } catch {
      return { size: 0, rects: [] };
    }
  }, [text]);

  if (size === 0) {
    return (
      <div className="wb-remote__qr-box" title="二维码生成失败">
        <span style={{ fontSize: 14 }}>二维码内容过长</span>
      </div>
    );
  }
  return (
    <svg
      className="wb-remote__qr-svg"
      viewBox={`0 0 ${size} ${size}`}
      shapeRendering="crispEdges"
      role="img"
      aria-label="扫码绑定二维码"
    >
      <rect width={size} height={size} fill="#ffffff" />
      {rects.map((r, i) => (
        <rect key={i} x={r.x} y={r.y} width={1} height={1} fill="#000000" />
      ))}
    </svg>
  );
}

export function RemoteLinkPanel({ onClose }: { onClose: () => void }): React.ReactElement {
  const [devices, setDevices] = useState<Array<{ id: string; name: string; at: number }>>([]);
  const [desktopOnline, setDesktopOnline] = useState(true);
  // 绑定载荷：设备标识（手机端扫码后解析，向云端登记并建立绑定关系）
  const bindPayload = `wdbuddy://bind?deviceId=${getDeviceId()}`;

  return (
    <div className="wb-modal-mask" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wb-modal">
        <div className="wb-modal__header">📱 手机联动</div>
        <div className="wb-modal__body">
          {/* 服务状态诚实标注：云端服务与手机端 APP 均未上线，避免用户空扫码 */}
          <div className="wb-badge wb-badge--warning" style={{ marginBottom: 12 }}>
            ⚠ 服务暂未开放：手机端 APP 与云端服务尚未上线，扫码暂无法完成绑定
          </div>

          {/* 桌面端在线状态（FR-20：离线时明确提示；服务上线后生效） */}
          <div className={`wb-badge ${desktopOnline ? 'wb-badge--success' : 'wb-badge--danger'}`}>
            {desktopOnline ? '● 桌面端在线，可接收指令' : '● 桌面端离线，手机指令将无法送达'}
          </div>

          {/* 绑定二维码（真实渲染，修复"二维码没显示出来"） */}
          <div className="wb-remote__qr">
            <div className="wb-remote__qr-box" title="扫码绑定">
              <QrSvg text={bindPayload} />
            </div>
            <div style={{ fontSize: 12, color: 'var(--wb-text-secondary)' }}>
              使用手机 APP / 微信小程序扫码绑定本桌面端
            </div>
            <div style={{ fontSize: 11, color: 'var(--wb-text-disabled)' }}>设备标识：{getDeviceId()}</div>
          </div>

          {/* 已绑定设备 */}
          <div style={{ marginTop: 16 }}>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>已绑定设备</div>
            {devices.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--wb-text-secondary)' }}>（暂无绑定设备）</div>
            ) : (
              devices.map((d) => (
                <div key={d.id} className="wb-memory__item">
                  <span>📱 {d.name}</span>
                  <span style={{ flex: 1 }} />
                  <button className="wb-btn wb-btn--ghost wb-btn--small" onClick={() => setDevices((p) => p.filter((x) => x.id !== d.id))}>
                    解绑
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
        <div className="wb-modal__footer">
          <button className="wb-btn wb-btn--ghost" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}
