/** commitlint：强制 Conventional Commits（可维护性 NFR-07） */
module.exports = {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "subject-case": [0],
    "type-enum": [
      2,
      "always",
      [
        "feat",     // 新功能
        "fix",      // 修复
        "docs",     // 文档
        "style",    // 格式
        "refactor", // 重构
        "perf",     // 性能
        "test",     // 测试
        "build",    // 构建
        "ci",       // 持续集成
        "chore",    // 杂务
        "revert",   // 回滚
      ],
    ],
  },
};
