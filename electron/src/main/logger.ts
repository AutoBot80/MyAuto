import fs from "fs";
import path from "path";
import { getLogsDir } from "./paths";

const MAX_BYTES = 10 * 1024 * 1024;

function ensureLogDir(): string {
  const dir = getLogsDir();
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function rotateIfNeeded(logPath: string): void {
  try {
    const st = fs.statSync(logPath);
    if (st.size < MAX_BYTES) return;
    const bak = `${logPath}.1`;
    if (fs.existsSync(bak)) fs.unlinkSync(bak);
    fs.renameSync(logPath, bak);
  } catch {
    // ignore
  }
}

export function logAppLine(level: string, message: string): void {
  const dir = ensureLogDir();
  const logPath = path.join(dir, "app.log");
  rotateIfNeeded(logPath);
  const line = `${new Date().toISOString()} [${level}] ${message}\n`;
  fs.appendFileSync(logPath, line, { encoding: "utf8" });
}

export function logInfo(message: string): void {
  logAppLine("INFO", message);
}

export function logError(message: string, err?: unknown): void {
  const extra = err instanceof Error ? err.stack || err.message : err ? String(err) : "";
  logAppLine("ERROR", extra ? `${message} ${extra}` : message);
}
