import type { ReactNode } from "react";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import type { Page } from "../types";

interface AppLayoutProps {
  headerTitle: string;
  headerRight: ReactNode;
  currentPage: Page;
  onNavigate: (page: Page) => void;
  children: ReactNode;
}

export function AppLayout({
  headerTitle,
  headerRight,
  currentPage,
  onNavigate,
  children,
}: AppLayoutProps) {
  return (
    <div className="app-wrap">
      <div className="app-box">
        <Header title={headerTitle} rightSlot={headerRight} />
        <div className="app-body">
          <Sidebar currentPage={currentPage} onNavigate={onNavigate} />
          <main className="app-main">{children}</main>
        </div>
      </div>
    </div>
  );
}
