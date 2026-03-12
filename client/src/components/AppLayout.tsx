import type { ReactNode } from "react";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { HomeIcon } from "./HomeIcon";
import type { Page } from "../types";

interface AppLayoutProps {
  headerTitle: string;
  headerRight: ReactNode;
  currentPage: Page;
  onNavigate: (page: Page) => void;
  onGoHome?: () => void;
  children: ReactNode;
}

export function AppLayout({
  headerTitle,
  headerRight,
  currentPage,
  onNavigate,
  onGoHome,
  children,
}: AppLayoutProps) {
  const headerLeft =
    onGoHome && (
      <button
        type="button"
        className="app-topbar-home"
        onClick={onGoHome}
        title="Home"
        aria-label="Home"
      >
        <HomeIcon />
      </button>
    );

  return (
    <div className="app-wrap">
      <div className="app-box">
        <Header
          title={headerTitle}
          leftSlot={headerLeft}
          rightSlot={headerRight}
        />
        <div className="app-body">
          <Sidebar
            currentPage={currentPage}
            onNavigate={onNavigate}
          />
          <main className="app-main">{children}</main>
        </div>
      </div>
    </div>
  );
}
