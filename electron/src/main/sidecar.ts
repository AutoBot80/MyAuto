import { spawn, type ChildProcessWithoutNullStreams } from "child_process";
import { execSync } from "child_process";
import fs from "fs";
import path from "path";
import { logError, logInfo } from "./logger";
import { getRepoRootFromMain, getSidecarExePath, getSidecarScriptPath } from "./paths";

export interface SidecarJobPayload {
  type: string;
  saathi_base_dir?: string;
  timeoutMs?: number;
  params?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SidecarJobResult {
  success: boolean;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  timedOut?: boolean;
  parsed?: unknown;
  error?: string;
}

const activePids = new Set<number>();

function defaultTimeoutMs(payload: SidecarJobPayload): number {
  if (typeof payload.timeoutMs === "number" && payload.timeoutMs > 0) {
    return payload.timeoutMs;
  }
  if (payload.type === "fill_dms") {
    return 900_000;
  }
  return 120_000;
}

function killProcessTree(pid: number): void {
  if (process.platform === "win32") {
    try {
      execSync(`taskkill /F /T /PID ${pid}`, { windowsHide: true, stdio: "ignore" });
    } catch {
      // ignore
    }
  } else {
    try {
      process.kill(-pid, "SIGKILL");
    } catch {
      try {
        process.kill(pid, "SIGKILL");
      } catch {
        // ignore
      }
    }
  }
  activePids.delete(pid);
}

export function killAllSidecarJobs(): void {
  for (const pid of [...activePids]) {
    killProcessTree(pid);
  }
}

function resolvePython(): string {
  const v = process.env.SAATHI_PYTHON?.trim();
  if (v && fs.existsSync(v)) return v;
  return "python";
}

export async function runSidecarJob(payload: SidecarJobPayload): Promise<SidecarJobResult> {
  const timeoutMs = defaultTimeoutMs(payload);
  const baseDir = payload.saathi_base_dir?.trim();
  const env = {
    ...process.env,
    SAATHI_BASE_DIR: baseDir || process.env.SAATHI_BASE_DIR || "D:\\Saathi",
  };

  let proc: ChildProcessWithoutNullStreams;
  const exe = getSidecarExePath();

  if (exe) {
    logInfo(`sidecar: spawn exe ${exe}`);
    proc = spawn(exe, [], {
      env,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    }) as ChildProcessWithoutNullStreams;
  } else {
    const script = getSidecarScriptPath();
    if (!fs.existsSync(script)) {
      const err = `Sidecar script missing: ${script}`;
      logError(err);
      return {
        success: false,
        stdout: "",
        stderr: err,
        exitCode: null,
        error: err,
      };
    }
    const py = resolvePython();
    const cwd = getRepoRootFromMain();
    logInfo(`sidecar: spawn ${py} ${script} (cwd=${cwd})`);
    proc = spawn(py, [script], {
      cwd,
      env: { ...env, PYTHONPATH: path.join(cwd, "backend") },
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    }) as ChildProcessWithoutNullStreams;
  }

  const pid = proc.pid;
  if (pid) activePids.add(pid);

  const stdoutChunks: Buffer[] = [];
  const stderrChunks: Buffer[] = [];

  proc.stdout.on("data", (c: Buffer) => stdoutChunks.push(c));
  proc.stderr.on("data", (c: Buffer) => stderrChunks.push(c));

  const stdinPayload = JSON.stringify({
    ...payload,
    saathi_base_dir: baseDir || env.SAATHI_BASE_DIR,
  });

  proc.stdin.write(stdinPayload, "utf8");
  proc.stdin.end();

  let timedOut = false;
  const killTimer = setTimeout(() => {
    timedOut = true;
    logError(`sidecar: timeout after ${timeoutMs}ms, killing pid ${pid}`);
    if (pid) killProcessTree(pid);
  }, timeoutMs);

  const exitCode: number | null = await new Promise((resolve) => {
    proc.on("error", (e: Error) => {
      logError("sidecar spawn error", e);
      resolve(null);
    });
    proc.on("close", (code: number | null) => {
      clearTimeout(killTimer);
      if (pid) activePids.delete(pid);
      resolve(code);
    });
  });

  const stdout = Buffer.concat(stdoutChunks).toString("utf8");
  const stderr = Buffer.concat(stderrChunks).toString("utf8");

  let parsed: unknown;
  try {
    const line = stdout.trim();
    parsed = line ? JSON.parse(line) : undefined;
  } catch (e) {
    logError("sidecar: invalid JSON stdout", e);
    return {
      success: false,
      stdout,
      stderr,
      exitCode,
      timedOut,
      error: "Sidecar did not return valid JSON on stdout",
    };
  }

  const obj = parsed as { success?: boolean } | undefined;
  const success =
    !timedOut &&
    exitCode === 0 &&
    obj !== undefined &&
    typeof obj === "object" &&
    obj.success === true;

  return {
    success,
    stdout,
    stderr,
    exitCode,
    timedOut,
    parsed,
    error: timedOut ? "Job timed out" : !success ? (obj as { error?: string })?.error || stderr || undefined : undefined,
  };
}
