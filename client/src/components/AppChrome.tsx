import type { ReactNode } from "react";
import { UpdateBanner } from "./UpdateBanner";

export function AppChrome({ children }: { children: ReactNode }) {
  return (
    <div
      className="app-chrome"
      onContextMenu={(e) => e.preventDefault()}
    >
      {children}
      <UpdateBanner />
    </div>
  );
}
