/**
 * skill-store — 自定义技能持久化（FR-17）
 * 磁盘形态：userData/skills/<id>/manifest.json + entry.md
 * 提供列表/创建/删除；manifest 复用 skill-sdk 的 SkillManifest 结构。
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import { app } from 'electron';
import type { Skill } from '@wdbuddy/shared-types';

export interface CustomSkillInput {
  name: string;
  description: string;
  icon: string;
  category: string;
  /** 指令模板：{} 为用户待补充占位 */
  instruction: string;
  /** 声明式权限（UI 勾选或默认推断） */
  permissions: string[];
}

interface SkillManifestFile {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  permissions: string[];
  triggers: string[];
  entry: string;
  source: 'custom';
  version: string;
  /** 指令模板（entry.md 的镜像，便于直接读取） */
  instruction: string;
}

export class SkillStore {
  private readonly root: string;

  constructor(userDataPath?: string) {
    this.root = path.join(userDataPath ?? app.getPath('userData'), 'skills');
    fs.mkdirSync(this.root, { recursive: true });
  }

  /** 列出自定义技能 */
  list(): Array<Skill & { instruction: string }> {
    const out: Array<Skill & { instruction: string }> = [];
    for (const dir of fs.readdirSync(this.root, { withFileTypes: true })) {
      if (!dir.isDirectory()) continue;
      const mf = path.join(this.root, dir.name, 'manifest.json');
      if (!fs.existsSync(mf)) continue;
      try {
        const m = JSON.parse(fs.readFileSync(mf, 'utf8')) as SkillManifestFile;
        out.push({
          id: m.id,
          name: m.name,
          description: m.description,
          icon: m.icon,
          category: m.category,
          permissions: m.permissions as Skill['permissions'],
          source: 'custom',
          instruction: m.instruction,
        });
      } catch {
        /* 跳过损坏的 manifest */
      }
    }
    return out;
  }

  /** 创建（或覆盖同名 id）自定义技能 */
  create(input: CustomSkillInput): { ok: boolean; id?: string; error?: string } {
    const nameOk = input.name.trim().length >= 1 && input.name.trim().length <= 30;
    const instrOk = input.instruction.trim().length >= 5;
    if (!nameOk) return { ok: false, error: '技能名称需 1~30 个字符' };
    if (!instrOk) return { ok: false, error: '指令模板至少 5 个字符（建议用 {} 标记待补充内容）' };
    // id：名称转拼音不可行，用 时间戳+随机 后缀保唯一；显示用 name
    const base = input.name.trim().toLowerCase().replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-').replace(/^-+|-+$/g, '') || 'skill';
    const id = `custom-${base}-${Date.now().toString(36).slice(-4)}`;
    const dir = path.join(this.root, id);
    fs.mkdirSync(dir, { recursive: true });
    const manifest: SkillManifestFile = {
      id,
      name: input.name.trim(),
      description: input.description.trim() || '自定义技能',
      icon: input.icon || '🛠',
      category: input.category || '自定义',
      permissions: input.permissions.length > 0 ? input.permissions : ['fs.read', 'fs.write'],
      triggers: [input.name.trim()],
      entry: './entry.md',
      source: 'custom',
      version: '1.0.0',
      instruction: input.instruction.trim(),
    };
    fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest, null, 2), 'utf8');
    fs.writeFileSync(
      path.join(dir, 'entry.md'),
      `# ${manifest.name}\n\n${manifest.instruction}\n`,
      'utf8'
    );
    return { ok: true, id };
  }

  /** 删除自定义技能 */
  remove(id: string): void {
    // 安全校验：只允许删本 root 下的目录（防路径注入）
    const target = path.resolve(this.root, id);
    if (!target.startsWith(path.resolve(this.root) + path.sep)) return;
    fs.rmSync(target, { recursive: true, force: true });
  }
}
