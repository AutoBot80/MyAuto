/**
 * Chromium File System Access API — `move`, `showOpenFilePicker`, and `showDirectoryPicker`
 * are not in every TypeScript DOM lib snapshot.
 */

interface FileSystemFileHandle {
  move(destination: FileSystemDirectoryHandle, name?: string): Promise<void>;
}

interface OpenFilePickerOptions {
  multiple?: boolean;
  excludeAcceptAllOption?: boolean;
  startIn?: FileSystemHandle;
  types?: FilePickerAcceptType[];
}

interface FilePickerAcceptType {
  description?: string;
  accept: Record<string, string | string[]>;
}

interface DirectoryPickerOptions {
  id?: string;
  mode?: "read" | "readwrite";
  startIn?: FileSystemHandle | "desktop" | "documents" | "downloads";
}

interface Window {
  showOpenFilePicker(options?: OpenFilePickerOptions): Promise<FileSystemFileHandle[]>;
  showDirectoryPicker(options?: DirectoryPickerOptions): Promise<FileSystemDirectoryHandle>;
}
