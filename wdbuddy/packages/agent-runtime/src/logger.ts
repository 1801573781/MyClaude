/**
 * 结构化日志（可观测性 NFR-08）
 * - pino 结构化，taskId/stepId 全链路贯穿 runtime → gateway
 * - 日志脱敏：API Key 等字段自动打码（隐私 NFR-06）
 * - 遥测默认关闭
 */
import pino from 'pino';

const REDACT_KEYS = ['apiKey', 'api_key', 'authorization', 'password', 'token', 'secret'];

export interface Logger {
  debug(msg: string, extra?: Record<string, unknown>): void;
  info(msg: string, extra?: Record<string, unknown>): void;
  warn(msg: string, extra?: Record<string, unknown>): void;
  error(msg: string, extra?: Record<string, unknown>): void;
  child(bindings: Record<string, unknown>): Logger;
}

export function createLogger(bindings: Record<string, unknown> = {}): Logger {
  const p = pino({
    level: process.env['WB_LOG_LEVEL'] ?? 'info',
    redact: { paths: REDACT_KEYS.map((k) => `*.${k}`), censor: '[REDACTED]' },
    base: { svc: 'agent-runtime', ...bindings },
  });
  return {
    debug: (msg, extra) => p.debug(extra ?? {}, msg),
    info: (msg, extra) => p.info(extra ?? {}, msg),
    warn: (msg, extra) => p.warn(extra ?? {}, msg),
    error: (msg, extra) => p.error(extra ?? {}, msg),
    child: (b) => createLoggerChild(p, b),
  };
}

function createLoggerChild(parent: pino.Logger, bindings: Record<string, unknown>): Logger {
  const c = parent.child(bindings);
  return {
    debug: (msg, extra) => c.debug(extra ?? {}, msg),
    info: (msg, extra) => c.info(extra ?? {}, msg),
    warn: (msg, extra) => c.warn(extra ?? {}, msg),
    error: (msg, extra) => c.error(extra ?? {}, msg),
    child: (b) => createLoggerChild(c, b),
  };
}
