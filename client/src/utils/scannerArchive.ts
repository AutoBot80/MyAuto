/**
 * Optional workflow: user grants access to their local "scanner" folder once (parent of
 * `landing` and `processed`). After consolidated OCR succeeds, the chosen file is moved
 * from `landing` to `processed` via the File System Access API (Chromium).
 */

const IDB_NAME = "dealer-saathi-scanner-fs";
const IDB_VERSION = 1;
const STORE = "handles";
const SCANNER_ROOT_KEY = "scanner-root";

export const SCANNER_SUB = {
  landing: "landing",
  processed: "processed",
} as const;

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, IDB_VERSION);
    req.onerror = () => reject(req.error ?? new Error("indexedDB open failed"));
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
  });
}

async function idbGet<T>(key: string): Promise<T | undefined> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const store = tx.objectStore(STORE);
    const g = store.get(key);
    g.onerror = () => reject(g.error);
    g.onsuccess = () => resolve(g.result as T | undefined);
    tx.oncomplete = () => db.close();
  });
}

async function idbSet(key: string, value: unknown): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    const p = store.put(value, key);
    p.onerror = () => reject(p.error);
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
  });
}

export function fsAccessSupported(): boolean {
  return typeof window !== "undefined" && "showDirectoryPicker" in window && "showOpenFilePicker" in window;
}

export async function loadScannerRootHandle(): Promise<FileSystemDirectoryHandle | null> {
  if (!fsAccessSupported()) return null;
  const h = await idbGet<FileSystemDirectoryHandle>(SCANNER_ROOT_KEY);
  return h ?? null;
}

export async function saveScannerRootHandle(handle: FileSystemDirectoryHandle): Promise<void> {
  await idbSet(SCANNER_ROOT_KEY, handle);
}

export async function clearScannerRootHandle(): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    const d = store.delete(SCANNER_ROOT_KEY);
    d.onerror = () => reject(d.error);
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
  });
}

export async function pickScannerRootDirectory(): Promise<FileSystemDirectoryHandle | null> {
  if (!fsAccessSupported()) return null;
  try {
    return await window.showDirectoryPicker({ mode: "readwrite" });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") return null;
    throw e;
  }
}

export async function getLandingDirectory(
  scannerRoot: FileSystemDirectoryHandle
): Promise<FileSystemDirectoryHandle> {
  return scannerRoot.getDirectoryHandle(SCANNER_SUB.landing, { create: false });
}

export async function getProcessedDirectory(
  scannerRoot: FileSystemDirectoryHandle
): Promise<FileSystemDirectoryHandle> {
  return scannerRoot.getDirectoryHandle(SCANNER_SUB.processed, { create: true });
}

export interface ConsolidatedPickFromLanding {
  file: File;
  fileHandle: FileSystemFileHandle;
}

/** Passed to upload after picking via File System Access API under `scanner/landing`. */
export interface ConsolidatedFsArchiveContext {
  fileHandle: FileSystemFileHandle;
  scannerRoot: FileSystemDirectoryHandle;
}

/** Opens the OS file picker starting in `scanner/landing` (requires prior access to `scannerRoot`). */
export async function pickConsolidatedPdfFromLanding(
  scannerRoot: FileSystemDirectoryHandle
): Promise<ConsolidatedPickFromLanding | null> {
  if (!fsAccessSupported()) return null;
  const landing = await getLandingDirectory(scannerRoot);
  try {
    const [fileHandle] = await window.showOpenFilePicker({
      startIn: landing,
      types: [{ description: "PDF", accept: { "application/pdf": [".pdf"] } }],
      multiple: false,
    });
    const file = await fileHandle.getFile();
    return { file, fileHandle };
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") return null;
    throw e;
  }
}

/** Move the file from `landing` into `processed` (same basename). */
export async function moveConsolidatedToProcessed(
  fileHandle: FileSystemFileHandle,
  scannerRoot: FileSystemDirectoryHandle
): Promise<void> {
  const processed = await getProcessedDirectory(scannerRoot);
  await fileHandle.move(processed);
}
