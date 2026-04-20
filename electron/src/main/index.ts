import { app, BrowserWindow } from "electron";
import fs from "fs";
import path from "path";
import { logError, logInfo } from "./logger";
import { getAppIconPath, getRepoRootFromMain } from "./paths";
import { registerIpc } from "./ipc-handlers";
import { killAllSidecarJobs } from "./sidecar";

let mainWindow: BrowserWindow | null = null;

function loadUi(win: BrowserWindow): void {
  const packaged = app.isPackaged;
  if (packaged) {
    const indexHtml = path.join(process.resourcesPath, "client-dist", "index.html");
    if (!fs.existsSync(indexHtml)) {
      logError(`Missing UI bundle: ${indexHtml}`);
      return;
    }
    win.loadFile(indexHtml).catch((e: unknown) => logError("loadFile", e));
    return;
  }

  // Do not load client/dist via file:// — Vite's default build uses absolute "/assets/..." paths,
  // which break under the file protocol (blank window). Use the Vite dev server, or set base in Vite for packaged file:// loads.
  const useFileDist = process.env.SAATHI_USE_FILE_DIST === "1";
  const distIndex = path.join(getRepoRootFromMain(), "client", "dist", "index.html");
  if (useFileDist && fs.existsSync(distIndex)) {
    win.loadFile(distIndex).catch((e: unknown) => logError("loadFile dist", e));
    if (process.env.SAATHI_OPEN_DEVTOOLS === "1") {
      win.webContents.openDevTools({ mode: "detach" });
    }
    return;
  }

  const devUrl = process.env.SAATHI_VITE_URL || "http://localhost:5173";
  win.loadURL(devUrl).catch((e: unknown) => logError("loadURL dev", e));
  if (process.env.SAATHI_OPEN_DEVTOOLS === "1") {
    win.webContents.openDevTools({ mode: "detach" });
  }
}

function createWindow(): void {
  const iconPath = getAppIconPath();
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    show: false,
    ...(fs.existsSync(iconPath) ? { icon: iconPath } : {}),
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.maximize();
  mainWindow.show();

  loadUi(mainWindow);
  registerIpc(mainWindow);

  // The client Add Sales page uses a `beforeunload` handler to warn about
  // unsaved work. In Electron the native "Leave site?" dialog doesn't appear
  // by default, which blocks the window from closing. Show a dialog instead.
  mainWindow.webContents.on("will-prevent-unload", (event) => {
    const { dialog } = require("electron") as typeof import("electron");
    const choice = dialog.showMessageBoxSync(mainWindow!, {
      type: "question",
      buttons: ["Leave", "Stay"],
      defaultId: 1,
      title: "Close application?",
      message: "Customer processing is not complete. Close anyway?",
    });
    if (choice === 0) {
      event.preventDefault();
    }
  });
}

app.whenReady().then(() => {
  logInfo("application started");
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("will-quit", () => {
  logInfo("application will quit");
  killAllSidecarJobs();
});

process.on("uncaughtException", (e: unknown) => logError("uncaughtException", e));
process.on("unhandledRejection", (e: unknown) => logError("unhandledRejection", e));
