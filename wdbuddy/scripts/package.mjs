#!/usr/bin/env node
/**
 * WDBuddy 打包入口：pnpm package
 * 1. 全量构建（依赖包 → renderer → desktop）
 * 2. electron-builder 产出 nsis（Win）/ dmg（macOS）双平台安装包（含自动更新源）
 */
import { spawn } from "node:child_process";

const isWin = process.platform === "win32";
const pkgManager = isWin ? "pnpm.cmd" : "pnpm";

function exec(cmd, args, cwd) {
  return new Promise((resolve, reject) => {
    console.log(`[package] ▶ ${cmd} ${args.join(" ")}${cwd ? " (cwd: " + cwd + ")" : ""}`);
    const p = spawn(cmd, args, { stdio: "inherit", shell: isWin, cwd });
    p.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} exited with ${code}`))));
    p.on("error", reject);
  });
}

async function main() {
  console.log("[package] ① 全量构建……");
  await exec(pkgManager, ["exec", "turbo", "run", "build"]);

  console.log("[package] ② electron-builder 打包（当前平台）……");
  await exec(pkgManager, ["exec", "electron-builder", "--config", "electron-builder.yml"], "apps/desktop");

  console.log("[package] ✅ 打包完成，产物位于 apps/desktop/release/");
}

main().catch((e) => {
  console.error("[package] ❌ 打包失败：", e);
  process.exit(1);
});
