declare module "electron-updater" {
  export interface UpdateInfo {
    version?: string;
    [key: string]: unknown;
  }

  export interface AppUpdater {
    autoDownload: boolean;
    autoInstallOnAppQuit: boolean;
    checkForUpdates(): Promise<unknown>;
    downloadUpdate(): Promise<string[]>;
    quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void;
    on(event: "checking-for-update", cb: () => void): AppUpdater;
    on(event: "update-available", cb: (info: UpdateInfo) => void): AppUpdater;
    on(event: "update-not-available", cb: () => void): AppUpdater;
    on(event: "update-downloaded", cb: (info: UpdateInfo) => void): AppUpdater;
    on(event: "error", cb: (err: Error) => void): AppUpdater;
  }

  export const autoUpdater: AppUpdater;
}
