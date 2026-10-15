/**
 * WDBuddy 全局唯一类型源（single source of truth）
 * IPC 通道（§11.1）、Runtime JSON-RPC 协议（§11.2）、云端 REST（§11.3）类型全部由此派生。
 * 所有模块仅从此导入，禁止手写重复类型（NFR-07）。
 */

// ─────────────────────────── 基础类型（§12 核心类型） ───────────────────────────

export type TaskMode = 'ask' | 'plan' | 'craft';
export type PermissionLevel = 'default' | 'full';
export type ModelId =
  | 'auto'
  | 'deepseek'
  | 'zhipu-glm'
  | 'kimi'
  | 'minimax'
  | 'hunyuan'
  | `custom:${string}`;
export type TaskStatus =
  | 'draft'
  | 'planning'
  | 'awaiting-approval'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted';

export type TaskTrigger =
  | { type: 'manual' }
  | { type: 'cron'; expr: string }
  | { type: 'event'; watch: string[] };

export interface Task {
  id: string;
  title: string;
  mode: TaskMode;
  status: TaskStatus;
  workspaceUri: string | null;
  permission: PermissionLevel;
  modelId: ModelId;
  maxContext: boolean;
  trigger: TaskTrigger;
  createdAt: number;
  updatedAt: number;
}

export type PlanStepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

export interface PlanStep {
  id: string;
  seq: number;
  title: string;
  status: PlanStepStatus;
  summary?: string;
  /** 失败原因（status=failed 时由 Loop 写入，时间线展开可见） */
  error?: string;
  artifactIds: string[];
  startedAt?: number;
  endedAt?: number;
}

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export interface MessageUsage {
  promptTokens: number;
  completionTokens: number;
}

export interface MessageMeta {
  plan?: PlanStep[];
  stepId?: string;
  model?: string;
  usage?: MessageUsage;
}

export interface Message {
  id: string;
  taskId: string;
  seq: number;
  role: MessageRole;
  content: string; // markdown
  meta?: MessageMeta;
  createdAt: number;
}

export type ArtifactKind = 'file' | 'preview' | 'diff' | 'browser';

export interface Artifact {
  id: string;
  taskId: string;
  kind: ArtifactKind;
  path?: string;
  mimeType: string;
  version: number;
  sha256: string;
  createdAt: number;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  permissions: string[];
  source: 'builtin' | 'market' | 'custom';
}

/** 用户偏好条目（FR-18：自动沉淀的使用习惯，注入后续任务上下文） */
export interface PreferenceItem {
  id: string;
  key: string;
  value: string;
  origin: 'auto' | 'user';
  updatedAt: number;
}

// ─────────────────────────── 事件与权限（§10.5 沙箱裁决） ───────────────────────────

export type ToolName =
  | 'fs.read'
  | 'fs.write'
  | 'fs.list'
  | 'fs.delete'
  | 'shell.exec'
  | 'browser.open'
  | 'office.generate'
  | 'search.web'
  | 'user.ask';

/** policy-engine 裁决结果：允许 / 拒绝 / 需确认 */
export type PolicyDecision = 'allow' | 'deny' | 'require-confirm';

export interface PermissionContext {
  tool: ToolName;
  args: Readonly<Record<string, unknown>>;
  permissionLevel: PermissionLevel;
  workspaceRoots: readonly string[];
}

export interface PermissionRequest {
  requestId: string;
  taskId: string;
  tool: ToolName;
  args: Readonly<Record<string, unknown>>;
  reason: string;
}

export type PermissionResponseAction = 'allow-once' | 'allow-always-task' | 'deny';

// ─────────────────────────── 模型网关（§10.4） ───────────────────────────

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface ChatStreamRequest {
  model: ModelId;
  messages: ChatMessage[];
  maxTokens?: number;
  temperature?: number;
  signal?: AbortSignal;
}

export interface Delta {
  type: 'text';
  text: string;
}

export interface ChatCompletion {
  content: string;
  usage: MessageUsage;
  model: string;
}

