/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional UI hint for the local scanner root (parent of `landing` and `processed`). */
  readonly VITE_SCANNER_ROOT?: string;
}
