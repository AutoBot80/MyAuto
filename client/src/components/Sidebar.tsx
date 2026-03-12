import type { Page } from "../types";

interface SidebarProps {
  currentPage: Page;
  onNavigate: (page: Page) => void;
}

const LABELS: Record<Page, string> = {
  "add-sales": "Add Sales",
  "customer-details": "Customer Details",
  "rto-status": "RTO Queue",
  "ai-reader-queue": "AI Reader Queue",
};

export function Sidebar({ currentPage, onNavigate }: SidebarProps) {
  return (
    <nav className="app-sidebar">
      {(Object.keys(LABELS) as Page[]).map((page) => (
        <a
          key={page}
          href={`#${page}`}
          className={`app-nav-link ${currentPage === page ? "active" : ""}`}
          onClick={(e) => {
            e.preventDefault();
            onNavigate(page);
          }}
        >
          {LABELS[page]}
        </a>
      ))}
    </nav>
  );
}
