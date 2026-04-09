import { useState, useEffect, useCallback, Component, type ReactNode } from "react";
import "./App.css";
import type { Page } from "./types";
import { useToday } from "./hooks/useToday";
import { AppLayoutV2 } from "./components/AppLayoutV2";
import { AddSalesPage } from "./pages/AddSalesPage";
import { AdminPage } from "./pages/AdminPage";
import { HomePage } from "./pages/HomePage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { ViewCustomerPage } from "./pages/ViewCustomerPage";
import { ViewVehiclesPage } from "./pages/ViewVehiclesPage";
import { RtoPaymentsPendingPage } from "./pages/RtoPaymentsPendingPage";
import { BulkLoadsPage } from "./pages/BulkLoadsPage";
import { SubdealerChallanPage } from "./pages/SubdealerChallanPage";
import { getDealer } from "./api/dealers";
import { getBulkLoadPendingCount } from "./api/bulkLoads";
import { getChallanStagingFailedCount } from "./api/subdealerChallan";
import { getSiteUrls, type SiteUrls } from "./api/siteUrls";
import { usePageVisible } from "./hooks/usePageVisible";

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

type AppMode = "home" | "pos" | "service" | "rto" | "dealer" | "admin";

const POS_PAGES: Page[] = [
  "add-sales",
  "subdealer-challan",
  "bulk-loads",
  "customer-details",
  "view-vehicles",
  "contact-us",
];

const SERVICE_PAGES: Page[] = ["service-reminders", "contact-us"];

const RTO_PAGES: Page[] = ["rto-status", "contact-us"];

const DEALER_PAGES: Page[] = ["dealer-dashboard", "contact-us"];

const ADMIN_PAGES: Page[] = ["admin-tools", "contact-us"];

