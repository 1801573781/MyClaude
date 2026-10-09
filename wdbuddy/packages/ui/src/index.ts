/**
 * @wdbuddy/ui — 跨端共享基础组件（轻量，无第三方 UI 依赖）
 */
import React from 'react';

// ─────────── Button ───────────

export type ButtonVariant = 'primary' | 'default' | 'danger' | 'ghost';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: 'small' | 'medium' | 'large';
}

export function Button({ variant = 'default', size = 'medium', className = '', ...rest }: ButtonProps): React.ReactElement {
  const cls = ['wb-btn', `wb-btn--${variant}`, `wb-btn--${size}`, className].filter(Boolean).join(' ');
  return React.createElement('button', { ...rest, className: cls });
}

// ─────────── Spinner ───────────

export interface SpinnerProps {
  size?: number;
  label?: string;
}

export function Spinner({ size = 16, label }: SpinnerProps): React.ReactElement {
  return React.createElement(
    'span',
    { className: 'wb-spinner-wrap', role: 'status' },
    React.createElement('span', {
      className: 'wb-spinner',
      style: { width: size, height: size },
      'aria-hidden': true,
    }),
    label ? React.createElement('span', { className: 'wb-spinner-label' }, label) : null
  );
}

// ─────────── StatusBadge ───────────

export type BadgeTone = 'neutral' | 'running' | 'success' | 'warning' | 'danger' | 'info';

export interface StatusBadgeProps {
  tone: BadgeTone;
  children: React.ReactNode;
}

export function StatusBadge({ tone, children }: StatusBadgeProps): React.ReactElement {
  return React.createElement(
    'span',
    { className: `wb-badge wb-badge--${tone}` },
    children
  );
}

// ─────────── EmptyState ───────────

export interface EmptyStateProps {
  icon?: string;
  title: string;
  description?: string;
}

export function EmptyState({ icon = '🗂', title, description }: EmptyStateProps): React.ReactElement {
  return React.createElement(
    'div',
    { className: 'wb-empty' },
    React.createElement('div', { className: 'wb-empty__icon' }, icon),
    React.createElement('div', { className: 'wb-empty__title' }, title),
    description ? React.createElement('div', { className: 'wb-empty__desc' }, description) : null
  );
}

// ─────────── 图标（内联 SVG，避免依赖） ───────────

export const Icons = {
  logo: '◆',
  settings: '⚙',
  skills: '🧩',
  memory: '🧠',
  mobile: '📱',
  folder: '📁',
  send: '➤',
  attach: '📎',
  bulb: '💡',
  plus: '＋',
} as const;
