import type { ReactNode } from "react";
import { Header } from "./Header";
import type { Page } from "../types";

const PAGE_LABELS: Record<Page, string> = {
  "add-sales": "Add Sales",
  "customer-details": "Customer Details",
  "rto-status": "RTO Queue",
  "ai-reader-queue": "AI Reader Queue",
};

interface AppLayoutV2Props {
  headerTitle: string;
  headerSubtitle?: string | null;
  headerRight: ReactNode;
  currentPage: Page;
  onNavigate: (page: Page) => void;
  children: ReactNode;
}

export function AppLayoutV2({
  headerTitle,
  headerSubtitle,
  headerRight,
  currentPage,
  onNavigate,
  children,
}: AppLayoutV2Props) {
  return (
    <div className="app-wrap app-wrap-v2">
      <div className="app-box">
        <Header
          title={headerTitle}
          subtitle={headerSubtitle}
          rightSlot={headerRight}
        />
        <nav className="app-tabs-v2" role="tablist">
          {(Object.keys(PAGE_LABELS) as Page[]).map((page) => (
            <button
              key={page}
              type="button"
              role="tab"
              aria-selected={currentPage === page}
              className={`app-tab-v2 ${currentPage === page ? "active" : ""}`}
              onClick={() => onNavigate(page)}
            >
              {PAGE_LABELS[page]}
            </button>
          ))}
        </nav>
        <main className="app-main app-main-v2">{children}</main>
      </div>
    </div>
  );
}
