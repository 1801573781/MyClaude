/**
 * 按 Electron ABI 重建原生模块（better-sqlite3 等）
 * 背景：pnpm install 按系统 Node ABI（如 137）安装预编译二进制，
 *       Electron 33 内置 Node ABI 为 130，主进程 require 时报 ERR_DLOPEN_FAILED。
 * 方案：用 electron-rebuild 重建，或借助 prebuild-install 直接下载
 *       Electron 官方预编译产物（无需本地编译工具链）。
 * 用法：node scripts/rebuild-electron-natives.mjs
 */
import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd());

/** 读取已安装 Electron 的精确版本（从 apps/desktop 解析） */
function getElectronVersion() {
  const pkgPath = path.join(ROOT, "apps/desktop/node_modules/electron/package.json");
  if (!existsSync(pkgPath)) {
    throw new Error("未找到 apps/desktop/node_modules/electron，请先 pnpm install");
  }
  return JSON.parse(readFileSync(pkgPath, "utf-8")).version;
}

/** 在指定目录执行命令（继承输出） */
function run(cmd, cwd) {
  console.log(`[rebuild] ${cmd}  (cwd: ${cwd})`);
  execSync(cmd, { cwd, stdio: "inherit" });
}

const electronVersion = getElectronVersion();
console.log(`[rebuild] Electron 版本：${electronVersion}`);

// ─────────── 方案 A：electron-rebuild（标准做法，可处理所有原生依赖） ───────────
try {
  // electron-rebuild 是根 devDependency（scripts/rebuild 依赖其显式安装）
  const erBin = path.join(ROOT, "node_modules/.bin/electron-rebuild");
  const erCmd = existsSync(erBin)
    ? `node "${erBin}" -v ${electronVersion} -f -w better-sqlite3`
    : `pnpm.cmd exec electron-rebuild -v ${electronVersion} -f -w better-sqlite3`;
  run(erCmd, path.join(ROOT, "apps/desktop"));
  console.log("[rebuild] ✅ electron-rebuild 完成");
} catch (err) {
  console.warn(`[rebuild] ⚠ electron-rebuild 失败：${err.message}`);
  console.warn("[rebuild] 回退方案 B：prebuild-install 直拉 Electron 预编译产物");

  // ─────────── 方案 B：prebuild-install（better-sqlite3 官方发布 Electron 预编译包） ───────────
  const sqliteDir = path.join(
    ROOT,
    "node_modules/.pnpm",
    `better-sqlite3@12.11.1`,
    "node_modules/better-sqlite3"
  );
  if (!existsSync(sqliteDir)) {
    throw new Error(`未找到 better-sqlite3 目录：${sqliteDir}`);
  }
  // --runtime=electron --target=<electron版本> 指定拉取 Electron ABI 的预编译二进制
  run(
    `pnpm.cmd exec prebuild-install --runtime electron --target ${electronVersion} --verbose || npx.cmd --yes prebuild-install --runtime electron --target ${electronVersion}`,
    sqliteDir
  );
  console.log("[rebuild] ✅ prebuild-install 完成");
}

console.log("[rebuild] 全部完成：better-sqlite3 已按 Electron ABI 就绪");
