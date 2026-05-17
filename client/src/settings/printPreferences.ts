const SILENT_PRINT_STORAGE_KEY = "dealer_saathi_silent_print";

/** Default ON: print PDFs to the default printer without the system dialog (Electron only). */
export function getSilentPrintEnabled(): boolean {
  try {
    const raw = localStorage.getItem(SILENT_PRINT_STORAGE_KEY);
    if (raw === null) return true;
    return raw === "1" || raw.toLowerCase() === "true";
  } catch {
    return true;
  }
}

export function setSilentPrintEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(SILENT_PRINT_STORAGE_KEY, enabled ? "1" : "0");
  } catch {
    /* ignore */
  }
}
