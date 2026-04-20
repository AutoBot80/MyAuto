/** Types for `window.electronAPI` exposed by `electron/src/preload/index.ts`. */

export interface SidecarJobResult {
  success: boolean;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  timedOut?: boolean;
  parsed?: unknown;
  error?: string;
}

export interface ElectronAPI {
  sidecar: {
    runJob: (payload: Record<string, unknown>) => Promise<SidecarJobResult>;
  };
  print: {
    getPrinters: () => Promise<Array<{ name: string; displayName?: string; description?: string }>>;
    printHtml: (opts: { html: string; deviceName?: string; silent?: boolean; copies?: number }) => Promise<{
      ok: boolean;
      fallback?: boolean;
      error?: string;
    }>;
    testPrint: (deviceName?: string) => Promise<{ ok: boolean; error?: string }>;
    printPdfsFromUrls: (
      items: { presigned_url: string; filename?: string; kind?: string }[],
      deviceName?: string
    ) => Promise<{ ok: boolean; printed: number; error?: string }>;
  };
  file: {
    list: (p: string) => Promise<string[]>;
    move: (from: string, to: string) => Promise<void>;
    exists: (p: string) => Promise<boolean>;
    openFolder: (p: string) => Promise<void>;
    selectFolder: () => Promise<string | null>;
  };
  updater: {
    install: () => Promise<void>;
    check: () => Promise<void>;
    onAvailable: (cb: (info: unknown) => void) => void;
    onDownloaded: (cb: (info: unknown) => void) => void;
    onError: (cb: (msg: string) => void) => void;
  };
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}

export function isElectron(): boolean {
  return typeof window !== "undefined" && !!window.electronAPI;
}
