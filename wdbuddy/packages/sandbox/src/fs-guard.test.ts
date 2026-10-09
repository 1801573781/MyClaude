import { describe, expect, it, beforeEach } from 'vitest';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { FsGuard, checkPath, normalizePath, isInsideRoots, isSensitiveDir } from './fs-guard.js';

let tmpRoot: string;

beforeEach(() => {
  tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-fsguard-'));
});

describe('normalizePath', () => {
  it('统一斜杠分隔符并消解 ..', () => {
    expect(normalizePath('a/b/../c').replace(/\\/g, '/')).toBe('a/c');
  });

  it('空字符串原样返回', () => {
    expect(normalizePath('')).toBe('');
  });
});

describe('isSensitiveDir', () => {
  it('Windows 系统目录被拉黑（大小写不敏感）', () => {
    expect(isSensitiveDir('C:\\Windows')).toBe(true);
    expect(isSensitiveDir('c:\\windows\\system32')).toBe(true);
    expect(isSensitiveDir('C:\\Program Files\\App')).toBe(true);
  });

  it('Unix 系统目录被拉黑', () => {
    expect(isSensitiveDir('/etc/passwd')).toBe(true);
    expect(isSensitiveDir('/usr/bin/node')).toBe(true);
  });

  it('普通目录不拉黑', () => {
    expect(isSensitiveDir('D:\\work\\project')).toBe(false);
    expect(isSensitiveDir('/home/user/work')).toBe(false);
  });
});

describe('isInsideRoots', () => {
  it('授权根内路径放行', () => {
    expect(isInsideRoots(path.join(tmpRoot, 'sub', 'file.txt'), [tmpRoot])).toBe(true);
    expect(isInsideRoots(tmpRoot, [tmpRoot])).toBe(true);
  });

  it('路径穿越（..）逃逸被识别', () => {
    const escape = path.resolve(tmpRoot, '..', 'outside.txt');
    expect(isInsideRoots(escape, [tmpRoot])).toBe(false);
  });

  it('前缀相似但非子目录不放行（如 /work vs /workspace）', () => {
    const sibling = tmpRoot + '-sibling';
    expect(isInsideRoots(sibling, [tmpRoot])).toBe(false);
  });
});

describe('checkPath', () => {
  const opts = { workspaceRoots: [tmpRoot], permissionLevel: 'default' as const };

  it('工作空间内读取放行', () => {
    const v = checkPath(path.join(tmpRoot, 'a.txt'), opts, 'read');
    expect(v.allowed).toBe(true);
  });

  it('工作空间外读取被拒绝（outside-workspace）', () => {
    const v = checkPath(path.join(os.tmpdir(), 'wb-outside', 'a.txt'), opts, 'read');
    expect(v.allowed).toBe(false);
    if (!v.allowed) expect(v.reason).toBe('outside-workspace');
  });

  it('敏感目录直接拒绝', () => {
    const v = checkPath('C:\\Windows\\system32\\config', opts, 'read');
    expect(v.allowed).toBe(false);
    if (!v.allowed) expect(v.reason).toBe('sensitive-dir');
  });

  it('符号链接逃逸被拦截（symlink-escape）', () => {
    // 在工作空间内创建指向外部的符号链接
    const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-outside-'));
    const linkPath = path.join(tmpRoot, 'evil-link');
    fs.symlinkSync(outsideDir, linkPath, 'dir');
    const target = path.join(linkPath, 'steal.txt');
    const v = checkPath(target, opts, 'write');
    expect(v.allowed).toBe(false);
    if (!v.allowed) expect(v.reason).toBe('symlink-escape');
    fs.rmSync(outsideDir, { recursive: true, force: true });
  });

  it('完全访问权限下工作空间外读取放行', () => {
    const v = checkPath(path.join(os.tmpdir(), 'wb-anywhere.txt'), { ...opts, permissionLevel: 'full' }, 'read');
    expect(v.allowed).toBe(true);
  });

  it('完全访问下符号链接落入敏感目录仍拒绝', () => {
    const v = checkPath('C:\\Windows\\evil\\x.txt', { workspaceRoots: [], permissionLevel: 'full' }, 'write');
    expect(v.allowed).toBe(false);
  });
});

describe('FsGuard 类', () => {
  it('工作空间内可读写', () => {
    const guard = new FsGuard({ workspaceRoots: [tmpRoot], permissionLevel: 'default' });
    const p = path.join(tmpRoot, 'hello.txt');
    guard.writeFile(p, 'hi');
    expect(guard.readFile(p)).toBe('hi');
  });

  it('工作空间外写入抛出 FsGuardError', () => {
    const guard = new FsGuard({ workspaceRoots: [tmpRoot], permissionLevel: 'default' });
    expect(() => guard.writeFile(path.join(os.tmpdir(), 'wb-out', 'x.txt'), 'x')).toThrow(/outside-workspace/);
  });

  it('withPermissionLevel 提升后可写工作空间外', () => {
    const guard = new FsGuard({ workspaceRoots: [tmpRoot], permissionLevel: 'default' });
    const full = guard.withPermissionLevel('full');
    const p = path.join(tmpRoot, '..', 'wb-full-write.txt');
    full.writeFile(p, 'ok');
    expect(fs.existsSync(p)).toBe(true);
    fs.rmSync(p, { force: true });
  });

  it('listDir 列出目录内容', () => {
    const guard = new FsGuard({ workspaceRoots: [tmpRoot], permissionLevel: 'default' });
    guard.writeFile(path.join(tmpRoot, 'f1.txt'), '1');
    guard.writeFile(path.join(tmpRoot, 'f2.txt'), '2');
    const items = guard.listDir(tmpRoot);
    expect(items).toContain('f1.txt');
    expect(items).toContain('f2.txt');
  });
});
