# ADR-0005：模型接入统一为 OpenAI 兼容协议抽象 + Provider 适配器

- 状态：已接受
- 日期：2026-10-05

## 背景

产品需支持 DeepSeek / 智谱 GLM / Kimi / MiniMax / 混元 / Auto 路由 / 自定义 API 七类模型源。各厂商协议细节各异，直接在业务代码里写分支会导致爆炸式复杂度。

## 决策

`packages/model-gateway` 统一抽象：

- 统一流式接口：`chatStream(req): AsyncIterable<Delta>`
- 每个 Provider 一个适配器文件（`providers/*.ts`），全部复用 OpenAI 兼容基座（`openai-compat.ts`：SSE 解析 + chat）
- **新增模型 = 实现 1 个文件 + registry 注册**
- `router` 实现 Auto 模式：按任务类型/步骤特征路由（长上下文→Kimi，代码/调研→DeepSeek，轻问答→GLM）
- `retry`：超时 + 指数退避 + Provider 降级链，流中断可续

## 理由

1. 国内主流模型厂商均提供 OpenAI 兼容端点，收敛为单一协议抽象成本最低
2. 适配器模式让模型差异（鉴权/模型名/端点）隔离在单文件内
3. 自定义 API（settings 页录入）复用同一工厂

## 后果

- API Key 由主进程从 `safeStorage` 钥匙串解析注入，gateway 不持有明文
- 路由表为纯函数，配套单测全分支覆盖
