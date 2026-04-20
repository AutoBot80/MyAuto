import { dialog, shell } from "electron";
import fs from "fs";
import path from "path";
import { getSaathiBaseDir } from "./paths";
import { logError, logInfo } from "./logger";

function resolveUnderBase(userPath: string): string {
  const base = path.resolve(getSaathiBaseDir());
  const resolved = path.isAbsolute(userPath) ? path.resolve(userPath) : path.resolve(base, userPath);
  const rel = path.relative(base, resolved);
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    throw new Error("Path escapes Saathi base directory");
  }
  const normBase = base.toLowerCase();
  const normTarget = resolved.toLowerCase();
  const sep = path.sep;
  if (normTarget !== normBase && !normTarget.startsWith(normBase + sep)) {
    throw new Error("Path must be under the Saathi base directory");
  }
  return resolved;
}

export function listFiles(userPath: string): string[] {
  const resolved = resolveUnderBase(userPath);
  if (!fs.existsSync(resolved)) return [];
  return fs.readdirSync(resolved, { withFileTypes: true }).map((d: fs.Dirent) => d.name);
}

export function moveFile(from: string, to: string): void {
  const a = resolveUnderBase(from);
  const b = resolveUnderBase(to);
  fs.mkdirSync(path.dirname(b), { recursive: true });
  fs.renameSync(a, b);
  logInfo(`file: move ${a} -> ${b}`);
}

export function fileExists(userPath: string): boolean {
  const resolved = resolveUnderBase(userPath);
  return fs.existsSync(resolved);
}

export function openFolder(userPath: string): void {
  const resolved = resolveUnderBase(userPath);
  shell.openPath(resolved).catch((e: unknown) => logError("openFolder", e));
}

export async function selectFolder(): Promise<string | null> {
  const r = await dialog.showOpenDialog({
    properties: ["openDirectory"],
    defaultPath: getSaathiBaseDir(),
  });
  if (r.canceled || !r.filePaths[0]) return null;
  const picked = r.filePaths[0];
  resolveUnderBase(picked);
  return picked;
}
