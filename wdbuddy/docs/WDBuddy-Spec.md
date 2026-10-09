# WDBuddy 需求规格说明书 & 设计规格说明书

> 本目录存放工程文档。需求与设计规格的**权威原件**位于：
> `D:/AI/MyClaude/spec/WDBuddy-Spec.md`

本工程（`root/ = d:/ai/myclaude/wdbuddy/`）依据该规格 v1.0 生成，目录结构以规格 §9 为唯一标准。

## 与规格的对应关系

| 规格 | 工程落位 |
|---|---|
| §8.1 三进程模型 | `apps/desktop`（Main）+ `apps/renderer`（Renderer）+ `packages/agent-runtime`（Runtime） |
| §9 目录结构 | 本仓库根目录即 §9 的 `root/` |
| §10.1 主进程 | `apps/desktop/src/main/`（runtime-bridge / task-store / settings / auth / updater / notification / db） |
| §10.2 渲染进程 | `apps/renderer/src/`（WorkbenchLayout + task-list/chat/artifacts 三 feature，边界由 ESLint 强制） |
| §10.3 Agent Runtime | `packages/agent-runtime/src/`（core/loop + planner + executor + replanner + verifier + checkpoint） |
| §10.4 模型网关 | `packages/model-gateway/src/`（gateway + router + retry + providers×6） |
| §10.5 沙箱 | `packages/sandbox/src/`（fs-guard + policy-engine） |
| §10.6 技能系统 | `packages/skill-sdk/src/`（manifest + registry） |
| §11 接口 | `packages/shared-types/src/index.ts`（IPC/JSON-RPC/REST 类型单一事实源） |
| §12 数据模型 | `shared-types` 类型 + `apps/desktop/src/main/db/schema.sql`（SQLite 8 表） |
| §16 测试 | 各包 `*.test.ts`（单元）+ `e2e/tests/golden-scenarios.spec.ts`（20 黄金场景）+ `packages/testing/mock-model-server` |

## ADR 索引

- [ADR-0001 Agent Runtime 为独立 Node 子进程](./adr/0001-agent-runtime-as-child-process.md)
- [ADR-0002 SQLite（WAL）+ 步骤级检查点](./adr/0002-sqlite-wal-checkpoint.md)
- [ADR-0003 Main↔Runtime JSON-RPC over stdio](./adr/0003-json-rpc-over-stdio.md)
- [ADR-0004 权限检查收敛统一沙箱层](./adr/0004-unified-sandbox-layer.md)
- [ADR-0005 OpenAI 兼容抽象 + Provider 适配器](./adr/0005-openai-compat-provider-adapter.md)
