import { BrowserWindow, ipcMain, type IpcMainInvokeEvent } from "electron";
import * as fileOps from "./file-ops";
import { copyUploadScanArtifacts, renameSaleSubfolders } from "./upload-scan-copies";
import { copyChallanScanArtifacts } from "./challan-scan-copies";
import { logError, logInfo } from "./logger";
import { getSiteUrlsFromEnv } from "./paths";
import * as printer from "./printer";
import { releaseBrowsersHardReset, runSidecarJob, type SidecarJobPayload } from "./sidecar";
import { checkForUpdatesManual, quitAndInstall, setupAutoUpdater } from "./updater";

export function registerIpc(mainWindow: BrowserWindow): void {
  setupAutoUpdater((channel, payload) => {
    mainWindow.webContents.send(channel, payload);
  });

  ipcMain.handle("sidecar:runJob", async (_evt: IpcMainInvokeEvent, payload: SidecarJobPayload) => {
    const jobType = String(payload?.type ?? payload?.job ?? "unknown");
    logInfo(`sidecar:runJob start type=${jobType}`);
    try {
      const result = await runSidecarJob(payload);
      logInfo(`sidecar:runJob end type=${jobType} success=${result.success}`);
      return result;
    } catch (e) {
      logError("sidecar:runJob", e);
      return {
        success: false,
        stdout: "",
        stderr: String(e),
        exitCode: null,
        error: String(e),
      };
    }
  });

  ipcMain.handle("sidecar:releaseBrowsers", async () => {
    try {
      return await releaseBrowsersHardReset();
    } catch (e) {
      logError("sidecar:releaseBrowsers", e);
      return {
        success: false,
        stdout: "",
        stderr: String(e),
        exitCode: null,
        error: String(e),
      };
    }
  });

  ipcMain.handle("print:getPrinters", async () => printer.getPrinters());
  ipcMain.handle("print:html", async (_evt: IpcMainInvokeEvent, opts: printer.PrintOptions) =>
    printer.printHtml(opts)
  );
  ipcMain.handle("print:test", async (_evt: IpcMainInvokeEvent, deviceName?: string) =>
    printer.testPrint(deviceName)
  );
  ipcMain.handle(
    "print:pdfsFromUrls",
    async (
      _evt: IpcMainInvokeEvent,
      items: printer.PresignedPrintItem[],
      options?: printer.PdfPrintOptions | string
    ) => {
      const opts: printer.PdfPrintOptions =
        typeof options === "string" ? { deviceName: options } : options ?? {};
      if (opts.background === false) {
        return printer.printPdfsFromPresignedUrls(items, opts);
      }
      void printer.printPdfsFromPresignedUrls(items, opts).catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        logError(`print:pdfsFromUrls background failed: ${msg}`);
      });
      return { ok: true, printed: 0, queued: items.length };
    }
  );

  ipcMain.handle("file:list", (_evt: IpcMainInvokeEvent, p: string) => fileOps.listFiles(p));
  ipcMain.handle("file:move", (_evt: IpcMainInvokeEvent, from: string, to: string) => {
    fileOps.moveFile(from, to);
  });
  ipcMain.handle("file:exists", (_evt: IpcMainInvokeEvent, p: string) => fileOps.fileExists(p));
  ipcMain.handle("file:openFolder", (_evt: IpcMainInvokeEvent, p: string) => fileOps.openFolder(p));
  ipcMain.handle("file:selectFolder", async () => fileOps.selectFolder());

  ipcMain.handle(
    "file:copyUploadScanArtifacts",
    async (
      _evt: IpcMainInvokeEvent,
      payload: { dealerId: number; subfolder: string; items: { sourcePath: string; destFileName: string }[] }
    ) => {
      try {
        return await copyUploadScanArtifacts(payload);
      } catch (e) {
        logError("file:copyUploadScanArtifacts", e);
        return { ok: false as const, message: String(e) };
      }
    }
  );

  ipcMain.handle(
    "file:copyChallanScanArtifacts",
    async (
      _evt: IpcMainInvokeEvent,
      payload: { dealerId: number; artifactLeaf: string; items: { sourcePath: string; destFileName: string }[] }
    ) => {
      try {
        return await copyChallanScanArtifacts(payload);
      } catch (e) {
        logError("file:copyChallanScanArtifacts", e);
        return { ok: false as const, message: String(e) };
      }
    }
  );

  ipcMain.handle(
    "file:renameSaleSubfolders",
    async (
      _evt: IpcMainInvokeEvent,
      payload: { dealerId: number; oldSubfolder: string; newSubfolder: string }
    ) => {
      try {
        return renameSaleSubfolders(payload);
      } catch (e) {
        logError("file:renameSaleSubfolders", e);
        return { ok: false as const, message: String(e) };
      }
    }
  );

  ipcMain.handle("config:siteUrls", () => getSiteUrlsFromEnv());

  ipcMain.handle("updater:install", () => {
    logInfo("updater: user requested install and restart");
    quitAndInstall();
  });

  ipcMain.handle("updater:check", async () => {
    await checkForUpdatesManual();
  });
}
