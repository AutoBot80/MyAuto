import { useState, useEffect, useCallback, Component, type ReactNode } from "react";
import "./App.css";
import type { Page } from "./types";
import { useToday } from "./hooks/useToday";
import { AppChrome } from "./components/AppChrome";
import { AppLayoutV2 } from "./components/AppLayoutV2";
import { AddSalesPage } from "./pages/AddSalesPage";
import { AdminPage } from "./pages/AdminPage";
import { AdminDataFolderPage } from "./pages/AdminDataFolderPage";
import { AdminDealersPage } from "./pages/AdminDealersPage";
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
import { LoginPage } from "./pages/LoginPage";
import { ALL_HOME_TILES_TRUE, getMe, type HomeTileFlags } from "./api/auth";
import { clearAccessToken, getAccessToken } from "./auth/token";

const authDisabled = import.meta.env.VITE_AUTH_DISABLED === "true";

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

const ADMIN_PAGES: Page[] = ["admin-dealers", "admin-upload-scans", "admin-run-logs", "admin-tools"];

function initialLoginNameFromEnv(): string | null {
  const v = import.meta.env.VITE_LOGIN_NAME;
  if (typeof v !== "string" || !v.trim()) return null;
  return v.trim();
}

type BootState = "loading" | "need-login" | "ready";

