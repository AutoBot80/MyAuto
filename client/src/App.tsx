import { useState } from "react";
import "./App.css";
import type { Page } from "./types";
import { useToday } from "./hooks/useToday";
import { AppLayoutV2 } from "./components/AppLayoutV2";
import { AddSalesPage } from "./pages/AddSalesPage";
import { AiReaderQueuePage } from "./pages/AiReaderQueuePage";
import { PlaceholderPage } from "./pages/PlaceholderPage";

function App() {
  const today = useToday();
  const [page, setPage] = useState<Page>("add-sales");

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
      headerTitle="Arya Agencies"
      headerRight={headerRight}
      currentPage={page}
      onNavigate={setPage}
    >
      {mainContent}
    </AppLayoutV2>
  );
}

export default App;
