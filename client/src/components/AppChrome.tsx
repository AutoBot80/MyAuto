import type { ReactNode } from "react";

export function AppChrome({ children }: { children: ReactNode }) {
  return (
    <div
      className="app-chrome app-deterrent"
      onContextMenu={(e) => e.preventDefault()}
    >
      {children}
    </div>
  );
}
