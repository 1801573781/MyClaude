#!/usr/bin/env node
/**
 * WDBuddy 开发模式入口
 * 主进程（tsc watch）+ 渲染进程（Vite dev server）+ Agent Runtime 并行热更。
 * Vite dev server 端口 5173，主进程开发模式加载 http://localhost:5173。
 */
import { spawn, exec } from "node:child_process";

const isWin = process.platform === "win32";
const npmCmd = isWin ? "npm.cmd" : "npm";
const pkgManager = isWin ? "pnpm.cmd" : "pnpm";

const procs = [];
let shuttingDown = false;

/**
 * 跨平台 spawn：Windows 下必须走 shell 才能执行 .cmd 脚本，
 * 而 "shell:true + args 数组" 已被 Node 弃用（DEP0190），故拼接为完整命令字符串。
 */
function smartSpawn(cmd, args, options) {
  if (isWin) return spawn([cmd, ...args].join(" "), { shell: true, ...options });
  return spawn(cmd, args, options);
}

/** 强杀进程树：Windows 下 taskkill /T 递归终止，避免 p.kill() 只杀 cmd 壳留下孤儿 */
function killTree(child) {
  if (!child?.pid || child.exitCode !== null) return;
  if (isWin) {
    exec(`taskkill /T /F /PID ${child.pid}`, () => { /* 尽力而为 */ });
  } else {
    try {
      child.kill("SIGKILL");
    } catch {
      /* ignore */
    }
  }
}

/**
 * 构建依赖包（turbo run build），带退出挂死防护。
 * 实测（2026-10-07，Win11 + turbo 2.11.7）：当存在 cache-miss 任务时，turbo 打印完
 * "Tasks: N successful, N total" 摘要后进程可能不退出，导致 dev.mjs 在 await 处永久
 * 挂死（终端停在摘要行后，renderer/desktop 均不会被拉起，表现为"什么都看不到"）。
 * 防护策略：
 *   1. 构建输出改走管道并原样转发（终端显示不变），同时嗅探完成摘要行；
 *   2. 摘要打印即代表全部任务已结束、产物已落盘——若进程在宽限期内仍未退出，
 *      判定为退出挂死：强杀进程树并按摘要结果放行，启动流程继续；
 *   3. 总看门狗：超时仍无摘要则强杀并中止（防御构建任务本身卡死）。
 */
function runBuildGuarded(timeoutMs = 600000) {
  const GRACE_MS = 15000;
  return new Promise((resolve) => {
    const args = ["exec", "turbo", "run", "build", "--filter=!@wdbuddy/renderer", "--filter=!@wdbuddy/e2e"];
    const p = smartSpawn(pkgManager, args, { stdio: "pipe" });
    let settled = false;
    let summaryOk = null; // null=未见摘要；true=全部成功；false=存在失败
    let graceTimer = null;
    let watchdog = null;
    let tail = ""; // 滚动缓冲：摘要行可能被分块截断
    const finish = (code) => {
      if (settled) return;
      settled = true;
      if (graceTimer) clearTimeout(graceTimer);
      if (watchdog) clearTimeout(watchdog);
      resolve(code);
    };
    const onChunk = (d) => {
      const text = String(d);
      process.stdout.write(text);
      if (summaryOk !== null) return;
      tail = (tail + text).slice(-400);
      const m = tail.match(/Tasks:\s*(\d+)\s+successful(?:,\s*(\d+)\s+failed)?,\s*(\d+)\s+total/);
      if (!m) return;
      summaryOk = m[1] === m[3] && (!m[2] || Number(m[2]) === 0);
      // 摘要已打印：给 turbo 正常退出的宽限机会，超时则强杀放行
      graceTimer = setTimeout(() => {
        console.error("\n[dev] ⚠ turbo 已打印构建摘要但未退出（Windows 退出挂死），强制结束进程树并继续");
        killTree(p);
        finish(summaryOk ? 0 : 1);
      }, GRACE_MS);
    };
    p.stdout?.on("data", onChunk);
    p.stderr?.on("data", onChunk);
    p.on("error", (err) => {
      console.error("[dev] ✖ 构建进程启动失败：", err);
      finish(1);
    });
    // 用 close（而非 exit）确保 stdio 刷盘完成；正常退出时以 turbo 退出码为准
    // （失败时 turbo 必返回非零，无需依赖摘要嗅探结果改判）
    p.on("close", (code) => finish(code ?? 1));
    watchdog = setTimeout(() => {
      if (settled) return;
      console.error(`\n[dev] ✖ 构建超时（${timeoutMs / 1000}s 无完成摘要），强制终止`);
      killTree(p);
      finish(1);
    }, timeoutMs);
  });
}