function App() {
  const today = useToday();
  const pageVisible = usePageVisible();
  const [boot, setBoot] = useState<BootState>(() => {
    if (authDisabled) return "ready";
    return getAccessToken() ? "loading" : "need-login";
  });
  const [dealerId, setDealerId] = useState(() => Number(import.meta.env.VITE_DEALER_ID) || 100001);
  const [mode, setMode] = useState<AppMode>("home");
  const [page, setPage] = useState<Page>("add-sales");
  const [bulkLoadsPendingCount, setBulkLoadsPendingCount] = useState<number>(0);
  const [challanFailedCount, setChallanFailedCount] = useState<number>(0);
  const [dealerName, setDealerName] = useState<string>("—");
  const [dealerAddress, setDealerAddress] = useState<string | null>(null);
  const [dealerOemId, setDealerOemId] = useState<number | null>(null);
  const [dealerPreferInsurer, setDealerPreferInsurer] = useState<string | null>(null);
  const [siteUrls, setSiteUrls] = useState<SiteUrls | null>(null);
  const [siteUrlsError, setSiteUrlsError] = useState<string | null>(null);
  const [addSalesAutoNewTrigger, setAddSalesAutoNewTrigger] = useState(0);
  /** Display name for Welcome line; rename _setLoginName when wiring the Login screen. */
  const [loginName, _setLoginName] = useState<string | null>(initialLoginNameFromEnv);
  /** Home tiles from ``roles_ref`` flags (JWT / ``/auth/me``). Dev bypass: all true. */
  const [homeTiles, setHomeTiles] = useState<HomeTileFlags>(ALL_HOME_TILES_TRUE);
  const [sessionAdmin, setSessionAdmin] = useState(authDisabled);

  useEffect(() => {
    if (boot !== "ready") return;
    getDealer(dealerId)
      .then((d) => {
        setDealerName(d.dealer_name);
        const addr = d.address?.trim();
        setDealerAddress(addr ? addr : null);
        setDealerOemId(d.oem_id ?? null);
        setDealerPreferInsurer(d.prefer_insurer ?? null);
      })
      .catch(() => {
        setDealerName("Dealer");
        setDealerAddress(null);
      });
  }, [boot, dealerId]);

  useEffect(() => {
    if (authDisabled) return;
    if (!getAccessToken()) {
      setBoot("need-login");
      return;
    }
    getMe()
      .then((m) => {
        setDealerId(m.dealer_id);
        _setLoginName(m.name || m.login_id);
        setHomeTiles({
          tile_pos: m.tile_pos,
          tile_rto: m.tile_rto,
          tile_service: m.tile_service,
          tile_dealer: m.tile_dealer,
        });
        setSessionAdmin(m.admin);
        setBoot("ready");
      })
      .catch(() => {
        clearAccessToken();
        setBoot("need-login");
      });
  }, [authDisabled]);

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
    getChallanStagingFailedCount(dealerId)
      .then(setChallanFailedCount)
      .catch(() => setChallanFailedCount(0));
  }, [dealerId]);

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
    else if (mode === "admin" && !ADMIN_PAGES.includes(page)) setPage("admin-dealers");
  }, [mode, page]);

  // If session loses access to a module (roles / JWT), leave that mode.
  useEffect(() => {
    if (boot !== "ready" || authDisabled) return;
    if (mode === "pos" && !homeTiles.tile_pos) setMode("home");
    else if (mode === "service" && !homeTiles.tile_service) setMode("home");
    else if (mode === "rto" && !homeTiles.tile_rto) setMode("home");
    else if (mode === "dealer" && !homeTiles.tile_dealer) setMode("home");
    else if (mode === "admin" && !sessionAdmin) setMode("home");
  }, [boot, authDisabled, mode, homeTiles, sessionAdmin]);

  const dmsUrl = siteUrls?.dms_base_url ?? "";

  function renderContent(p: Page) {
    switch (p) {
      case "add-sales":
        return (
          <AddSalesPage
            dealerId={dealerId}
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
            dealerId={dealerId}
            dmsUrl={dmsUrl}
            challanFailedCount={challanFailedCount}
            onChallanCountsRefresh={refreshChallanFailedCount}
          />
        );
      case "bulk-loads":
        return <BulkLoadsPage dealerId={dealerId} onNavigateToAddSales={() => setPage("add-sales")} />;
      case "customer-details":
        return <ViewCustomerPage dealerId={dealerId} />;
      case "view-vehicles":
        return <ViewVehiclesPage dealerId={dealerId} />;
      case "rto-status":
        return <RtoPaymentsPendingPage dealerId={dealerId} />;
      case "service-reminders":
        return <PlaceholderPage title="Service Reminders" />;
      case "dealer-dashboard":
        return <PlaceholderPage title="Dealer Saathi" message="RTO details, Sub-dealer sales etc. – Coming soon." />;
      case "admin-tools":
        return <AdminPage />;
      case "admin-upload-scans":
        return <AdminDataFolderPage dealerId={dealerId} kind="upload-scans" />;
      case "admin-run-logs":
        return <AdminDataFolderPage dealerId={dealerId} kind="run-logs" />;
      case "admin-dealers":
        return <AdminDealersPage />;
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

  if (boot === "loading") {
    return (
      <div style={{ padding: "2rem", textAlign: "center", fontFamily: "system-ui, sans-serif" }}>
        Loading…
      </div>
    );
  }
  if (boot === "need-login") {
    return (
      <LoginPage
        onLoggedIn={(s) => {
          setDealerId(s.dealer_id);
          _setLoginName(s.name || s.login_id);
          setHomeTiles({
            tile_pos: s.tile_pos,
            tile_rto: s.tile_rto,
            tile_service: s.tile_service,
            tile_dealer: s.tile_dealer,
          });
          setSessionAdmin(s.admin);
          setBoot("ready");
        }}
      />
    );
  }

  if (mode === "home") {
    return (
      <AppChrome>
        <div className="app-wrap app-wrap-v2">
          <div className="app-box">
            <header className="app-topbar">
              <div className="app-topbar-left">
                {loginName ? <span className="app-topbar-welcome">Welcome {loginName}</span> : null}
              </div>
              <div className="app-topbar-spacer" />
              <div className="app-topbar-title-block">
                <h1 className="app-topbar-title">{dealerName}</h1>
                {dealerAddress ? (
                  <span className="app-topbar-subtitle">{dealerAddress}</span>
                ) : null}
              </div>
              <div className="app-topbar-spacer" />
              <div className="app-topbar-right-with-home">
                <span className="app-topbar-brand">© Dealer Saathi ™</span>
                <div className="app-topbar-date">{headerRight}</div>
              </div>
            </header>
            <main className="app-main-v2">
              <HomePage
                tiles={homeTiles}
                showAdmin={sessionAdmin}
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
                  setPage("admin-dealers");
                }}
              />
            </main>
          </div>
        </div>
      </AppChrome>
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
    <AppChrome>
      <div className="app-layout-root" key={mode}>
        <AppLayoutV2
          headerTitle={dealerName}
          headerSubtitle={dealerAddress}
          headerRight={headerRight}
          currentPage={currentPage}
          onNavigate={(p) => setPage(p)}
          visiblePages={visiblePages}
          onGoHome={() => setMode("home")}
          welcomeLoginName={loginName}
          tabBadges={
            mode === "pos"
              ? { "bulk-loads": bulkLoadsPendingCount, "subdealer-challan": challanFailedCount }
              : undefined
          }
        >
          <PageErrorBoundary>{content}</PageErrorBoundary>
        </AppLayoutV2>
      </div>
    </AppChrome>
  );
}

export default App;
