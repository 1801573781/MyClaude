# ADR-0004：所有工具的权限检查收敛于统一沙箱层

- 状态：已接受
- 日期：2026-10-05

## 背景

Agent 会执行文件读写、shell 命令、网页打开等敏感操作。模型输出不可 100% 信任（幻觉路径、提示词注入诱导），若权限检查散落各工具，极易遗漏且难以审计。

## 决策

所有工具的权限检查**单点收敛**于 `packages/sandbox`：

- **fs-guard**：路径规范化 → 符号链接解析后必须仍在授权根内（防 `..` 穿越与软链逃逸）→ 系统敏感目录黑名单 → 越界拒绝
- **policy-engine**：纯函数 `(tool, args, permissionLevel) → allow | deny | require-confirm`，默认权限下敏感操作阻塞等待用户决定

## 理由

1. **单点强制**：绕过沙箱 = 绕过全部工具入口，不存在漏网路径
2. **可单测**：policy-engine 纯函数 100% 分支覆盖（含路径穿越/符号链接逃逸用例）
3. **不可绕过**：Executor 只持有 `policyFor()` 返回的策略闭包，无法接触未受控 fs API

## 后果

- 新增工具必须在 registry 注册并声明 sensitivity，天然进入裁决链
- `require-confirm` 走 `task/permission.request` 通知 → UI 权限卡片 → `runtime/permission.respond` 应答闭环
- 与渲染沙箱（iframe sandbox + contextIsolation）构成双保险
