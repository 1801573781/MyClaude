import { defineWorkspace } from "vitest/config";

export default defineWorkspace([
  "packages/*",
  "apps/renderer",
  {
    // 主进程测试需 node 环境
    test: {
      include: ["apps/desktop/src/**/*.test.ts"],
      environment: "node",
    },
  },
  {
    // 云端服务测试
    test: {
      include: ["apps/server/src/**/*.test.ts"],
      environment: "node",
    },
  },
]);