export interface ModelProviderInfo {
  id: ModelId;
  name: string;
  protocol: 'openai-compatible';
  baseUrl?: string;
}

// ─────────────────────────── IPC 通道（§11.1 renderer ↔ main） ───────────────────────────

export interface NewTaskInput {
  description: string;
  mode: TaskMode;
  workspaceUri: string | null;
  permission: PermissionLevel;
  modelId: ModelId;
  maxContext: boolean;
  trigger: TaskTrigger;
}

export type TaskControlAction = 'start' | 'pause' | 'resume' | 'cancel' | 'approve' | 'reject';

export interface TaskCreateRequest {
  input: NewTaskInput;
}

export interface TaskControlRequest {
  taskId: string;
  action: TaskControlAction;
  /** Plan 审批批准时携带（可能被用户编辑过的）步骤，Runtime 以此替换执行计划 */
  steps?: PlanStep[];
  /** start 时可选：顶栏当前选择的模型，同步进任务（修复已有任务无法换模型） */
  modelId?: ModelId;
  /** start 时可选：本轮任务模式（Ask/Plan/Craft），同步进任务（修复模式首次固化） */
  mode?: TaskMode;
  /** start 时可选：本轮跟进指令（追加为用户消息并作为本次执行指令） */
  instruction?: string;
}

export interface PlanApprovalRequest {
  taskId: string;
  steps: PlanStep[];
  edited: boolean;
}

export interface PromptEnhanceRequest {
  text: string;
}

export interface PromptEnhanceResult {
  original: string;
  enhanced: string;
  title: string;
  diffRanges?: Array<{ start: number; end: number }>;
}

export interface WorkspaceGrantRequest {
  uri: string;
}

export interface FsRevealRequest {
  path: string;
}

export type TaskFilter =
  | { group: 'all' }
  | { group: 'active' }
  | { group: 'completed' }
  | { group: 'rules' };

export type IpcChannel =
  | 'task:create'
  | 'task:list'
  | 'task:control'
  | 'task:approvePlan'
  | 'prompt:enhance'
  | 'workspace:grant'
  | 'fs:reveal'
  | 'settings:get'
  | 'settings:set'
  | 'providerKey:list'
  | 'providerKey:set'
  | 'runtime:event'
  | 'chat:export';

/** Provider 密钥状态（渲染层展示用，不含明文） */
export interface ProviderKeyEntry {
  modelId: ModelId;
  hasKey: boolean;
  /** 脱敏展示，如 sk-1***abcd；未配置为空串 */
  masked: string;
}

export interface IpcRequestMap {
  'task:create': { input: NewTaskInput };
  'task:list': { filter: TaskFilter };
  'task:control': TaskControlRequest;
  'task:approvePlan': PlanApprovalRequest;
  'prompt:enhance': PromptEnhanceRequest;
  'workspace:grant': WorkspaceGrantRequest;
  'fs:reveal': FsRevealRequest;
  'settings:get': Record<string, never>;
  'settings:set': { settings: AppSettings };
  'providerKey:list': Record<string, never>;
  'providerKey:set': { modelId: ModelId; apiKey: string };
  'runtime:event': { event: RuntimeEvent };
  'chat:export': { taskId: string; format: 'md' | 'html' };
  'templates:preview': Record<string, never>;
}

export type IpcResponseMap = {
  'task:create': { taskId: string };
  'task:list': { tasks: Task[] };
  'task:control': { ok: true };
  'task:approvePlan': { ok: true };
  'prompt:enhance': PromptEnhanceResult;
  'workspace:grant': { ok: true };
  'fs:reveal': { ok: true };
  'settings:get': AppSettings;
  'settings:set': { ok: true };
  'providerKey:list': { keys: ProviderKeyEntry[] };
  'providerKey:set': { ok: true };
  'runtime:event': { ok: true };
  'chat:export': { ok: true; path: string };
  'templates:preview': TemplatesPreviewResult;
};

