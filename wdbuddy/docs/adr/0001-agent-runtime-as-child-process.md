# ADR-0001：Agent Runtime 为独立 Node 子进程

- 状态：已接受
- 日期：2026-10-05

## 背景

WDBuddy 的核心是 Agent 循环（Planner → Executor → Replanner → Verifier），执行过程中包含 LLM 长调用、文件操作、shell 执行等重负载与不可控因素。若将 Agent 循环放进主进程或渲染进程，任何崩溃/阻塞都会拖垮整个应用。

## 决策

Agent Runtime 作为**独立 Node 子进程**运行（`packages/agent-runtime`），由主进程 `runtime-bridge` 拉起并守护。

## 理由

1. **崩溃隔离**：Agent 崩不拖垮 UI——子进程崩溃后看门狗自动重启（≤3 次指数退避，超限进入 `runtime-offline` 降级态）
2. **独立资源管控**：内存/CPU 可单独监控与限制，满足稳态内存 ≤600MB 指标
3. **可独立重启与升级**：Runtime 版本更新无需重启整个应用

## 后果

- 进程间通信引入 JSON-RPC over stdio（见 ADR-0003）
- 权限检查必须跨进程可信：收敛到 Runtime 内的统一沙箱层（见 ADR-0004）
