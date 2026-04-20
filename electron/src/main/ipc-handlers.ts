import { BrowserWindow, ipcMain, type IpcMainInvokeEvent } from "electron";
import * as fileOps from "./file-ops";
import { logError, logInfo } from "./logger";
import * as printer from "./printer";
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

  ipcMain.handle("file:list", (_evt: IpcMainInvokeEvent, p: string) => fileOps.listFiles(p));
  ipcMain.handle("file:move", (_evt: IpcMainInvokeEvent, from: string, to: string) => {
    fileOps.moveFile(from, to);
  });
  ipcMain.handle("file:exists", (_evt: IpcMainInvokeEvent, p: string) => fileOps.fileExists(p));
  ipcMain.handle("file:openFolder", (_evt: IpcMainInvokeEvent, p: string) => fileOps.openFolder(p));
  ipcMain.handle("file:selectFolder", async () => fileOps.selectFolder());

  ipcMain.handle("updater:install", () => {
    logInfo("updater: user requested install and restart");
    quitAndInstall();
  });

  ipcMain.handle("updater:check", async () => {
    await checkForUpdatesManual();
  });
}