/** 模版库预览（10 主题 × 真实渲染 deck + 14 版式演示 + 克隆模板） */
export interface TemplatesPreviewResult {
  themes: Array<{
    id: string;
    name: string;
    vibe: string;
    /** 核心色板（带 #） */
    colors: string[];
  }>;
  /** themeId → 迷你 deck HTML（封面/目录/要点/时间线/数据大屏 5 版式） */
  htmls: Record<string, string>;
  /** 14 种版式演示 deck（用商务蓝主题渲染，每页一种版式） */
  layoutsHtml: string;
  /** 克隆模板（内置第三方 pptx，视觉 100% 复刻仅换文字） */
  cloneTemplates?: CloneTemplateInfo[];
}

/** 模版库"克隆模板"分区条目（templates-preview 由扫描模板生成） */
export interface CloneTemplateInfo {
  /** 模板 id（生成时 template 参数直接引用） */
  id: string;
  /** 展示名 */
  name: string;
  /** 一句话风格描述 */
  vibe: string;
  /** 适配场景标签 */
  tags: string[];
  /** 模板总页数 */
  pages: number;
  /** 版式构成（kind → 页数），如 { cover: 2, toc: 1, section: 5 } */
  kinds: Record<string, number>;
  /** 线框预览 deck HTML（每页缩略线框：标题/条目卡/编号槽位示意） */
  previewHtml: string;
}

// ─────────────────────────── Runtime JSON-RPC 协议（§11.2 main ↔ runtime，stdio） ───────────────────────────

export type RuntimeRequestMethod =
  | 'runtime/initialize'
  | 'task/run'
  | 'task/control'
  | 'task/loadCheckpoint'
  | 'templates/preview';

export interface RpcRequest {
  jsonrpc: '2.0';
  id: number;
  method: RuntimeRequestMethod;
  params: RuntimeRunParams | TaskControlRequest | RuntimeProviderConfig | Record<string, never> | LoadCheckpointParams;
}

export interface RuntimeRunParams {
  task: Task;
  instruction: string;
  /** 跟进时的完整对话历史（修复：跟进只传单条指令导致上下文丢失、任务从头重来） */
  history?: ChatMessage[];
  /** 上轮遗留的未完成步骤（存在则继续执行而非重新规划） */
  pendingSteps?: PlanStep[];
}

export interface LoadCheckpointParams {
  taskId: string;
}

export interface RpcResponse {
  jsonrpc: '2.0';
  id: number;
  result?: unknown;
  error?: { code: number; message: string };
}

export type RuntimeNotificationMethod =
  | 'task/plan.ready'
  | 'task/step.update'
  | 'task/token.delta'
  | 'task/artifact.created'
  | 'task/permission.request'
  | 'task/done'
  | 'task/error'
  | 'task/rejected'
  | 'task/cancelled'
  | 'task/awaiting.input'
  | 'runtime/heartbeat';

export interface RuntimeNotification {
  jsonrpc: '2.0';
  method: RuntimeNotificationMethod;
  params: Record<string, unknown>;
}

export type RuntimeEvent =
  | { type: 'plan.ready'; taskId: string; steps: PlanStep[] }
  | { type: 'step.update'; taskId: string; step: PlanStep }
  | { type: 'token.delta'; taskId: string; text: string; usage?: MessageUsage }
  | { type: 'artifact.created'; taskId: string; artifact: Artifact }
  | { type: 'permission.request'; taskId: string; request: PermissionRequest }
  | { type: 'task.done'; taskId: string; report: VerifyReport; artifacts: Artifact[] }
  | { type: 'task.error'; taskId: string; stepId?: string; cause: string }
  | { type: 'task.rejected'; taskId: string; reason: string }
  | { type: 'task.cancelled'; taskId: string; reason: string }
  | { type: 'task.awaiting.input'; taskId: string; question: string; options?: string[] }
  | { type: 'checkpoint.recovery'; taskId: string; hint: string }
  | { type: 'runtime.offline'; reason: string };

// ─────────────────────────── 自检报告（Verifier） ───────────────────────────

export interface VerifyCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface VerifyReport {
  taskId: string;
  checks: VerifyCheck[];
  passed: boolean;
  finishedAt: number;
}