function run(name, cmd, args, cwd, extraEnv = {}, opts = {}) {
  console.log(`[dev] ▶ ${name}: ${cmd} ${args.join(" ")}`);
  const p = smartSpawn(cmd, args, { cwd, stdio: "pipe", env: { ...process.env, FORCE_COLOR: "1", ...extraEnv } });
  const tag = `[${name}]`;
  p.stdout?.on("data", (d) => process.stdout.write(String(d).split("\n").map((l) => (l ? tag + " " + l : l)).join("\n")));
  p.stderr?.on("data", (d) => process.stderr.write(String(d).split("\n").map((l) => (l ? tag + " " + l : l)).join("\n")));
  p.on("exit", (code) => {
    console.log(`${tag} exited with ${code}`);
    // 关键进程退出时整体收尾，避免父进程陪着一个已崩溃的会话"僵"在终端：
    //  - critical:"always"：任何退出（用户关窗=0 / 崩溃≠0）都结束 dev 会话
    //  - critical:"fail"  ：仅异常退出时结束 dev 会话
    if (shuttingDown) return;
    if (opts.critical === "always" || (opts.critical === "fail" && code !== 0)) {
      if (code !== 0) console.error(`[dev] ✖ 关键进程 ${name} 异常退出(code=${code})，终止整个开发会话`);
      else console.log(`[dev] 关键进程 ${name} 已退出，结束开发会话`);
      shutdown(code ?? 0);
    }
  });
  procs.push(p);
  return p;
}

/** 判断渲染进程 Vite 是否已就绪 */
async function waitViteReady(port = 5173, timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(`http://localhost:${port}`);
      if (res.ok) return true;
    } catch {
      /* not ready yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

async function main() {
  console.log("[dev] WDBuddy 开发模式启动……");

  // 1. 先构建依赖包与桌面主进程（shared-types / sandbox / model-gateway / desktop 等），
  //    确保 Electron 运行的 dist 始终为最新产物，构建失败直接中止
  //    （runBuildGuarded：防御 turbo 打印完摘要后不退出导致启动永久挂死）
  const buildCode = await runBuildGuarded();
  if (buildCode !== 0) {
    console.error(`[dev] ✖ 依赖包构建失败(code=${buildCode})，中止启动`);
    process.exit(buildCode);
  }

  // 2. 渲染进程 Vite dev server（异常退出视为致命）
  run("renderer", npmCmd, ["run", "dev"], "apps/renderer", {}, { critical: "fail" });

  // 3. 等 Vite 就绪后启动 Electron 主进程（WB_DEV=1 → 加载 Vite dev server 而非本地文件）
  const ready = await waitViteReady();
  if (!ready) {
    console.error("[dev] ⚠ Vite dev server 未在 60s 内就绪，仍继续启动 Electron");
  }
  // desktop 是整个会话的宿主：用户关窗(0)或崩溃(≠0)都应结束 dev 会话
  run("desktop", npmCmd, ["run", "dev"], "apps/desktop", { WB_DEV: "1", NODE_ENV: "development" }, { critical: "always" });

  // Agent Runtime 由主进程 runtime-bridge 按需拉起，无需独立启动
  console.log("[dev] 全部进程已拉起（Agent Runtime 由 runtime-bridge 守护拉起）");
}

function shutdown(exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log("\n[dev] 正在关闭全部子进程……");
  const alive = procs.filter((p) => p.exitCode === null && p.pid);
  const finish = () => process.exit(exitCode);
  if (alive.length === 0) {
    finish();
    return;
  }
  if (isWin) {
    // Windows 下 p.kill() 只能杀掉 cmd.exe 壳，其孙进程（vite/electron）会变孤儿
    // 残留并占用 5173 端口，必须用 taskkill /T 递归终止整棵进程树
    const pidArgs = alive.map((p) => `/PID ${p.pid}`).join(" ");
    exec(`taskkill /T /F ${pidArgs}`, () => finish());
    setTimeout(finish, 3000); // taskkill 超时兜底
  } else {
    for (const p of alive) {
      try {
        p.kill("SIGTERM");
      } catch {
        /* ignore */
      }
    }
    setTimeout(finish, 1000);
  }
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

main().catch((e) => {
  console.error("[dev] 启动失败：", e);
  process.exit(1);
});
