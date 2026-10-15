/**
 * Shell 工具（敏感操作，默认逐次确认，安全 §13）
 */
import { spawn } from 'node:child_process';
import type { AgentTool, ToolResult } from './registry.js';

const MAX_OUTPUT = 32 * 1024; // 32KB 输出截断

export const shellExecTool: AgentTool = {
  name: 'shell.exec',
  description: '在工作空间内执行 shell 命令（敏感操作，默认需用户确认）',
  sensitivity: 'sensitive',
  async execute(args): Promise<ToolResult> {
    const command = typeof args['command'] === 'string' ? (args['command'] as string) : '';
    if (!command) return { ok: false, output: '缺少 command 参数', artifacts: [] };

    // Windows cmd.exe 多行内联 python 拦截：cmd 不支持引号内换行，多行
    // python -c 会被截断在首行引号处 → SyntaxError: unterminated string
    // literal。历史上 477.txt 任务的 P3 解析模板步骤即因此失败。
    // 引导改为：先用 fs 工具把 python 写入 .py 文件，再 python file.py 执行
    if (process.platform === 'win32' && /\n/.test(command)) {
      const pyInline = /python\s+(-[A-Za-z]+\s+)*-c\s/.test(command);
      if (pyInline) {
        return {
          ok: false,
          output:
            '检测到多行 python -c 内联命令：Windows cmd.exe 不支持带换行的 -c 传参，' +
            '会被截断为 unterminated string literal（SyntaxError）。' +
            '请改用两步：1) 用 fs.write 把 python 代码写入工作区 .py 文件；' +
            '2) python -X utf8 <脚本路径> <参数> 执行该文件。',
          artifacts: [],
        };
      }
    }

    const isWin = process.platform === 'win32';
    return new Promise<ToolResult>((resolve) => {
      const child = spawn(isWin ? 'cmd.exe' : '/bin/sh', isWin ? ['/c', command] : ['-c', command], {
        cwd: undefined,
        env: { ...process.env, FORCE_COLOR: '0' },
        windowsHide: true,
      });

      let stdout = '';
      let stderr = '';
      child.stdout?.on('data', (d: Buffer) => {
        if (stdout.length < MAX_OUTPUT) stdout += d.toString('utf-8');
      });
      child.stderr?.on('data', (d: Buffer) => {
        if (stderr.length < MAX_OUTPUT) stderr += d.toString('utf-8');
      });
      child.on('error', (err) => {
        resolve({ ok: false, output: `启动失败：${err.message}`, artifacts: [] });
      });
      child.on('exit', (code) => {
        const out = [stdout, stderr ? `STDERR:\n${stderr}` : ''].filter(Boolean).join('\n');
        resolve({
          ok: code === 0,
          output: out || `（无输出，exit=${code}）`,
          artifacts: [],
        });
      });

      // 60s 超时保护
      setTimeout(() => {
        child.kill('SIGKILL');
        resolve({ ok: false, output: '命令执行超时（60s），已终止', artifacts: [] });
      }, 60_000).unref?.();
    });
  },
};
