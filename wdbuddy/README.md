# WDBuddy — 全场景职场 AI 智能体桌面工作台

> 输入一句话，交付可验收的成果。不是"告诉你怎么做"，而是"直接把活干了"。

## 产品定位

WDBuddy 是一款桌面端 AI 办公智能体（Agent），用户用一句自然语言下达任务，产品在本地自主规划、拆解、执行多步骤任务，并交付**可验收的成果**（文档、表格、PPT、网页、图表、调研报告、海报等）。

- **过程可见**：步骤时间线、实时流式输出、Diff 回滚、版本管理
- **低使用门槛**：内置提示词增强，口语化输入也能稳定输出
- **权限分级**：敏感操作默认需确认，文件操作限定授权目录
- **免部署**：下载即用，微信扫码登录

## 技术架构（三进程模型）

```
Renderer(React)  ──IPC──▶  Main(主进程)  ──JSON-RPC/stdio──▶  Agent Runtime(子进程)
   三栏 UI/状态              窗口/托盘/存储                       Planner/Executor/Verifier
```

| ADR | 决策 |
|---|---|
| 0001 | Agent Runtime 为独立 Node 子进程（崩溃隔离、可独立重启） |
| 0002 | SQLite（WAL）+ 步骤级检查点（事件溯源），断点恢复零丢失 |
| 0003 | Main↔Runtime 采用 JSON-RPC over stdio（无端口、天然进程边界） |
| 0004 | 权限检查收敛于统一沙箱层（fs-guard + policy-engine），单点强制 |
| 0005 | 模型接入统一为 OpenAI 兼容协议抽象 + Provider 适配器 |

## 目录结构（§9 唯一标准）

```
wdbuddy/
├── apps/
│   ├── desktop/     # Electron 应用壳（主进程 + preload）
│   ├── renderer/    # 渲染进程（React 18 + Zustand，三栏工作台）
│   ├── server/      # 云端服务（账号/积分/模型网关/技能市场/多端联动）
│   └── mobile/      # 多端联动端（占位）
├── packages/
│   ├── shared-types/   # ★ 全局唯一类型源
│   ├── agent-runtime/  # ★ Agent 循环（Planner/Executor/Replanner/Verifier）
│   ├── model-gateway/  # 统一流式协议 + Provider 适配器
│   ├── sandbox/        # ★ 权限强制层（fs-guard + policy-engine）
│   ├── skill-sdk/      # 技能定义 SDK（manifest + 声明式权限）
│   ├── ui/             # 跨端共享基础组件
│   └── testing/        # mock-model-server + fixtures
├── e2e/                # Playwright 黄金场景
├── configs/  scripts/  docs/
```

## 开发

```bash
# 安装依赖
pnpm install

# 开发模式（主进程 + 渲染 + runtime 并行热更）
pnpm dev

# 构建 / 测试 / 打包
pnpm build
pnpm test
pnpm e2e
pnpm package
```

## 核心概念

- **三种任务模式**：`Ask`（只读问答）/ `Plan`（计划审批后执行）/ `Craft`（全自主执行交付）
- **沙箱双保险**：Agent 行为沙箱（fs-guard + policy-engine）约束 AI 动作；渲染沙箱（iframe + contextIsolation）约束 AI 生成内容
- **检查点恢复**：`kill -9` Runtime 后任务可从检查点恢复（`interrupted` 状态）
- **类型单一事实源**：IPC 通道、JSON-RPC 协议、REST 接口类型全部由 `shared-types` 派生，禁手写重复类型

详细规格见 `docs/WDBuddy-Spec.md`。
