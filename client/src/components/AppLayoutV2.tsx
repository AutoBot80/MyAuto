import type { ReactNode } from "react";
import { Header } from "./Header";
import type { Page } from "../types";

const PAGE_LABELS: Record<Page, string> = {
  "add-sales": "Add Sales",
  "customer-details": "View Customers",
  "rto-status": "RTO Payments Pending",
  "service-reminders": "Service Reminders",
  "contact-us": "Contact Us",
};

interface AppLayoutV2Props {
  headerTitle: string;
  headerSubtitle?: string | null;
  headerRight: ReactNode;
  currentPage: Page;
  onNavigate: (page: Page) => void;
  children: ReactNode;
  /** If set, only these pages are shown as tabs. Otherwise all pages. */
  visiblePages?: Page[];
  /** When set, show a Home link in the header that calls this. */
  onGoHome?: () => void;
}

export function AppLayoutV2({
  headerTitle,
  headerSubtitle,
  headerRight,
  currentPage,
  onNavigate,
  children,
  visiblePages,
  onGoHome,
}: AppLayoutV2Props) {
  const tabs: Page[] = visiblePages ?? (Object.keys(PAGE_LABELS) as Page[]);
  const homeLogo = onGoHome ? (
    <button
      type="button"
      className="app-topbar-home-logo"
      onClick={onGoHome}
      aria-label="Home"
      title="Home"
    >
      <svg className="app-topbar-home-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
        <polyline points="9 22 9 12 15 12 15 22" />
      </svg>
    </button>
  ) : null;

  const leftSlot = homeLogo;

  const rightSlot = (
    <div className="app-topbar-right-with-home">
      <span className="app-topbar-brand">Dealer Saathi <sup>©</sup></span>
      <div className="app-topbar-date">{headerRight}</div>
    </div>
  );

  return (
    <div className="app-wrap app-wrap-v2">
      <div className="app-box">
        <Header
          title={headerTitle}
          subtitle={headerSubtitle}
          leftSlot={leftSlot}
          rightSlot={rightSlot}
        />
        <nav className="app-tabs-v2" role="tablist">
          {tabs.map((p) => (
            <button
              key={p}
              type="button"
              role="tab"
              aria-selected={currentPage === p}
              className={`app-tab-v2 ${currentPage === p ? "active" : ""}`}
              onClick={() => onNavigate(p)}
            >
              {PAGE_LABELS[p]}
            </button>
          ))}
        </nav>
        <main className="app-main-v2">
          <div className="app-main-v2-content" key={currentPage}>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
