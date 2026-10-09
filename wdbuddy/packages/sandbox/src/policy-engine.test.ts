import { describe, expect, it } from 'vitest';
import { evaluate, policyFor, DEFAULT_POLICY_OPTIONS } from './policy-engine.js';
import type { PermissionContext } from '@wdbuddy/shared-types';

const ROOTS = ['D:\\work'];

function ctx(tool: PermissionContext['tool'], args: Record<string, unknown>, level: 'default' | 'full' = 'default'): PermissionContext {
  return { tool, args, permissionLevel: level, workspaceRoots: ROOTS };
}

describe('evaluate — 只读工具', () => {
  it('fs.read 默认权限直接 allow', () => {
    expect(evaluate(ctx('fs.read', { path: 'D:\\work\\a.txt' })).decision).toBe('allow');
  });

  it('fs.list 默认权限直接 allow', () => {
    expect(evaluate(ctx('fs.list', { path: 'D:\\work' })).decision).toBe('allow');
  });

  it('search.web 默认权限直接 allow', () => {
    expect(evaluate(ctx('search.web', { query: '环保节日' })).decision).toBe('allow');
  });
});

describe('evaluate — shell.exec', () => {
  it('普通命令默认权限 require-confirm', () => {
    expect(evaluate(ctx('shell.exec', { command: 'node --version' })).decision).toBe('require-confirm');
  });

  it('危险命令任何权限级别都 deny', () => {
    expect(evaluate(ctx('shell.exec', { command: 'rm -rf /' })).decision).toBe('deny');
    expect(evaluate(ctx('shell.exec', { command: 'rm -rf /' }, 'full')).decision).toBe('deny');
  });

  it('fork 炸弹 deny', () => {
    expect(evaluate(ctx('shell.exec', { command: ':(){ :|:& };:' })).decision).toBe('deny');
  });

  it('格式化磁盘 deny', () => {
    expect(evaluate(ctx('shell.exec', { command: 'format C:' })).decision).toBe('deny');
  });

  it('缺少 command 参数 deny', () => {
    expect(evaluate(ctx('shell.exec', {})).decision).toBe('deny');
  });

  it('完全访问权限下普通命令 allow', () => {
    expect(evaluate(ctx('shell.exec', { command: 'node --version' }, 'full')).decision).toBe('allow');
  });

  it('confirmBeforeShell=false 时默认权限也 allow', () => {
    const r = evaluate(ctx('shell.exec', { command: 'node --version' }), { ...DEFAULT_POLICY_OPTIONS, confirmBeforeShell: false });
    expect(r.decision).toBe('allow');
  });
});

describe('evaluate — fs.write', () => {
  it('工作空间内写入默认权限 allow', () => {
    expect(evaluate(ctx('fs.write', { path: 'D:\\work\\out.html' })).decision).toBe('allow');
  });

  it('工作空间外写入默认权限 require-confirm', () => {
    expect(evaluate(ctx('fs.write', { path: 'E:\\outside\\x.txt' })).decision).toBe('require-confirm');
  });

  it('工作空间外写入但关闭确认时 allow', () => {
    const r = evaluate(
      ctx('fs.write', { path: 'E:\\outside\\x.txt' }),
      { ...DEFAULT_POLICY_OPTIONS, confirmBeforeWriteOutsideWorkspace: false }
    );
    expect(r.decision).toBe('allow');
  });

  it('完全访问下工作空间外写入 allow', () => {
    expect(evaluate(ctx('fs.write', { path: 'E:\\outside\\x.txt' }, 'full')).decision).toBe('allow');
  });
});

describe('evaluate — fs.delete', () => {
  it('删除文件默认权限 require-confirm（即使在工作空间内）', () => {
    expect(evaluate(ctx('fs.delete', { path: 'D:\\work\\tmp.txt' })).decision).toBe('require-confirm');
  });

  it('完全访问下删除 allow', () => {
    expect(evaluate(ctx('fs.delete', { path: 'D:\\work\\tmp.txt' }, 'full')).decision).toBe('allow');
  });
});

describe('evaluate — browser.open', () => {
  it('https URL 默认权限 allow', () => {
    expect(evaluate(ctx('browser.open', { url: 'https://example.com' })).decision).toBe('allow');
  });

  it('file:// 协议 deny（防本地文件读取）', () => {
    expect(evaluate(ctx('browser.open', { url: 'file:///C:/Windows/system32/config' })).decision).toBe('deny');
  });
});

describe('evaluate — 边界', () => {
  it('参数为空 deny', () => {
    expect(evaluate(ctx('fs.read', undefined as unknown as Record<string, unknown>)).decision).toBe('deny');
  });

  it('office.generate 生成类 allow', () => {
    expect(evaluate(ctx('office.generate', { kind: 'pptx', out: 'D:\\work\\a.pptx' })).decision).toBe('allow');
  });
});

describe('policyFor', () => {
  it('返回按任务权限级别的评估器', () => {
    const p = policyFor('default', ROOTS);
    expect(p.evaluate('fs.read', { path: 'D:\\work\\x' }).decision).toBe('allow');
    expect(p.evaluate('shell.exec', { command: 'ls' }).decision).toBe('require-confirm');
  });
});
