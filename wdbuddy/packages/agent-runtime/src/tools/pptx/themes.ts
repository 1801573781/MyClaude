/**
 * PPT/HTML 幻灯片主题库 —— 设计系统（第一阶核心）
 * 每套主题含完整色彩体系（主色/辅色/强调/深浅背景/深浅文字/次要文字）
 * 与风格描述（供 LLM 按任务气质自动选型）。
 * 颜色一律不带 #（pptxgenjs 约定）；HTML 引擎使用时再加 #。
 */

export interface SlideTheme {
  id: string;
  /** 展示名（提示词中告知 LLM） */
  name: string;
  /** 风格描述（LLM 选择依据） */
  vibe: string;
  /** 主色：标题、重点、封面底色 */
  primary: string;
  /** 辅色：装饰线、次级图形 */
  secondary: string;
  /** 强调色：数字、高亮、进度条 */
  accent: string;
  /** 深色背景（封面/结尾/数据大屏） */
  bgDark: string;
  /** 浅色背景（内容页） */
  bgLight: string;
  /** 浅色背景上的正文色 */
  textDark: string;
  /** 深色背景上的正文色 */
  textLight: string;
  /** 次要文字（说明/页码） */
  textMuted: string;
  /** 卡片底色 */
  cardBg: string;
  /** 深色模式：内容页也用深色背景，全篇统一深色（如深空科技全深底） */
  dark?: boolean;
  /** 标题字体（HTML 引擎用；pptx 用系统字体兜底） */
  fontTitle: string;
  /** 正文字体 */
  fontBody: string;
}

export const THEMES: readonly SlideTheme[] = [
  {
    id: 'tech-dark',
    name: '深空科技',
    vibe: '科技酷炫：深蓝黑底+霓虹青紫，适合产品发布、技术分享、面向学生的酷炫培训',
    primary: '1E2A52', secondary: '3D4F8A', accent: '00E5FF',
    bgDark: '0A0E27', bgLight: 'F4F6FB', textDark: '1F2430', textLight: 'EAF0FF',
    textMuted: '8A93B5', cardBg: 'FFFFFF', dark: true,
    fontTitle: '"Segoe UI", "Microsoft YaHei", sans-serif',
    fontBody: '"Segoe UI", "Microsoft YaHei", sans-serif',
  },
  {
    id: 'business-blue',
    name: '商务蓝',
    vibe: '稳重专业：经典商务深蓝，适合工作汇报、项目总结、客户提案',
    primary: '1F4E79', secondary: '2E75B6', accent: 'F2A900',
    bgDark: '16324F', bgLight: 'F8FAFD', textDark: '24292F', textLight: 'F0F6FC',
    textMuted: '6B7A90', cardBg: 'FFFFFF',
    fontTitle: '"Segoe UI", "Microsoft YaHei", sans-serif',
    fontBody: '"Segoe UI", "Microsoft YaHei", sans-serif',
  },
  {
    id: 'minimal-light',
    name: '极简白',
    vibe: '干净克制：大量留白+细线条，适合设计类、品牌理念、高端展示',
    primary: '222222', secondary: '555555', accent: 'E63946',
    bgDark: '1A1A1A', bgLight: 'FFFFFF', textDark: '222222', textLight: 'FAFAFA',
    textMuted: '9CA3AF', cardBg: 'F9FAFB',
    fontTitle: '"Segoe UI", "Microsoft YaHei", sans-serif',
    fontBody: '"Segoe UI", "Microsoft YaHei", sans-serif',
  },
  {
    id: 'warm-energy',
    name: '活力暖橙',
    vibe: '热情积极：暖橙渐变，适合团队动员、营销活动、教育激励',
    primary: 'C2410C', secondary: 'EA580C', accent: 'F59E0B',
    bgDark: '7C2D12', bgLight: 'FFF9F2', textDark: '3B2110', textLight: 'FFF3E8',
    textMuted: 'A1826B', cardBg: 'FFFFFF',
    fontTitle: '"Segoe UI", "Microsoft YaHei", sans-serif',
    fontBody: '"Segoe UI", "Microsoft YaHei", sans-serif',
  },
  {
    id: 'academic',
    name: '学术庄重',
    vibe: '严谨沉稳：墨绿+米白，适合学术答辩、课题汇报、政策解读',
    primary: '14532D', secondary: '166534', accent: 'B45309',
    bgDark: '0F3D22', bgLight: 'F7F9F4', textDark: '1F2A22', textLight: 'EDF7EF',
    textMuted: '7A8A7D', cardBg: 'FFFFFF',
    fontTitle: '"Times New Roman", "SimSun", serif',
    fontBody: '"Segoe UI", "Microsoft YaHei", sans-serif',
  },
  {
    id: 'red-gold',
    name: '汇报红金',
    vibe: '隆重醒目：中国红+金色点缀，适合年终总结、政企汇报、节庆主题',
    primary: '9E1B1B', secondary: 'C0392B', accent: 'D4A017',
    bgDark: '701616', bgLight: 'FDF7F2', textDark: '33201A', textLight: 'FBEBE6',
    textMuted: 'A08878', cardBg: 'FFFFFF',
    fontTitle: '"Segoe UI", "Microsoft YaHei", sans-serif',
    fontBody: '"Segoe UI", "Microsoft YaHei", sans-serif',
  },
  {
    id: 'fresh-mint',
    name: '清新薄荷',
    vibe: '轻盈舒适：薄荷绿+淡青，适合健康环保、生活消费、轻松分享',
    primary: '0F766E', secondary: '14B8A6', accent: 'F97316',
    bgDark: '134E4A', bgLight: 'F2FBF9', textDark: '1A2E2B', textLight: 'E6FBF7',
    textMuted: '7FA39D', cardBg: 'FFFFFF',
    fontTitle: '"Segoe UI", "Microsoft YaHei", sans-serif',
    fontBody: '"Segoe UI", "Microsoft YaHei", sans-serif',
  },
  {
    id: 'purple-elegant',
    name: '优雅紫',
    vibe: '高级雅致：深紫+香槟金，适合时尚品牌、艺术人文、高端沙龙',
    primary: '581C87', secondary: '7E22CE', accent: 'D4AF37',
    bgDark: '3B0A5E', bgLight: 'FAF6FD', textDark: '2A1B33', textLight: 'F3E8FB',
    textMuted: '9A85A8', cardBg: 'FFFFFF',
    fontTitle: '"Segoe UI", "Microsoft YaHei", sans-serif',
    fontBody: '"Segoe UI", "Microsoft YaHei", sans-serif',
  },
  {
    id: 'sunset-gradient',
    name: '日落渐变',
    vibe: '浪漫温暖：橙粉渐变情绪，适合旅行分享、生活方式、情感向内容',
    primary: 'BE3455', secondary: 'E86A5E', accent: 'FFB35C',
    bgDark: '89304A', bgLight: 'FFF6F2', textDark: '3D2030', textLight: 'FFEDE4',
    textMuted: 'B08A8A', cardBg: 'FFFFFF',
    fontTitle: '"Segoe UI", "Microsoft YaHei", sans-serif',
    fontBody: '"Segoe UI", "Microsoft YaHei", sans-serif',
  },
  {
    id: 'ink-wash',
    name: '水墨中国',
    vibe: '东方意境：黑白灰+朱砂红印章色，适合传统文化、诗词书画、国风主题',
    primary: '2B2B2B', secondary: '575757', accent: 'C0392B',
    bgDark: '1C1C1C', bgLight: 'FAFAF7', textDark: '262626', textLight: 'F5F5F0',
    textMuted: '8C8C8C', cardBg: 'FFFFFF',
    fontTitle: '"SimHei", "Microsoft YaHei", sans-serif',
    fontBody: '"Microsoft YaHei", sans-serif',
  },
];

