import { spawn, type ChildProcessWithoutNullStreams } from "child_process";
import { execSync } from "child_process";
import { createInterface } from "node:readline";
import fs from "fs";
import path from "path";
import { logError, logInfo } from "./logger";
import { getRepoRootFromMain, getSaathiBaseDir, getSidecarExePath, getSidecarScriptPath } from "./paths";

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

/** Long-lived sidecar: one JSON line in (stdin), one JSON line out (stdout) per job. */
let daemonProc: ChildProcessWithoutNullStreams | null = null;
let daemonRl: ReturnType<typeof createInterface> | null = null;
let sidecarRunChain: Promise<void> = Promise.resolve();

function resetSidecarDaemon(): void {
  if (daemonRl) {
    try {
      daemonRl.close();
    } catch {
      /* ignore */
    }
    daemonRl = null;
  }
  if (daemonProc?.pid) {
    killProcessTree(daemonProc.pid);
  }
  daemonProc = null;
}

function defaultTimeoutMs(payload: SidecarJobPayload): number {
  if (typeof payload.timeoutMs === "number" && payload.timeoutMs > 0) {
    return payload.timeoutMs;
  }
  if (payload.type === "fill_dms" || payload.type === "fill_insurance") {
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
  resetSidecarDaemon();
  for (const pid of [...activePids]) {
    killProcessTree(pid);
  }
}

function resolvePython(): string {
  const v = process.env.SAATHI_PYTHON?.trim();
  if (v && fs.existsSync(v)) return v;
  return "python";
}

function ensureSidecarDaemon(baseDir: string | undefined): ChildProcessWithoutNullStreams | null {
  const env = {
    ...process.env,
    SAATHI_BASE_DIR: baseDir || getSaathiBaseDir(),
    PYTHONUNBUFFERED: "1",
  };
  if (daemonProc && daemonRl) {
    return daemonProc;
  }

  const exe = getSidecarExePath();
  let proc: ChildProcessWithoutNullStreams;

  if (exe) {
    logInfo(`sidecar daemon: spawn exe ${exe}`);
    proc = spawn(exe, ["--daemon"], {
      env,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    }) as ChildProcessWithoutNullStreams;
  } else {
    const script = getSidecarScriptPath();
    if (!fs.existsSync(script)) {
      logError(`Sidecar script missing: ${script}`);
      return null;
    }
    const py = resolvePython();
    const cwd = getRepoRootFromMain();
    logInfo(`sidecar daemon: spawn ${py} -u ${script} --daemon (cwd=${cwd})`);
    proc = spawn(py, ["-u", script, "--daemon"], {
      cwd,
      env: { ...env, PYTHONPATH: path.join(cwd, "backend") },
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    }) as ChildProcessWithoutNullStreams;
  }

  proc.stderr.on("data", (c: Buffer) => {
    logError(`sidecar daemon stderr: ${c.toString("utf8").trimEnd()}`);
  });

  const pid = proc.pid;
  if (pid) activePids.add(pid);

  proc.on("close", () => {
    if (pid) activePids.delete(pid);
    if (daemonProc === proc) {
      resetSidecarDaemon();
    }
  });

  daemonProc = proc;
  daemonRl = createInterface({ input: proc.stdout, crlfDelay: Infinity });
  return proc;
}

async function readDaemonResponseLine(timeoutMs: number): Promise<string> {
  const rl = daemonRl;
  if (!rl) {
    throw new Error("Sidecar daemon readline not ready");
  }
  return await new Promise<string>((resolve, reject) => {
    const onLine = (line: string): void => {
      clearTimeout(t);
      resolve(line);
    };
    const t = setTimeout(() => {
      rl.removeListener("line", onLine);
      logError(`sidecar daemon: read timeout after ${timeoutMs}ms`);
      resetSidecarDaemon();
      reject(new Error("Sidecar daemon response timeout"));
    }, timeoutMs);
    rl.once("line", onLine);
  });
}

function parseSidecarStdout(stdout: string): unknown {
  const line = stdout.trim();
  return line ? JSON.parse(line) : undefined;
}

function buildSidecarResult(params: {
  parsed: unknown;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  timedOut: boolean;
}): SidecarJobResult {
  const { parsed, stdout, stderr, exitCode, timedOut } = params;
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

/** One-shot process: stdin closed after one job (fallback if daemon unavailable). */
async function runSidecarJobOneshot(payload: SidecarJobPayload): Promise<SidecarJobResult> {
  const timeoutMs = defaultTimeoutMs(payload);
  const baseDir = payload.saathi_base_dir?.trim();
  const env = {
    ...process.env,
    SAATHI_BASE_DIR: baseDir || getSaathiBaseDir(),
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
    parsed = parseSidecarStdout(stdout);
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

  return buildSidecarResult({ parsed, stdout, stderr, exitCode, timedOut });
}

async function runSidecarJobDaemon(payload: SidecarJobPayload): Promise<SidecarJobResult> {
  const timeoutMs = defaultTimeoutMs(payload);
  const baseDir = payload.saathi_base_dir?.trim();
  const proc = ensureSidecarDaemon(baseDir);
  if (!proc || !daemonRl) {
    return runSidecarJobOneshot(payload);
  }

  const stdinPayload = JSON.stringify({
    ...payload,
    saathi_base_dir: baseDir || getSaathiBaseDir(),
  });

  proc.stdin.write(stdinPayload + "\n", "utf8");

  let line: string;
  try {
    line = await readDaemonResponseLine(timeoutMs);
  } catch (e) {
    logError("sidecar daemon read failed", e);
    return runSidecarJobOneshot(payload);
  }

  const stdout = line;
  const stderr = "";

  let parsed: unknown;
  try {
    parsed = parseSidecarStdout(stdout);
  } catch (e) {
    logError("sidecar daemon: invalid JSON stdout", e);
    resetSidecarDaemon();
    return runSidecarJobOneshot(payload);
  }

  return buildSidecarResult({
    parsed,
    stdout,
    stderr,
    exitCode: 0,
    timedOut: false,
  });
}

export async function runSidecarJob(payload: SidecarJobPayload): Promise<SidecarJobResult> {
  const prev = sidecarRunChain;
  let release = (): void => {};
  sidecarRunChain = new Promise<void>((r) => {
    release = r;
  });
  await prev;
  try {
    return await runSidecarJobDaemon(payload);
  } finally {
    release();
  }
}
