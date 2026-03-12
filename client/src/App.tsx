import { useState, useEffect } from "react";
import "./App.css";
import type { Page } from "./types";
import { useToday } from "./hooks/useToday";
import { AppLayoutV2 } from "./components/AppLayoutV2";
import { AddSalesPage } from "./pages/AddSalesPage";
import { AiReaderQueuePage } from "./pages/AiReaderQueuePage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { getDealer } from "./api/dealers";

const DEALER_ID = Number(import.meta.env.VITE_DEALER_ID) || 100001;

function App() {
  const today = useToday();
  const [page, setPage] = useState<Page>("add-sales");
  const [dealerName, setDealerName] = useState<string>("—");
  const [dealerCity, setDealerCity] = useState<string | null>(null);

  useEffect(() => {
    getDealer(DEALER_ID)
      .then((d) => {
        setDealerName(d.dealer_name);
        setDealerCity(d.city ?? null);
      })
      .catch(() => setDealerName("Dealer"));
  }, []);

  const mainContent =
    page === "add-sales" ? (
      <AddSalesPage />
    ) : page === "customer-details" ? (
      <PlaceholderPage title="Customer Details" />
    ) : page === "rto-status" ? (
      <PlaceholderPage title="RTO Queue" />
    ) : page === "ai-reader-queue" ? (
      <AiReaderQueuePage />
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
