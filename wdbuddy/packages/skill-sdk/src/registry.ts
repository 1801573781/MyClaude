/**
 * 技能注册表：内置与市场技能统一加载（§10.6）
 * - 分类 / 搜索（FR-17）
 * - Composer 以 / 触发或自然语言匹配
 */
import type { Skill } from '@wdbuddy/shared-types';
import { BUILTIN_SKILLS, toSkill, validateManifest, type SkillManifest } from './manifest.js';

export class SkillRegistry {
  private readonly skills = new Map<string, SkillManifest>();

  constructor(builtins: readonly SkillManifest[] = BUILTIN_SKILLS) {
    for (const m of builtins) this.skills.set(m.id, m);
  }

  register(manifest: unknown): { ok: boolean; errors?: string[] } {
    const v = validateManifest(manifest);
    if (!v.ok) return { ok: false, errors: v.errors };
    this.skills.set(v.manifest.id, v.manifest);
    return { ok: true };
  }

  get(id: string): SkillManifest | undefined {
    return this.skills.get(id);
  }

  list(): Skill[] {
    return [...this.skills.values()].map(toSkill);
  }

  /** 分类列表 */
  categories(): string[] {
    return [...new Set([...this.skills.values()].map((s) => s.category))];
  }

  /** 搜索：名称/描述/触发词模糊匹配 */
  search(query: string): Skill[] {
    const q = query.trim().toLowerCase();
    if (!q) return this.list();
    return [...this.skills.values()]
      .filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.description.toLowerCase().includes(q) ||
          s.triggers.some((t) => t.toLowerCase().includes(q))
      )
      .map(toSkill);
  }

  /** Composer 斜杠命令匹配：/周报 → weekly-report */
  matchSlashCommand(input: string): SkillManifest | null {
    const m = input.match(/^\/([^\s]+)/);
    if (!m) return null;
    const cmd = m[1]!.toLowerCase();
    for (const s of this.skills.values()) {
      if (s.id === cmd) return s;
      if (s.triggers.some((t) => t.toLowerCase() === cmd)) return s;
    }
    return null;
  }

  /** 自然语言触发匹配（命中返回技能，多命中取触发词最长者） */
  matchNaturalLanguage(text: string): SkillManifest | null {
    let best: SkillManifest | null = null;
    let bestLen = 0;
    for (const s of this.skills.values()) {
      for (const t of s.triggers) {
        if (text.includes(t) && t.length > bestLen) {
          best = s;
          bestLen = t.length;
        }
      }
    }
    return best;
  }
}
