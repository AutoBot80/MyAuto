import { useState, useEffect } from "react";
import "./App.css";
import type { Page } from "./types";
import { useToday } from "./hooks/useToday";
import { AppLayoutV2 } from "./components/AppLayoutV2";
import { AddSalesPage } from "./pages/AddSalesPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { getDealer } from "./api/dealers";
import { getBaseUrl } from "./api/client";

const DEALER_ID = Number(import.meta.env.VITE_DEALER_ID) || 100001;

function App() {
  const today = useToday();
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

  const mainContent =
    page === "add-sales" ? (
      <AddSalesPage dealerId={DEALER_ID} />
    ) : page === "customer-details" ? (
      <PlaceholderPage title="Customer Details" />
    ) : page === "dms-queue" ? (
      <iframe
        src={dmsLink && dmsLink.trim() !== "" ? dmsLink : `${getBaseUrl()}/dummy-dms/`}
        title="DMS"
        className="app-iframe-dms"
      />
    ) : page === "insurance-queue" ? (
      <PlaceholderPage title="Insurance Queue" />
    ) : page === "rto-status" ? (
      <PlaceholderPage title="RTO Queue" />
    ) : page === "service-reminders" ? (
      <PlaceholderPage title="Service Reminders" />
    ) : page === "contact-us" ? (
      <PlaceholderPage title="Contact Us" />
    ) : null;

  const headerRight = (
    <div className="app-topbar-right">
      <span>{today}</span>
    </div>
  );

  return (
    <AppLayoutV2
      headerTitle={dealerName}
      headerSubtitle={dealerCity}
      headerRight={headerRight}
      currentPage={page}
      onNavigate={setPage}
    >
      {mainContent}
    </AppLayoutV2>
  );
}

export default App;