function App() {
  const today = useToday();
  const pageVisible = usePageVisible();
  const [mode, setMode] = useState<AppMode>("home");
  const [page, setPage] = useState<Page>("add-sales");
  const [bulkLoadsPendingCount, setBulkLoadsPendingCount] = useState<number>(0);
  const [challanFailedCount, setChallanFailedCount] = useState<number>(0);
  const [dealerName, setDealerName] = useState<string>("—");
  const [dealerCity, setDealerCity] = useState<string | null>(null);
  const [dealerOemId, setDealerOemId] = useState<number | null>(null);
  const [dealerPreferInsurer, setDealerPreferInsurer] = useState<string | null>(null);
  const [siteUrls, setSiteUrls] = useState<SiteUrls | null>(null);
  const [siteUrlsError, setSiteUrlsError] = useState<string | null>(null);
  const [addSalesAutoNewTrigger, setAddSalesAutoNewTrigger] = useState(0);

  useEffect(() => {
    getDealer(DEALER_ID)
      .then((d) => {
        setDealerName(d.dealer_name);
        setDealerCity(d.city ?? null);
        setDealerOemId(d.oem_id ?? null);
        setDealerPreferInsurer(d.prefer_insurer ?? null);
      })
      .catch(() => setDealerName("Dealer"));
  }, []);

  useEffect(() => {
    getSiteUrls()
      .then(setSiteUrls)
      .catch((e) => setSiteUrlsError(e instanceof Error ? e.message : "Could not load site URLs from server."));
  }, []);

  const refreshBulkLoadsPendingCount = () => {
    if (mode === "pos") {
      getBulkLoadPendingCount()
        .then(setBulkLoadsPendingCount)
        .catch(() => setBulkLoadsPendingCount(0));
    }
  };

  const refreshChallanFailedCount = useCallback(() => {
    getChallanStagingFailedCount(DEALER_ID)
      .then(setChallanFailedCount)
      .catch(() => setChallanFailedCount(0));
  }, []);

  // Bulk-loads badge only: not tied to Add Sales OCR (upload runs extraction synchronously on the server).
  useEffect(() => {
    if (mode !== "pos") {
      setBulkLoadsPendingCount(0);
      return;
    }
    if (!pageVisible) {
      return;
    }
    refreshBulkLoadsPendingCount();
    const interval = setInterval(refreshBulkLoadsPendingCount, 60000);
    return () => clearInterval(interval);
  }, [mode, pageVisible]);

  // Subdealer Challans: master batches needing attention in the last 15 days (nav + Processed sub-tab badges).
  useEffect(() => {
    if (mode !== "pos") {
      setChallanFailedCount(0);
      return;
    }
    if (!pageVisible) {
      return;
    }
    refreshChallanFailedCount();
    const interval = setInterval(refreshChallanFailedCount, 60000);
    return () => clearInterval(interval);
  }, [mode, pageVisible, refreshChallanFailedCount]);

  // When switching mode, ensure page is in the current tab list (avoid blank screen). Must run on every render (before any early return).
  useEffect(() => {
    if (mode === "pos" && !POS_PAGES.includes(page)) setPage("add-sales");
    else if (mode === "service" && !SERVICE_PAGES.includes(page)) setPage("service-reminders");
    else if (mode === "rto" && !RTO_PAGES.includes(page)) setPage("rto-status");
    else if (mode === "dealer" && !DEALER_PAGES.includes(page)) setPage("dealer-dashboard");
    else if (mode === "admin" && !ADMIN_PAGES.includes(page)) setPage("admin-tools");
  }, [mode, page]);

  const dmsUrl = siteUrls?.dms_base_url ?? "";

  function renderContent(p: Page) {
    switch (p) {
      case "add-sales":
        return (
          <AddSalesPage
            dealerId={DEALER_ID}
            oemId={dealerOemId}
            preferInsurer={dealerPreferInsurer}
            dmsUrl={dmsUrl}
            siteUrlsError={siteUrlsError}
            siteUrlsLoading={!siteUrls && !siteUrlsError}
            autoNewTrigger={addSalesAutoNewTrigger}
          />
        );
      case "subdealer-challan":
        return (
          <SubdealerChallanPage
            dealerId={DEALER_ID}
            dmsUrl={dmsUrl}
            challanFailedCount={challanFailedCount}
            onChallanCountsRefresh={refreshChallanFailedCount}
          />
        );
      case "bulk-loads":
        return <BulkLoadsPage dealerId={DEALER_ID} onNavigateToAddSales={() => setPage("add-sales")} />;
      case "customer-details":
        return <ViewCustomerPage dealerId={DEALER_ID} />;
      case "view-vehicles":
        return <ViewVehiclesPage dealerId={DEALER_ID} />;
      case "rto-status":
        return <RtoPaymentsPendingPage dealerId={DEALER_ID} />;
      case "service-reminders":
        return <PlaceholderPage title="Service Reminders" />;
      case "dealer-dashboard":
        return <PlaceholderPage title="Dealer Saathi" message="RTO details, Sub-dealer sales etc. – Coming soon." />;
      case "admin-tools":
        return <AdminPage />;
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
                setAddSalesAutoNewTrigger((n) => n + 1);
              }}
              onSelectService={() => {
                setMode("service");
                setPage("service-reminders");
              }}
              onSelectRto={() => {
                setMode("rto");
                setPage("rto-status");
              }}
              onSelectDealer={() => {
                setMode("dealer");
                setPage("dealer-dashboard");
              }}
              onSelectAdmin={() => {
                setMode("admin");
                setPage("admin-tools");
              }}
            />
          </main>
        </div>
      </div>
    );
  }

  const visiblePages =
    mode === "pos" ? POS_PAGES
    : mode === "rto" ? RTO_PAGES
    : mode === "dealer" ? DEALER_PAGES
    : mode === "admin" ? ADMIN_PAGES
    : SERVICE_PAGES;
  const currentPage = visiblePages.includes(page) ? page : visiblePages[0];
  const content = renderContent(currentPage) ?? renderContent(visiblePages[0]);

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
        tabBadges={
          mode === "pos"
            ? { "bulk-loads": bulkLoadsPendingCount, "subdealer-challan": challanFailedCount }
            : undefined
        }
      >
        <PageErrorBoundary>{content}</PageErrorBoundary>
      </AppLayoutV2>
    </div>
  );
}

export default App;
