/// <reference types="vite/client" />

declare const __APP_VERSION__: string;

interface ImportMetaEnv {
  /** Optional UI hint for the local scanner root (parent of `landing` and `processed`). */
  readonly VITE_SCANNER_ROOT?: string;
}
