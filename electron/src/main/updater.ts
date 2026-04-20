import { app } from "electron";
import fs from "fs";
import path from "path";
import { autoUpdater } from "electron-updater";
import { logError, logInfo } from "./logger";

function resolveGhToken(): string {
  const env = (process.env.GH_TOKEN || "").trim();
  if (env) return env;
  try {
    const p = app.isPackaged
      ? path.join(process.resourcesPath, "update-token.json")
      : path.join(__dirname, "..", "..", "resources", "update-token.json");
    const data = JSON.parse(fs.readFileSync(p, "utf-8"));
    return (data.token || "").trim();
  } catch {
    return "";
  }
}

export type UpdateSender = (channel: string, payload?: unknown) => void;

export function setupAutoUpdater(send: UpdateSender): void {
  if (!app.isPackaged) {
    logInfo("updater: skipped (development build)");
    return;
  }
  const token = resolveGhToken();
  if (!token) {
    logInfo("updater: no GH_TOKEN found — auto-update disabled");
    return;
  }
  (autoUpdater as any).requestHeaders = { Authorization: `token ${token}` };
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;

  autoUpdater.on("checking-for-update", () => logInfo("updater: checking"));
  autoUpdater.on("update-available", (info: import("electron-updater").UpdateInfo) => {
    logInfo("updater: update available");
    send("update:available", info);
  });
  autoUpdater.on("update-not-available", () => logInfo("updater: no update"));
  autoUpdater.on("update-downloaded", (info: import("electron-updater").UpdateInfo) => {
    logInfo("updater: update downloaded (restart required to install)");
    send("update:downloaded", info);
  });
  autoUpdater.on("error", (err: Error) => {
    logError("updater", err);
    send("update:error", String(err));
  });

  autoUpdater.checkForUpdates().catch((e: unknown) => logError("updater checkForUpdates", e));
}

export async function checkForUpdatesManual(): Promise<void> {
  if (!app.isPackaged) return;
  await autoUpdater.checkForUpdates();
}

export function quitAndInstall(): void {
  if (!app.isPackaged) return;
  autoUpdater.quitAndInstall(false, true);
}
