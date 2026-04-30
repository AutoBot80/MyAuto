import { app } from "electron";
import fs from "fs";
import path from "path";
import { autoUpdater } from "electron-updater";
import { logError, logInfo, logWarn } from "./logger";

/** Written when an update finishes downloading; consumed on the next process start to run the installer. */
const PENDING_UPDATE_ON_LAUNCH = "pending-update-on-next-launch.json";

let suppressAutoDownloadFromUpdateAvailable = false;
/** True while `downloadUpdate()` is running for a deferred install-on-next-launch (skip re-writing the marker). */
let applyOnLaunchDownload = false;

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

function pendingMarkerPath(): string {
  return path.join(app.getPath("userData"), PENDING_UPDATE_ON_LAUNCH);
}

function readPendingUpdateVersion(): string | null {
  try {
    const p = pendingMarkerPath();
    if (!fs.existsSync(p)) return null;
    const j = JSON.parse(fs.readFileSync(p, "utf8")) as { version?: string };
    const v = j.version?.trim();
    return v || null;
  } catch {
    return null;
  }
}

function writePendingUpdateMarker(version: string): void {
  try {
    fs.writeFileSync(pendingMarkerPath(), JSON.stringify({ version }), "utf8");
  } catch (e) {
    logWarn(`updater: could not write pending marker: ${e}`);
  }
}

function clearPendingUpdateMarker(): void {
  try {
    const p = pendingMarkerPath();
    if (fs.existsSync(p)) fs.unlinkSync(p);
  } catch {
    // ignore
  }
}

/**
 * If a previous session finished downloading an update, install it now (before normal UI check).
 * Returns true when `quitAndInstall` was invoked (process will exit for the NSIS installer).
 */
async function tryInstallPendingUpdateOnNextLaunch(): Promise<boolean> {
  const pending = readPendingUpdateVersion();
  if (!pending) return false;

  logInfo(`updater: found pending update marker (${pending}), reconciling with feed…`);
  suppressAutoDownloadFromUpdateAvailable = true;
  try {
    const result = (await autoUpdater.checkForUpdates()) as {
      isUpdateAvailable?: boolean;
      updateInfo?: { version?: string };
    } | null;
    const latest = result?.updateInfo?.version?.trim();
    if (!result?.isUpdateAvailable || !latest) {
      logInfo("updater: feed reports no update — clearing stale pending marker");
      clearPendingUpdateMarker();
      return false;
    }
    if (latest !== pending) {
      logInfo(
        `updater: feed latest (${latest}) ≠ pending (${pending}) — clearing marker; user will get a fresh download if needed`
      );
      clearPendingUpdateMarker();
      return false;
    }

    applyOnLaunchDownload = true;
    try {
      await autoUpdater.downloadUpdate();
    } finally {
      applyOnLaunchDownload = false;
    }
    logInfo(`updater: applying pending v${pending} on launch (installer will run)`);
    clearPendingUpdateMarker();
    quitAndInstall();
    return true;
  } catch (e) {
    logError("updater tryInstallPendingUpdateOnNextLaunch", e);
    return false;
  } finally {
    suppressAutoDownloadFromUpdateAvailable = false;
  }
}

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
  // Defer download until after `update-available` so the renderer can paint; run `downloadUpdate()` in the background.
  // (Installer still comes from the configured GitHub feed — routing bytes via your API needs a generic feed + proxy.)
  autoUpdater.autoDownload = false;
  // Install on next **process start** (see tryInstallPendingUpdateOnNextLaunch), not on ordinary quit.
  autoUpdater.autoInstallOnAppQuit = false;

  let lastLoggedDownloadPercent = -1;
  autoUpdater.on("checking-for-update", () => logInfo("updater: checking for update…"));
  autoUpdater.on("update-available", (info: import("electron-updater").UpdateInfo) => {
    if (suppressAutoDownloadFromUpdateAvailable) return;
    lastLoggedDownloadPercent = -1;
    logInfo(`updater: update available → v${info.version}`);
    send("update:available", info);
    void autoUpdater
      .downloadUpdate()
      .catch((e: unknown) => logError("updater downloadUpdate", e));
  });
  autoUpdater.on("update-not-available", () => {
    logInfo("updater: already on latest version");
  });
  (autoUpdater as any).on("download-progress", (progress: { percent: number }) => {
    const p = Math.round(progress.percent);
    if (p >= lastLoggedDownloadPercent + 10 || p === 100) {
      lastLoggedDownloadPercent = p;
      logInfo(`updater: download ${p}%`);
    }
  });
  autoUpdater.on("update-downloaded", (info: import("electron-updater").UpdateInfo) => {
    const v = info.version ?? "";
    logInfo(`updater: v${v} downloaded — will install on next app start (or Restart now)`);
    if (v && !applyOnLaunchDownload) writePendingUpdateMarker(v);
    if (!applyOnLaunchDownload) send("update:downloaded", info);
  });
  autoUpdater.on("error", (err: Error) => {
    logError("updater", err);
    send("update:error", String(err));
  });

  void (async () => {
    const didInstall = await tryInstallPendingUpdateOnNextLaunch();
    if (didInstall) return;
    await autoUpdater.checkForUpdates().catch((e: unknown) => logError("updater checkForUpdates", e));
  })();
}

export async function checkForUpdatesManual(): Promise<void> {
  if (!app.isPackaged) return;
  await autoUpdater.checkForUpdates();
}

export function quitAndInstall(): void {
  if (!app.isPackaged) return;
  clearPendingUpdateMarker();
  autoUpdater.quitAndInstall(false, true);
}
