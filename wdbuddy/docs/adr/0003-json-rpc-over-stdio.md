# ADR-0003：Main ↔ Runtime 采用 JSON-RPC over stdio

- 状态：已接受
- 日期：2026-10-05

## 背景

主进程与 Agent Runtime 子进程（ADR-0001）需要双向通信：M→R 请求（initialize/task/run/control/loadCheckpoint）与 R→M 通知（plan.ready/step.update/token.delta/permission.request/done/error/heartbeat）。

## 决策

采用 **JSON-RPC 2.0 over stdio**（子进程 stdin/stdout，按行分隔 JSON）。

## 理由

1. **无端口占用**：不监听任何 TCP 端口
2. **免网络安全面**：不暴露本地服务，无未授权访问风险
3. **天然进程边界**：stdio 是父子进程的原生通道，权限即进程权限

## 后果

- 协议类型定义于 `packages/shared-types`（请求/通知/事件联合类型），两端共享防漂移，配套契约测试
- 心跳：Runtime 每 15s 发 `runtime/heartbeat`；主进程看门狗超时即 kill + 退避重启
- 权限应答为非标准通知 `runtime/permission.respond`（对应 UI 三选项）
