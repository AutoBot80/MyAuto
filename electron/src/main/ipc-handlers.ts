import { BrowserWindow, ipcMain, type IpcMainInvokeEvent } from "electron";
import * as fileOps from "./file-ops";
import { logError, logInfo } from "./logger";
import { getSiteUrlsFromEnv } from "./paths";
import * as printer from "./printer";
import { runDealerSignOverlayHeadless } from "./dealer-sign-overlay";
import { runSidecarJob, type SidecarJobPayload } from "./sidecar";
import { checkForUpdatesManual, quitAndInstall, setupAutoUpdater } from "./updater";

export function registerIpc(mainWindow: BrowserWindow): void {
  setupAutoUpdater((channel, payload) => {
    mainWindow.webContents.send(channel, payload);
  });

  ipcMain.handle("sidecar:runJob", async (_evt: IpcMainInvokeEvent, payload: SidecarJobPayload) => {
    try {
      return await runSidecarJob(payload);
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
      deviceName?: string
    ) => printer.printPdfsFromPresignedUrls(items, deviceName)
  );

  ipcMain.handle(
    "dealerSign:overlaySalePdfs",
    async (
      _evt: IpcMainInvokeEvent,
      payload: { dealerId: number; subfolder: string }
    ) => {
      const did = payload?.dealerId;
      const sub = payload?.subfolder;
      if (typeof did !== "number" || did <= 0 || typeof sub !== "string" || !sub.trim()) {
        return { ok: false as const, message: "invalid_payload" };
      }
      return runDealerSignOverlayHeadless(did, sub.trim());
    }
  );

  ipcMain.handle("file:list", (_evt: IpcMainInvokeEvent, p: string) => fileOps.listFiles(p));
  ipcMain.handle("file:move", (_evt: IpcMainInvokeEvent, from: string, to: string) => {
    fileOps.moveFile(from, to);
  });
  ipcMain.handle("file:exists", (_evt: IpcMainInvokeEvent, p: string) => fileOps.fileExists(p));
  ipcMain.handle("file:openFolder", (_evt: IpcMainInvokeEvent, p: string) => fileOps.openFolder(p));
  ipcMain.handle("file:selectFolder", async () => fileOps.selectFolder());

  ipcMain.handle("config:siteUrls", () => getSiteUrlsFromEnv());

  ipcMain.handle("updater:install", () => {
    logInfo("updater: user requested install and restart");
    quitAndInstall();
  });

  ipcMain.handle("updater:check", async () => {
    await checkForUpdatesManual();
  });
}