// ─────────────────────────── 设置（§4.7 / §14 AppSettings） ───────────────────────────

export interface CustomProvider {
  id: string;
  name: string;
  baseUrl: string;
  /** 请求体中的 model 名（如 glm-4-plus / qwen-max） */
  apiModel: string;
  /** secureStorage 键名（provider:\<id\>），明文密钥不落设置 */
  apiKeyRef: string;
  protocol: 'openai-compatible';
}

/** Main → Runtime 的 Provider 配置（runtime/initialize 参数与 runtime/providers.update 通知载荷） */
export interface RuntimeProviderConfig {
  /** modelId → API Key（内置 + custom:*；密钥仅在进程间传递，加密落盘） */
  providerKeys: Partial<Record<ModelId, string>>;
  /** modelId → 请求体模型名覆盖（内置 Provider 的"模型名"输入框；空 = 用默认） */
  apiModelOverrides?: Partial<Record<ModelId, string>>;
  /** 用户自定义 Provider 列表（设置页录入） */
  customProviders: CustomProvider[];
}

export interface AppSettings {
  general: {
    language: 'zh-CN' | 'en-US';
    theme: 'light' | 'dark' | 'auto';
    launchAtLogin: boolean;
    telemetry: boolean;
  };
  models: {
    defaultModelId: ModelId;
    customProviders: CustomProvider[];
    /** 内置模型名覆盖：modelId → 请求体 model 字段（空/缺失 = Provider 默认） */
    apiModelOverrides?: Partial<Record<ModelId, string>>;
  };
  permissions: {
    defaultPermission: PermissionLevel;
    confirmBeforeShell: boolean;
    confirmBeforeWriteOutsideWorkspace: boolean;
  };
  workspaces: string[];
  composer: {
    maxModeDefault: boolean;
  };
  remoteLink: {
    enabled: boolean;
    pairedDevices: Array<{ deviceId: string; name: string; pairedAt: number }>;
  };
}

// ─────────────────────────── 云端 REST（§11.3 摘要） ───────────────────────────

export interface WeChatQrResponse {
  qrUrl: string;
  sceneId: string;
  expiresIn: number;
}

export interface CreditsInfo {
  balance: number;
  usage: Array<{ taskId: string; model: string; cost: number; at: number }>;
}

export interface RemoteCommand {
  id: string;
  deviceId: string;
  instruction: string;
  createdAt: number;
  status: 'pending' | 'dispatched' | 'done' | 'failed';
}

export const DEFAULT_APP_SETTINGS: AppSettings = {
  general: { language: 'zh-CN', theme: 'auto', launchAtLogin: false, telemetry: false },
  models: { defaultModelId: 'auto', customProviders: [] },
  permissions: {
    defaultPermission: 'default',
    confirmBeforeShell: true,
    confirmBeforeWriteOutsideWorkspace: true,
  },
  workspaces: [],
  composer: { maxModeDefault: false },
  remoteLink: { enabled: true, pairedDevices: [] },
};

/** 内置模型 id 列表（不含 auto 与 custom:*） */
export const BUILTIN_MODEL_IDS: readonly ModelId[] = ['deepseek', 'zhipu-glm', 'kimi', 'minimax', 'hunyuan'];

/** 内置模型显示名（各下拉框统一数据源） */
export const BUILTIN_MODEL_NAMES: Record<string, string> = {
  deepseek: 'DeepSeek',
  'zhipu-glm': '智谱 GLM',
  kimi: 'Kimi',
  minimax: 'MiniMax',
  hunyuan: '混元',
};

/** 内置模型默认 apiModel（与各 Provider 文件保持一致，设置页 placeholder） */
export const BUILTIN_MODEL_DEFAULTS: Record<string, string> = {
  deepseek: 'deepseek-chat',
  'zhipu-glm': 'glm-4-plus',
  kimi: 'moonshot-v1-128k',
  minimax: 'abab6.5s-chat',
  hunyuan: 'hunyuan-turbo',
};
