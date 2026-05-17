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

async function resolveDefaultPrinterName(): Promise<string | undefined> {
  const printers = await getPrinters();
  const def = printers.find((p) => p.isDefault) ?? printers[0];
  return def?.name?.trim() || undefined;
}

/**
 * Print PDF. When ``silent`` is true, sends to the default printer without a system dialog.
 * Uses explicit ``pageSize`` so Chromium does not send an empty layout to the driver.
 */
async function printPdfContents(
  wc: WebContents,
  deviceName?: string,
  silent = false
): Promise<void> {
  let device = deviceName?.trim() || undefined;
  if (silent && !device) {
    device = await resolveDefaultPrinterName();
  }
  const base: WebContentsPrintOptions = {
    printBackground: true,
    pageSize: "A4",
    deviceName: device,
  };
  if (silent) {
    await printWithCallback(wc, { ...base, silent: true });
    return;
  }
  await printWithCallback(wc, { ...base, silent: false });
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

export interface PdfPrintOptions {
  silent?: boolean;
  deviceName?: string;
}

/**
 * PDF print needs a laid-out webview. For silent mode we keep a small off-screen window
 * (still ``show: true``) so Chromium assigns a content size; dialog mode uses a normal window.
 */
function createPdfPrintWindow(title: string, silent: boolean): BrowserWindow {
  return new BrowserWindow({
    show: true,
    width: silent ? 800 : 900,
    height: silent ? 600 : 1100,
    x: silent ? -32000 : undefined,
    y: silent ? -32000 : undefined,
    center: !silent,
    title: title || "Print",
    autoHideMenuBar: true,
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
  options?: PdfPrintOptions | string
): Promise<{ ok: boolean; printed: number; error?: string }> {
  const opts: PdfPrintOptions =
    typeof options === "string" ? { deviceName: options } : options ?? {};
  const deviceName = opts.deviceName;
  const silent = opts.silent === true;
  const { writeFile, unlink } = await import("fs/promises");
  const { join } = await import("path");
  const { tmpdir } = await import("os");

  let printed = 0;
  const total = items.length;
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const step = `${i + 1}/${total}`;
    const label =
      item.kind === "sale_certificate"
        ? "Sale Certificate"
        : item.kind === "insurance"
          ? "Insurance"
          : item.kind === "gate_pass"
            ? "Gate Pass"
            : item.kind === "cpa"
              ? "CPA"
              : item.filename ?? "Document";
    const windowTitle = `Print ${step}: ${label}`;
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
        const win = createPdfPrintWindow(windowTitle, silent);
        try {
          await win.loadFile(localPath);
          if (!silent) {
            win.focus();
            win.setAlwaysOnTop(true, "screen-saver");
          }
          await new Promise<void>((r) => setTimeout(r, silent ? 800 : 500));
          await printPdfContents(win.webContents, deviceName, silent);
          printed++;
        } finally {
          if (!silent) win.setAlwaysOnTop(false);
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

      const win = createPdfPrintWindow(windowTitle, silent);
      try {
        await win.loadFile(tmpPath);
        if (!silent) {
          win.focus();
          win.setAlwaysOnTop(true, "screen-saver");
        }
        await new Promise<void>((r) => setTimeout(r, silent ? 800 : 500));
        await printPdfContents(win.webContents, deviceName, silent);
        printed++;
      } finally {
        if (!silent) win.setAlwaysOnTop(false);
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

/**
 * Dev / smoke: print PDF(s) from ``absDir`` using the same local-path path as production
 * (``printPdfsFromPresignedUrls``). Used when ``SAATHI_PRINT_TEST_DIR`` is set in main.
 *
 * If ``SAATHI_PRINT_TEST_ONLY`` is set (e.g. ``Report`` or ``Report.pdf``), only that file is printed.
 */
export async function runPrintTestFromDir(absDir: string): Promise<{ ok: boolean; printed: number; error?: string }> {
  const { readdir } = await import("fs/promises");
  const { join } = await import("path");
  let names = (await readdir(absDir))
    .filter((n) => n.toLowerCase().endsWith(".pdf"))
    .sort();

  const onlyRaw = process.env.SAATHI_PRINT_TEST_ONLY?.trim();
  if (onlyRaw) {
    const want = onlyRaw.toLowerCase().endsWith(".pdf") ? onlyRaw : `${onlyRaw}.pdf`;
    names = names.filter((n) => n.toLowerCase() === want.toLowerCase());
    if (!names.length) {
      return {
        ok: false,
        printed: 0,
        error: `No PDF matching SAATHI_PRINT_TEST_ONLY=${onlyRaw} in ${absDir}`,
      };
    }
  }

  const items: PresignedPrintItem[] = names.map((filename) => ({
    presigned_url: join(absDir, filename),
    filename,
  }));
  if (!items.length) {
    return { ok: false, printed: 0, error: `No PDF files in ${absDir}` };
  }
  return printPdfsFromPresignedUrls(items);
}
