import { existsSync } from "node:fs";
import { readFile, writeFile, unlink } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { app, BrowserWindow, type WebContents, type WebContentsPrintOptions } from "electron";
import { logError, logInfo } from "./logger";

function pdfPrintResourcesDir(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "pdf-print");
  }
  return path.join(__dirname, "..", "..", "resources", "pdf-print");
}

function pdfJsBuildDir(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "pdfjs");
  }
  return path.join(__dirname, "..", "..", "node_modules", "pdfjs-dist", "build");
}

async function buildShellDocumentUrl(pdfAbsPath: string): Promise<{ shellUrl: string; cleanup: () => Promise<void> }> {
  const templatePath = path.join(pdfPrintResourcesDir(), "print-document.html");
  let template = await readFile(templatePath, "utf8");
  const moduleUrl = pathToFileURL(path.join(pdfJsBuildDir(), "pdf.min.mjs")).href;
  const workerUrl = pathToFileURL(path.join(pdfJsBuildDir(), "pdf.worker.min.mjs")).href;
  template = template.replace(/__PDFJS_MODULE__/g, moduleUrl).replace(/__PDFJS_WORKER__/g, workerUrl);
  const tmpShell = path.join(
    app.getPath("temp"),
    `saathi-pdf-shell-${process.pid}-${Date.now()}.html`
  );
  await writeFile(tmpShell, template, "utf8");
  const pdfUrl = pathToFileURL(pdfAbsPath).href;
  const shellUrl = `${pathToFileURL(tmpShell).href}?src=${encodeURIComponent(pdfUrl)}`;
  return {
    shellUrl,
    cleanup: async () => {
      await unlink(tmpShell).catch(() => {});
    },
  };
}

function printWithCallback(wc: WebContents, options: WebContentsPrintOptions): Promise<void> {
  return new Promise((resolve, reject) => {
    wc.print(options, (success: boolean, failureReason: string) => {
      if (success) resolve();
      else reject(new Error(failureReason || "print failed"));
    });
  });
}

async function waitForPdfJsReady(wc: WebContents, timeoutMs: number): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const ready = await wc.executeJavaScript("Boolean(window.__PDF_PRINT_READY__)");
    if (ready) return;
    await new Promise<void>((r) => setTimeout(r, 120));
  }
  throw new Error("PDF render timed out before print");
}

async function printPdfViaPdfJs(
  pdfAbsPath: string,
  deviceName: string | undefined,
  silent: boolean
): Promise<void> {
  const { shellUrl, cleanup } = await buildShellDocumentUrl(pdfAbsPath);
  const win = new BrowserWindow({
    show: false,
    width: 794,
    height: 1123,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: false,
    },
  });
  try {
    await win.loadURL(shellUrl);
    await waitForPdfJsReady(win.webContents, 120_000);
    const pageCount = await win.webContents.executeJavaScript(
      "document.querySelectorAll('.page').length"
    );
    logInfo(`pdf.js print: ${path.basename(pdfAbsPath)} pages=${pageCount}`);
    await printWithCallback(win.webContents, {
      silent,
      printBackground: true,
      deviceName: deviceName?.trim() || undefined,
      pageSize: "A4",
      margins: { marginType: "none" },
    });
  } finally {
    win.destroy();
    await cleanup();
  }
}

async function printPdfViaWindows(
  pdfAbsPath: string,
  deviceName: string | undefined,
  silent: boolean
): Promise<void> {
  const { print } = await import("pdf-to-printer");
  const options: { printer?: string; printDialog?: boolean; silent?: boolean; paperSize?: string } = {
    printDialog: !silent,
    silent,
    paperSize: "A4",
  };
  const dev = deviceName?.trim();
  if (dev) options.printer = dev;
  await print(pdfAbsPath, options);
  logInfo(`pdf-to-printer: ${path.basename(pdfAbsPath)} silent=${silent}`);
}

/**
 * Print a PDF file (all pages, no Chromium PDF viewer chrome).
 * Windows: SumatraPDF via ``pdf-to-printer``; fallback PDF.js. Other OS: PDF.js only.
 */
export async function printPdfFile(
  pdfAbsPath: string,
  deviceName: string | undefined,
  silent: boolean
): Promise<void> {
  const abs = path.resolve(pdfAbsPath);
  if (!existsSync(abs)) {
    throw new Error(`PDF not found: ${abs}`);
  }

  if (process.platform === "win32") {
    try {
      await printPdfViaWindows(abs, deviceName, silent);
      return;
    } catch (err) {
      logError("pdf-to-printer failed; falling back to PDF.js render", err);
    }
  }

  try {
    await printPdfViaPdfJs(abs, deviceName, silent);
  } catch (err) {
    if (silent && process.platform === "win32") {
      logError("pdf.js silent print failed; retrying with print dialog", err);
      await printPdfViaPdfJs(abs, deviceName, false);
      return;
    }
    throw err;
  }
}
