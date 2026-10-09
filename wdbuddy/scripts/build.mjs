#!/usr/bin/env node
/**
 * WDBuddy 构建入口：pnpm build
 * 依赖拓扑：shared-types → (sandbox / model-gateway / skill-sdk / ui) → agent-runtime → renderer / desktop
 * 由 turbo.json 的 dependsOn: ["^build"] 保证顺序。
 */
import { spawn } from "node:child_process";

const isWin = process.platform === "win32";
const pkgManager = isWin ? "pnpm.cmd" : "pnpm";

function exec(cmd, args) {
  return new Promise((resolve, reject) => {
    console.log(`[build] ▶ ${cmd} ${args.join(" ")}`);
    const p = spawn(cmd, args, { stdio: "inherit", shell: isWin });
    p.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} exited with ${code}`))));
    p.on("error", reject);
  });
}

async function main() {
  await exec(pkgManager, ["exec", "turbo", "run", "build", "--concurrency=4"]);
  console.log("[build] ✅ 全部构建完成");
}

main().catch((e) => {
  console.error("[build] ❌ 构建失败：", e);
  process.exit(1);
});
