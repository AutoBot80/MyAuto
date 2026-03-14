import { useState, useEffect, useRef, Component, type ReactNode } from "react";
import "./App.css";
import type { Page } from "./types";
import { useToday } from "./hooks/useToday";
import { AppLayoutV2 } from "./components/AppLayoutV2";
import { AddSalesPage } from "./pages/AddSalesPage";
import { HomePage } from "./pages/HomePage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { getDealer } from "./api/dealers";
import { getBaseUrl } from "./api/client";

const DEALER_ID = Number(import.meta.env.VITE_DEALER_ID) || 100001;

class PageErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean; error: Error | null }> {
  state = { hasError: false, error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }
  render() {
    if (this.state.hasError && this.state.error) {
      return (
        <div style={{ padding: "1rem", color: "#c00" }}>
          <strong>Something went wrong.</strong>
          <pre style={{ marginTop: "0.5rem", fontSize: "12px", overflow: "auto" }}>
            {this.state.error.message}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

type AppMode = "home" | "pos" | "service" | "rto";

const POS_PAGES: Page[] = [
  "add-sales",
  "customer-details",
  "rto-status",
  "contact-us",
];

const SERVICE_PAGES: Page[] = ["service-reminders", "contact-us"];

const RTO_PAGES: Page[] = ["rto-status", "contact-us"];

function getValidDmsUrl(dmsLink: string | null): string {
  const base = getBaseUrl().replace(/\/$/, "");
  const fallback = `${base}/dummy-dms/`;
  if (!dmsLink || typeof dmsLink !== "string") return fallback;
  const trimmed = dmsLink.trim();
  if (trimmed === "" || (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")))
    return fallback;
  return trimmed;
}

function App() {
  const today = useToday();
  const [mode, setMode] = useState<AppMode>("home");
  const [page, setPage] = useState<Page>("add-sales");
  const [dealerName, setDealerName] = useState<string>("—");
  const [dealerCity, setDealerCity] = useState<string | null>(null);
  const [dmsLink, setDmsLink] = useState<string | null>(null);

  useEffect(() => {
    getDealer(DEALER_ID)
      .then((d) => {
        setDealerName(d.dealer_name);
        setDealerCity(d.city ?? null);
        setDmsLink(d.dms_link ?? null);
      })
      .catch(() => setDealerName("Dealer"));
  }, []);

  const dmsUrl = getValidDmsUrl(dmsLink);
  const dmsWindowRef = useRef<Window | null>(null);
  const vahanUrl = `${getBaseUrl().replace(/\/$/, "")}/dummy-vaahan/`;
  const vahanWindowRef = useRef<Window | null>(null);

  const openDmsInNewTab = () => {
    if (dmsWindowRef.current && !dmsWindowRef.current.closed) {
      dmsWindowRef.current.focus();
    } else {
      dmsWindowRef.current = window.open(dmsUrl, "_blank");
    }
  };

  const openVahanInNewTab = () => {
    if (vahanWindowRef.current && !vahanWindowRef.current.closed) {
      vahanWindowRef.current.focus();
    } else {
      vahanWindowRef.current = window.open(vahanUrl, "_blank");
    }
  };

  function renderContent(p: Page) {
    switch (p) {
      case "add-sales":
        return (
          <AddSalesPage
            dealerId={DEALER_ID}
            dmsUrl={dmsUrl}
            openDmsInNewTab={openDmsInNewTab}
            openVahanInNewTab={openVahanInNewTab}
          />
        );
      case "customer-details":
        return <PlaceholderPage title="Customer Details" />;
      case "rto-status":
        return <PlaceholderPage title="RTO Payments Pending" />;
      case "service-reminders":
        return <PlaceholderPage title="Service Reminders" />;
      case "contact-us":
        return <PlaceholderPage title="Contact Us" />;
      default:
        return null;
    }
  }

  const headerRight = (
    <div className="app-topbar-right">
      <span>{today}</span>
    </div>
  );

  if (mode === "home") {
    return (
      <div className="app-wrap app-wrap-v2">
        <div className="app-box">
          <header className="app-topbar">
            <div className="app-topbar-left" />
            <div className="app-topbar-spacer" />
            <div className="app-topbar-title-block">
              <h1 className="app-topbar-title">{dealerName}</h1>
              {dealerCity ? (
                <span className="app-topbar-subtitle">{dealerCity}</span>
              ) : null}
            </div>
            <div className="app-topbar-spacer" />
            <div className="app-topbar-right-with-home">
              <span className="app-topbar-brand">Dealer Saathi <sup>©</sup></span>
              <div className="app-topbar-date">{headerRight}</div>
            </div>
          </header>
          <main className="app-main-v2">
            <HomePage
              onSelectPos={() => {
                setMode("pos");
                setPage("add-sales");
              }}
              onSelectService={() => {
                setMode("service");
                setPage("service-reminders");
              }}
              onSelectRto={() => {
                setMode("rto");
                setPage("rto-status");
              }}
            />
          </main>
        </div>
      </div>
    );
  }

  const visiblePages =
    mode === "pos" ? POS_PAGES : mode === "rto" ? RTO_PAGES : SERVICE_PAGES;
  const currentPage = visiblePages.includes(page) ? page : visiblePages[0];
  const content = renderContent(currentPage) ?? renderContent(visiblePages[0]);

  // When switching mode, ensure page is in the current tab list (avoid blank screen)
  useEffect(() => {
    if (mode === "pos" && !POS_PAGES.includes(page)) setPage("add-sales");
    else if (mode === "service" && !SERVICE_PAGES.includes(page)) setPage("service-reminders");
    else if (mode === "rto" && !RTO_PAGES.includes(page)) setPage("rto-status");
  }, [mode, page]);

  return (
    <div className="app-layout-root" key={mode}>
      <AppLayoutV2
        headerTitle={dealerName}
        headerSubtitle={dealerCity}
        headerRight={headerRight}
        currentPage={currentPage}
        onNavigate={(p) => setPage(p)}
        visiblePages={visiblePages}
        onGoHome={() => setMode("home")}
      >
        <PageErrorBoundary>{content}</PageErrorBoundary>
      </AppLayoutV2>
    </div>
  );
}

export default App;
