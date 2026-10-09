// @ts-check
/**
 * WDBuddy ESLint 扁平配置
 * - 分层边界由 Lint 强制（NFR-07）
 * - features 之间禁止互相引用（renderer 按 feature 切片，§10.2）
 * - 渲染进程禁止直接使用 Node API（NFR-03 安全）
 */
const eslint = require("@eslint/js");
const tseslint = require("typescript-eslint");
const importPlugin = require("eslint-plugin-import");

const restrictedRendererPatterns = [
  {
    group: ["node:*", "fs", "path", "child_process", "os", "electron"],
    message: "Renderer 禁止直接使用 Node/Electron API，必须经 preload contextBridge 白名单。",
  },
];

const restrictedFeatureImports = [
  {
    group: ["**/features/task-list/*"],
    message: "feature 边界（§10.2）：chat/artifacts 等禁止直接引用 task-list。",
  },
  {
    group: ["**/features/chat/*"],
    message: "feature 边界（§10.2）：task-list/artifacts 等禁止直接引用 chat。",
  },
  {
    group: ["**/features/artifacts/*"],
    message: "feature 边界（§10.2）：task-list/chat 等禁止直接引用 artifacts。",
  },
];

module.exports = tseslint.config(
  { ignores: ["**/dist/**", "**/build/**", "**/node_modules/**", "**/*.cjs", "**/*.mjs", "**/coverage/**"] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.ts", "**/*.tsx"],
    plugins: { import: importPlugin },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "import/order": [
        "warn",
        {
          groups: ["builtin", "external", "internal", "parent", "sibling", "index"],
          "newlines-between": "always",
        },
      ],
    },
  },
  {
    files: ["apps/renderer/src/features/chat/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": ["error", { patterns: [restrictedFeatureImports[0], restrictedFeatureImports[2]] }],
    },
  },
  {
    files: ["apps/renderer/src/features/task-list/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": ["error", { patterns: [restrictedFeatureImports[1], restrictedFeatureImports[2]] }],
    },
  },
  {
    files: ["apps/renderer/src/features/artifacts/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": ["error", { patterns: [restrictedFeatureImports[0], restrictedFeatureImports[1]] }],
    },
  },
  {
    files: ["apps/renderer/src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": ["error", { patterns: restrictedRendererPatterns }],
    },
  },
  {
    files: ["**/*.test.ts", "**/*.test.tsx", "packages/testing/**"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  }
);
