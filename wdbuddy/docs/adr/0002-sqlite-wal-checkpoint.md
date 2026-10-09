# ADR-0002：本地存储 SQLite（WAL）+ 步骤级检查点（事件溯源）

- 状态：已接受
- 日期：2026-10-05

## 背景

桌面端需要本地持久化任务/消息/步骤/产物/设置，且要求断电/崩溃后任务可从检查点恢复、数据零丢失（NFR-02）。

## 决策

- 本地存储采用 **SQLite（WAL 模式）**，单文件易备份
- 每个步骤执行完毕落 **checkpoints 表**（计划 + 步骤 + 工具状态快照，事件溯源）
- 启动时扫描 `running/paused` 任务 → 标记 `interrupted` 并提示恢复

## 理由

1. **断点恢复**：`kill -9` Runtime 后任务可从最近检查点续跑
2. **零丢失**：WAL 模式下写操作先落 WAL 日志再 checkpoint，断电不丢已提交事务
3. **单文件易备份**：每日滚动备份（保留 7 份），temp+rename 原子写

## 后果

- schema 集中在 `apps/desktop/src/main/db/schema.sql`，迁移走 `IF NOT EXISTS` 幂等语句
- 检查点清理：任务完成/删除时清空对应 checkpoints
