import { app } from "electron";
import fs from "fs";
import path from "path";
import { autoUpdater } from "electron-updater";
import { logError, logInfo, logWarn } from "./logger";

function resolveGhToken(): string {
  const env = (process.env.GH_TOKEN || "").trim();
  if (env) return env;
  try {
    const p = app.isPackaged
      ? path.join(process.resourcesPath, "update-token.json")
      : path.join(__dirname, "..", "..", "resources", "update-token.json");
    logInfo(`updater: reading token from ${p}`);
    const data = JSON.parse(fs.readFileSync(p, "utf-8"));
    return (data.token || "").trim();
  } catch (e) {
    logWarn(`updater: failed to read update-token.json: ${e}`);
    return "";
  }
}

export type UpdateSender = (channel: string, payload?: unknown) => void;

export function setupAutoUpdater(send: UpdateSender): void {
  const version = app.getVersion();
  logInfo(`updater: app version ${version}`);

  if (!app.isPackaged) {
    logInfo("updater: skipped (development build)");
    return;
  }
  const token = resolveGhToken();
  if (!token) {
    logWarn(
      "updater: no GH_TOKEN found — auto-update disabled. " +
      "Ensure update-token.json contains a valid token or set GH_TOKEN env var."
    );
    return;
  }
  logInfo("updater: token found, configuring auto-updater");
  (autoUpdater as any).requestHeaders = { Authorization: `token ${token}` };
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on("checking-for-update", () => logInfo("updater: checking for update…"));
  autoUpdater.on("update-available", (info: import("electron-updater").UpdateInfo) => {
    logInfo(`updater: update available → v${info.version}`);
    send("update:available", info);
  });
  autoUpdater.on("update-not-available", () => {
    logInfo("updater: already on latest version");
  });
  (autoUpdater as any).on("download-progress", (progress: { percent: number }) => {
    logInfo(`updater: download ${Math.round(progress.percent)}%`);
  });
  autoUpdater.on("update-downloaded", (info: import("electron-updater").UpdateInfo) => {
    logInfo(`updater: v${info.version} downloaded — will install on quit or manual restart`);
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
