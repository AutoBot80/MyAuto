import { app, BrowserWindow, dialog, Menu } from "electron";
import fs from "fs";
import { tmpdir } from "os";
import path from "path";
import { logError, logInfo } from "./logger";
import { getAppIconPath, getRepoRootFromMain } from "./paths";
import { registerIpc } from "./ipc-handlers";
import { runPrintTestFromDir } from "./printer";
import { killAllSidecarJobs, runSidecarJob } from "./sidecar";

/** Print-smoke / SAATHI_PRINT_TEST_DIR: use a dedicated userData + disk cache under %TEMP% to avoid Chromium cache lock / Access denied (0x5) on the default profile. */
const _printTestDirEnv = process.env.SAATHI_PRINT_TEST_DIR?.trim();
if (_printTestDirEnv) {
  const smokeRoot = path.join(tmpdir(), "saathi-electron-print-smoke");
  app.setPath("userData", smokeRoot);
  app.commandLine.appendSwitch("disk-cache-dir", path.join(smokeRoot, "chromium-disk-cache"));
}

let mainWindow: BrowserWindow | null = null;

/** After cooperative CDP teardown we allow the real quit to proceed (avoid ``before-quit`` loops). */
let quitAfterSidecarTeardown = false;

/** Packaged apps do not get Chromium’s default shortcuts; wire DevTools explicitly. */
function installDevToolsShortcuts(win: BrowserWindow): void {
  win.webContents.on("before-input-event", (_event, input) => {
    if (input.type !== "keyDown") return;
    const k = input.key;
    const isF12 = k === "F12";
    const isCtrlShiftI =
      (input.control || input.meta) && input.shift && (k === "I" || k === "i");
    if (isF12 || isCtrlShiftI) {
      win.webContents.toggleDevTools();
    }
  });
}

function setApplicationMenu(): void {
  const viewSubmenu: Electron.MenuItemConstructorOptions[] = [
    { role: "reload" },
    { role: "forceReload" },
    { type: "separator" },
    {
      label: "Toggle Developer Tools",
      accelerator: process.platform === "darwin" ? "Alt+Command+I" : "F12",
      // Electron 34+ types `focusedWindow` as BaseWindow (no webContents); we only have one BrowserWindow.
      click: () => {
        mainWindow?.webContents.toggleDevTools();
      },
    },
    { type: "separator" },
    { role: "resetZoom" },
    { role: "zoomIn" },
    { role: "zoomOut" },
  ];
  const template: Electron.MenuItemConstructorOptions[] =
    process.platform === "darwin"
      ? [{ role: "appMenu" }, { label: "View", submenu: viewSubmenu }]
      : [
          {
            label: "File",
            submenu: [{ role: "quit" }],
          },
          { label: "View", submenu: viewSubmenu },
        ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

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
    autoHideMenuBar: false,
    ...(fs.existsSync(iconPath) ? { icon: iconPath } : {}),
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  setApplicationMenu();
  installDevToolsShortcuts(mainWindow);

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

app.whenReady().then(async () => {
  const printTestDir = process.env.SAATHI_PRINT_TEST_DIR?.trim();
  if (printTestDir) {
    const only = process.env.SAATHI_PRINT_TEST_ONLY?.trim();
    logInfo(
      `SAATHI_PRINT_TEST_DIR smoke print: ${printTestDir}${only ? ` (only: ${only})` : ""}`
    );
    const r = await runPrintTestFromDir(printTestDir);
    if (r.ok) {
      logInfo(`print test ok: printed=${r.printed}`);
      await dialog.showMessageBox({
        type: "info",
        title: "Print test",
        message: `Printed ${r.printed} PDF file(s).\n\nFolder:\n${printTestDir}`,
      });
    } else {
      logError(`print test failed: ${r.error ?? "unknown"}`);
      await dialog.showMessageBox({
        type: "error",
        title: "Print test failed",
        message: r.error ?? "Unknown error",
      });
    }
    app.quit();
    return;
  }

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

app.on("before-quit", async (e) => {
  if (quitAfterSidecarTeardown) {
    return;
  }
  e.preventDefault();
  quitAfterSidecarTeardown = true;
  try {
    const result = await runSidecarJob({
      type: "teardown_local_browsers",
      timeoutMs: 15_000,
    });
    if (!result.success) {
      logError(`teardown_local_browsers: ${result.error ?? "failed"}`);
    } else {
      logInfo("teardown_local_browsers completed");
    }
  } catch (err) {
    logError("teardown_local_browsers", err);
  }
  killAllSidecarJobs();
  app.quit();
});

app.on("will-quit", () => {
  logInfo("application will quit");
  killAllSidecarJobs();
});

process.on("uncaughtException", (e: unknown) => logError("uncaughtException", e));
process.on("unhandledRejection", (e: unknown) => logError("unhandledRejection", e));
