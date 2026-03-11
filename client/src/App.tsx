import { useState } from "react";
import "./App.css";
import type { Page } from "./types";
import { useToday } from "./hooks/useToday";
import { AppLayout } from "./components/AppLayout";
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

  return (
    <AppLayout
      headerTitle="Arya Agencies"
      headerRight={today}
      currentPage={page}
      onNavigate={setPage}
    >
      {mainContent}
    </AppLayout>
  );
}

export default App;
