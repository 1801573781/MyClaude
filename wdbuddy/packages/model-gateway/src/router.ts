/**
 * Auto 模式路由器（§10.4）
 * 按任务类型/步骤特征路由到合适模型；路由表纯函数，100% 单测。
 */
import type { ModelId } from '@wdbuddy/shared-types';

/** 路由特征：由调用方从任务/步骤中提取 */
export interface RouteFeatures {
  /** 任务模式 */
  mode: 'ask' | 'plan' | 'craft';
  /** 是否需要长上下文（Max 模式或批量文件处理） */
  longContext: boolean;
  /** 是否代码类任务（编码辅助/应用开发） */
  codeRelated: boolean;
  /** 是否深度调研（多轮检索-阅读-汇总） */
  research: boolean;
}

/** 路由表：按优先级从上到下匹配 */
const ROUTE_TABLE: ReadonlyArray<{
  match: (f: RouteFeatures) => boolean;
  model: ModelId;
  reason: string;
}> = [
  {
    match: (f) => f.longContext,
    model: 'kimi',
    reason: '长上下文优先路由 Kimi（moonshot-v1-128k）',
  },
  {
    match: (f) => f.research,
    model: 'deepseek',
    reason: '深度调研路由 DeepSeek-V3（推理能力强）',
  },
  {
    match: (f) => f.codeRelated,
    model: 'deepseek',
    reason: '代码类任务路由 DeepSeek（编码能力）',
  },
  {
    match: (f) => f.mode === 'ask',
    model: 'zhipu-glm',
    reason: '轻量问答路由 GLM（低延迟）',
  },
  {
    match: () => true,
    model: 'deepseek',
    reason: '默认兜底 DeepSeek',
  },
];

export interface RouteResult {
  model: ModelId;
  reason: string;
}

/** 路由决策（纯函数） */
export function routeAuto(features: RouteFeatures): RouteResult {
  for (const rule of ROUTE_TABLE) {
    if (rule.match(features)) {
      return { model: rule.model, reason: rule.reason };
    }
  }
  return { model: 'deepseek', reason: '默认兜底 DeepSeek' };
}

/** 从指令文本粗提取代码特征（供 Executor 侧调用） */
export function detectCodeRelated(instruction: string): boolean {
  return /代码|网页|html|css|javascript|typescript|python|函数|bug|报错|编译|重构|应用开发/i.test(
    instruction
  );
}

/** 从指令文本粗提取调研特征 */
export function detectResearch(instruction: string): boolean {
  return /调研|研究报告|竞品|分析报告|资料收集|文献/i.test(instruction);
}
