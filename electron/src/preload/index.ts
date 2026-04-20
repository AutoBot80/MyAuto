import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  sidecar: {
    runJob: (payload: unknown) => ipcRenderer.invoke("sidecar:runJob", payload),
  },
  print: {
    getPrinters: () => ipcRenderer.invoke("print:getPrinters"),
    printHtml: (opts: { html: string; deviceName?: string; silent?: boolean; copies?: number }) =>
      ipcRenderer.invoke("print:html", opts),
    testPrint: (deviceName?: string) => ipcRenderer.invoke("print:test", deviceName),
    printPdfsFromUrls: (items: { presigned_url: string; filename?: string; kind?: string }[], deviceName?: string) =>
      ipcRenderer.invoke("print:pdfsFromUrls", items, deviceName),
  },
  file: {
    list: (p: string) => ipcRenderer.invoke("file:list", p),
    move: (from: string, to: string) => ipcRenderer.invoke("file:move", from, to),
    exists: (p: string) => ipcRenderer.invoke("file:exists", p),
    openFolder: (p: string) => ipcRenderer.invoke("file:openFolder", p),
    selectFolder: () => ipcRenderer.invoke("file:selectFolder"),
  },
  updater: {
    install: () => ipcRenderer.invoke("updater:install"),
    check: () => ipcRenderer.invoke("updater:check"),
    onAvailable: (cb: (info: unknown) => void) => {
      ipcRenderer.on("update:available", (_e: unknown, info: unknown) => cb(info));
    },
    onDownloaded: (cb: (info: unknown) => void) => {
      ipcRenderer.on("update:downloaded", (_e: unknown, info: unknown) => cb(info));
    },
    onError: (cb: (msg: string) => void) => {
      ipcRenderer.on("update:error", (_e: unknown, msg: string) => cb(msg));
    },
  },
});
