import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { BrowserWindow, type WebContents } from "electron";
import type { PrinterInfo, WebContentsPrintOptions } from "electron";
import { logError, logInfo } from "./logger";

export async function getPrinters(): Promise<PrinterInfo[]> {
  const win = new BrowserWindow({ show: false });
  try {
    return await win.webContents.getPrintersAsync();
  } finally {
    win.destroy();
  }
}

function createPrintWindow(): BrowserWindow {
  return new BrowserWindow({
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
}

function printWithCallback(wc: WebContents, options: WebContentsPrintOptions): Promise<void> {
  return new Promise((resolve, reject) => {
    wc.print(options, (success: boolean, failureReason: string) => {
      if (success) resolve();
      else reject(new Error(failureReason || "print failed"));
    });
  });
}

export interface PrintOptions {
  html: string;
  deviceName?: string;
  silent?: boolean;
  copies?: number;
}

export async function printHtml(opts: PrintOptions): Promise<{ ok: boolean; fallback?: boolean; error?: string }> {
  const win = createPrintWindow();
  const dataUrl = "data:text/html;charset=utf-8," + encodeURIComponent(opts.html);
  try {
    await win.loadURL(dataUrl);
    const silent = opts.silent !== false;
    const baseOpts: WebContentsPrintOptions = {
      silent,
      deviceName: opts.deviceName,
      copies: opts.copies ?? 1,
    };
    try {
      await printWithCallback(win.webContents, baseOpts);
      logInfo(`print: silent=${silent} ok`);
      return { ok: true };
    } catch (e) {
      logError("print silent failed, trying preview", e);
      await printWithCallback(win.webContents, {
        ...baseOpts,
        silent: false,
      });
      logInfo("print: fallback preview ok");
      return { ok: true, fallback: true };
    }
  } catch (e2) {
    logError("print failed", e2);
    return { ok: false, error: e2 instanceof Error ? e2.message : String(e2) };
  } finally {
    win.destroy();
  }
}

export async function testPrint(deviceName?: string): Promise<{ ok: boolean; error?: string }> {
  const html =
    "<html><body><p>Dealer Saathi test print</p><p>" +
    new Date().toISOString() +
    "</p></body></html>";
  const r = await printHtml({ html, deviceName, silent: true });
  if (r.ok) return { ok: true };
  const r2 = await printHtml({ html, deviceName, silent: false });
  return r2.ok ? { ok: true } : { ok: false, error: r2.error };
}

/**
 * ``presigned_url`` is an HTTPS presigned URL from the API, or an absolute local path when Create Invoice
 * ran in the sidecar (dealer PC PDFs).
 */
export interface PresignedPrintItem {
  presigned_url: string;
  filename?: string;
  kind?: string;
}

function createPdfPrintWindow(): BrowserWindow {
  return new BrowserWindow({
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      plugins: true,
    },
  });
}

/** Sidecar / Create Invoice writes PDFs on disk; same field as S3 URL for API compatibility. */
function isLocalPdfPath(s: string): boolean {
  const t = s.trim();
  if (t.startsWith("file://")) return true;
  if (/^[a-zA-Z]:[\\/]/.test(t)) return true;
  if (t.startsWith("\\\\")) return true;
  return false;
}

export async function printPdfsFromPresignedUrls(
  items: PresignedPrintItem[],
  deviceName?: string
): Promise<{ ok: boolean; printed: number; error?: string }> {
  const { writeFile, unlink } = await import("fs/promises");
  const { join } = await import("path");
  const { tmpdir } = await import("os");

  let printed = 0;
  for (const item of items) {
    let tmpPath: string | null = null;
    try {
      const useLocal = isLocalPdfPath(item.presigned_url);
      if (useLocal) {
        const localPath = item.presigned_url.trim().startsWith("file://")
          ? fileURLToPath(item.presigned_url.trim())
          : item.presigned_url.trim();
        if (!existsSync(localPath)) {
          return {
            ok: false,
            printed,
            error: `PDF not found on this PC: ${item.filename ?? localPath}`,
          };
        }
        const win = createPdfPrintWindow();
        try {
          await win.loadFile(localPath);
          try {
            await printWithCallback(win.webContents, {
              silent: true,
              deviceName,
              printBackground: true,
            });
          } catch (e) {
            logError("print pdf silent failed, trying dialog", e);
            await printWithCallback(win.webContents, {
              silent: false,
              deviceName,
              printBackground: true,
            });
          }
          printed++;
        } finally {
          win.destroy();
        }
        continue;
      }

      const res = await fetch(item.presigned_url);
      if (!res.ok) {
        return {
          ok: false,
          printed,
          error: `Download failed HTTP ${res.status} for ${item.filename ?? "pdf"}`,
        };
      }
      const buf = Buffer.from(await res.arrayBuffer());
      tmpPath = join(
        tmpdir(),
        `saathi-print-${process.pid}-${Date.now()}-${printed}-${Math.random().toString(36).slice(2)}.pdf`
      );
      await writeFile(tmpPath, buf);

      const win = createPdfPrintWindow();
      try {
        await win.loadFile(tmpPath);
        try {
          await printWithCallback(win.webContents, {
            silent: true,
            deviceName,
            printBackground: true,
          });
        } catch (e) {
          logError("print pdf silent failed, trying dialog", e);
          await printWithCallback(win.webContents, {
            silent: false,
            deviceName,
            printBackground: true,
          });
        }
        printed++;
      } finally {
        win.destroy();
      }
    } catch (e) {
      return {
        ok: false,
        printed,
        error: e instanceof Error ? e.message : String(e),
      };
    } finally {
      if (tmpPath) {
        unlink(tmpPath).catch(() => {});
      }
    }
  }
  return { ok: true, printed };
}