/** 16进制色混合：a 与 b 按 ratio 混合（ratio=1 全取 b）。深色模式卡片/分隔线派生用 */
export function mixHex(a: string, b: string, ratio: number): string {
  const norm = (h: string): [number, number, number] => {
    const s = h.replace('#', '');
    const full = s.length === 3 ? s.split('').map((c) => c + c).join('') : s;
    return [parseInt(full.slice(0, 2), 16), parseInt(full.slice(2, 4), 16), parseInt(full.slice(4, 6), 16)];
  };
  const [r1, g1, b1] = norm(a);
  const [r2, g2, b2] = norm(b);
  const mixCh = (x: number, y: number): number => Math.round(x + (y - x) * ratio);
  return [mixCh(r1, r2), mixCh(g1, g2), mixCh(b1, b2)]
    .map((c) => c.toString(16).padStart(2, '0'))
    .join('')
    .toUpperCase();
}

/** 深底可读化：偏暗颜色提亮（供深色模式的标题/边框使用） */
export function liftOnDark(hex: string): string {
  const s = hex.replace('#', '');
  const full = s.length === 3 ? s.split('').map((c) => c + c).join('') : s;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  const lum = 0.299 * r + 0.587 * g + 0.114 * b;
  return lum < 100 ? mixHex(hex, 'FFFFFF', 0.45) : hex;
}

/** 按主题 id 取主题；未知 id 回落商务蓝 */
export function getTheme(id: string | undefined): SlideTheme {
  return THEMES.find((t) => t.id === id) ?? THEMES[1]!;
}

/** auto 主题：按任务描述关键词匹配气质 */
export function pickThemeByVibe(text: string): SlideTheme {
  const t = text.toLowerCase();
  const rules: Array<[RegExp, string]> = [
    [/科技|技术|ai|互联网|数码|极客|酷炫|未来|学生/, 'tech-dark'],
    [/答辩|学术|论文|课题|研究|政策/, 'academic'],
    [/年终|总结表彰|红色|党建|节庆|国庆|春节/, 'red-gold'],
    [/营销|活动|动员|促销|大促|双十一/, 'warm-energy'],
    [/健康|环保|绿色|自然|生活|饮食/, 'fresh-mint'],
    [/时尚|品牌|艺术|人文|美妆|沙龙/, 'purple-elegant'],
    [/旅行|生活方式|情感|治愈|分享/, 'sunset-gradient'],
    [/国风|传统|文化|诗词|书法|水墨/, 'ink-wash'],
    [/设计|极简|品牌理念|高端/, 'minimal-light'],
  ];
  for (const [re, id] of rules) {
    if (re.test(t)) return getTheme(id);
  }
  return getTheme('business-blue');
}
