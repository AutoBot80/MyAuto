import { useCallback, useEffect, useState } from "react";
import { fetchAddSalesInvoices, type AddSalesInvoiceRow } from "../api/addSales";
import {
  listRecentCommittedChallanInvoices,
  type ChallanInvoiceMasterRow,
} from "../api/subdealerChallan";
import { SalesReportsCustomersTable } from "../components/SalesReportsCustomersTable";
import { SalesReportsSubdealersTable } from "../components/SalesReportsSubdealersTable";
import { downloadExcel } from "../utils/exportToExcel";
import {
  cell,
  formatChallanDateDisplay,
  formatCost,
  formatCostPerVehicleDisplay,
  formatDiscountReductionDisplay,
  formatInrAmount,
  formatLatestRunDisplay,
} from "../utils/formatDisplay";
import {
  defaultSalesReportsDateRange,
  type SalesReportsDatePreset,
  presetDateRange,
} from "../utils/istDatePresets";
import "./SalesReportsPage.css";

export type SalesReportsSubTab = "customers" | "subdealers";

export interface SalesReportsPageProps {
  dealerId: number;
  isPrincipalDealer: boolean;
}

export function SalesReportsPage({ dealerId, isPrincipalDealer }: SalesReportsPageProps) {
  const initial = defaultSalesReportsDateRange();
  const [dateFrom, setDateFrom] = useState(initial.from);
  const [dateTo, setDateTo] = useState(initial.to);
  const [preset, setPreset] = useState<SalesReportsDatePreset | "custom">(initial.preset);
  const [subTab, setSubTab] = useState<SalesReportsSubTab>("customers");

  const [customerRows, setCustomerRows] = useState<AddSalesInvoiceRow[]>([]);
  const [customerLoading, setCustomerLoading] = useState(false);
  const [customerError, setCustomerError] = useState<string | null>(null);

  const [subdealerRows, setSubdealerRows] = useState<ChallanInvoiceMasterRow[]>([]);
  const [subdealerLoading, setSubdealerLoading] = useState(false);
  const [subdealerError, setSubdealerError] = useState<string | null>(null);

  const showSubdealersTab = isPrincipalDealer;

  useEffect(() => {
    if (!showSubdealersTab && subTab === "subdealers") {
      setSubTab("customers");
    }
  }, [showSubdealersTab, subTab]);

  const loadCustomers = useCallback(
    async (from: string, to: string) => {
      if (dealerId <= 0) return;
      setCustomerError(null);
      setCustomerLoading(true);
      try {
        const r = await fetchAddSalesInvoices(dealerId, { dateFrom: from, dateTo: to });
        setCustomerRows(r.rows ?? []);
      } catch (e) {
        setCustomerError(e instanceof Error ? e.message : "Failed to load customer sales.");
        setCustomerRows([]);
      } finally {
        setCustomerLoading(false);
      }
    },
    [dealerId]
  );

  const loadSubdealers = useCallback(
    async (from: string, to: string) => {
      if (dealerId <= 0 || !showSubdealersTab) return;
      setSubdealerError(null);
      setSubdealerLoading(true);
      try {
        const rows = await listRecentCommittedChallanInvoices(dealerId, {
          dateFrom: from,
          dateTo: to,
          limit: 2000,
        });
        setSubdealerRows(rows);
      } catch (e) {
        setSubdealerError(e instanceof Error ? e.message : "Failed to load subdealer invoices.");
        setSubdealerRows([]);
      } finally {
        setSubdealerLoading(false);
      }
    },
    [dealerId, showSubdealersTab]
  );

  const applyFilters = useCallback(
    (from?: string, to?: string) => {
      const f = from ?? dateFrom;
      const t = to ?? dateTo;
      void loadCustomers(f, t);
      if (showSubdealersTab) void loadSubdealers(f, t);
    },
    [dateFrom, dateTo, loadCustomers, loadSubdealers, showSubdealersTab]
  );

  useEffect(() => {
    if (dealerId <= 0) return;
    applyFilters(initial.from, initial.to);
  }, [dealerId]); // eslint-disable-line react-hooks/exhaustive-deps -- initial load only

  const onPresetChange = (p: SalesReportsDatePreset) => {
    const range = presetDateRange(p);
    setPreset(p);
    setDateFrom(range.from);
    setDateTo(range.to);
    applyFilters(range.from, range.to);
  };

  const onDateInputChange = (which: "from" | "to", value: string) => {
    setPreset("custom");
    if (which === "from") setDateFrom(value);
    else setDateTo(value);
  };

  const exportCustomersExcel = () => {
    const headers = [
      "Customer Name",
      "Mobile",
      "Model",
      "Invoice Date",
      "Invoice Number",
      "Insurance Policy No.",
      "CPA Policy No.",
      "Ex-Showroom",
      "Insurance Premium",
      "CPA Premium",
    ];
    const data = customerRows.map((r) => [
      cell(r.customer_name),
      cell(r.mobile),
      cell(r.model),
      cell(r.invoice_date),
      cell(r.invoice_number),
      cell(r.insurance_policy_num),
      cell(r.cpa_policy_num),
      formatCost(r.ex_showroom_amount),
      formatCost(r.insurance_premium),
      formatCost(r.cpa_premium),
    ]);
    downloadExcel(
      `Sales_Reports_Customers_${dateFrom}_${dateTo}.xlsx`,
      "Customers",
      headers,
      data
    );
  };

  const exportSubdealersExcel = () => {
    const headers = [
      "Subdealer",
      "Vehicles",
      "Challan date",
      "Challan no.",
      "Discount Reduction",
      "Cost per vehicle",
      "Order no.",
      "Invoice no.",
      "Created",
      "Total cost (ex-showroom)",
    ];
    const data = subdealerRows.map((r) => [
      (r.to_dealer_name || "").trim() || `Dealer ${r.dealer_to}`,
      r.num_vehicles ?? "",
      formatChallanDateDisplay(r.challan_date),
      cell(r.challan_book_num),
      formatDiscountReductionDisplay(r),
      formatCostPerVehicleDisplay(r),
      cell(r.order_number),
      cell(r.invoice_number),
      formatLatestRunDisplay(r.created_at),
      formatInrAmount(r.total_ex_showroom_price),
    ]);
    downloadExcel(
      `Sales_Reports_Subdealers_${dateFrom}_${dateTo}.xlsx`,
      "Subdealers",
      headers,
      data
    );
  };

  const onExport = () => {
    if (subTab === "subdealers") exportSubdealersExcel();
    else exportCustomersExcel();
  };

  const activeRows = subTab === "subdealers" ? subdealerRows : customerRows;
  const activeLoading = subTab === "subdealers" ? subdealerLoading : customerLoading;

  return (
    <div className="sales-reports-page">
      <section className="sales-reports-filters" aria-label="Sales report date range">
        <div className="sales-reports-date-field">
          <label htmlFor="sr-date-from">From Date</label>
          <input
            id="sr-date-from"
            type="text"
            autoComplete="off"
            placeholder="dd-mm-yyyy"
            value={dateFrom}
            onChange={(e) => onDateInputChange("from", e.target.value)}
          />
        </div>
        <div className="sales-reports-date-field">
          <label htmlFor="sr-date-to">To Date</label>
          <input
            id="sr-date-to"
            type="text"
            autoComplete="off"
            placeholder="dd-mm-yyyy"
            value={dateTo}
            onChange={(e) => onDateInputChange("to", e.target.value)}
          />
        </div>

        <fieldset className="sales-reports-period">
          <legend className="sales-reports-period-legend">Period (IST)</legend>
          <label className="sales-reports-radio-label">
            <input
              type="radio"
              name="sales-reports-period"
              checked={preset === "current_month"}
              onChange={() => onPresetChange("current_month")}
            />{" "}
            Current Month
          </label>
          <label className="sales-reports-radio-label">
            <input
              type="radio"
              name="sales-reports-period"
              checked={preset === "previous_month"}
              onChange={() => onPresetChange("previous_month")}
            />{" "}
            Previous Month
          </label>
          <label className="sales-reports-radio-label">
            <input
              type="radio"
              name="sales-reports-period"
              checked={preset === "current_fy"}
              onChange={() => onPresetChange("current_fy")}
            />{" "}
            Current Financial Year
          </label>
          <label className="sales-reports-radio-label">
            <input
              type="radio"
              name="sales-reports-period"
              checked={preset === "previous_fy"}
              onChange={() => onPresetChange("previous_fy")}
            />{" "}
            Previous Financial Year
          </label>
        </fieldset>

        <div className="sales-reports-actions">
          <button
            type="button"
            className="app-button app-button--primary"
            onClick={() => applyFilters()}
            disabled={customerLoading || subdealerLoading}
          >
            {customerLoading || subdealerLoading ? "Loading…" : "Apply"}
          </button>
          <button
            type="button"
            className="app-button app-button--small"
            onClick={() => applyFilters()}
            disabled={customerLoading || subdealerLoading}
            title="Reload report data"
          >
            Refresh
          </button>
          <button
            type="button"
            className="app-button app-button--small"
            onClick={onExport}
            disabled={activeLoading || activeRows.length === 0}
            title="Export current table to Excel"
          >
            Export to Excel
          </button>
        </div>
      </section>

      <p className="sales-reports-hint">
        Showing sales from {dateFrom} to {dateTo} (IST). To date defaults to yesterday so today&apos;s sales may be
        incomplete.
      </p>

      <nav className="challans-subtabs" role="tablist" aria-label="Sales report type">
        <button
          type="button"
          role="tab"
          id="sales-reports-tab-customers"
          aria-selected={subTab === "customers"}
          aria-controls="sales-reports-panel-customers"
          className={`challans-subtab ${subTab === "customers" ? "active" : ""}`}
          onClick={() => setSubTab("customers")}
        >
          Customers
        </button>
        {showSubdealersTab ? (
          <button
            type="button"
            role="tab"
            id="sales-reports-tab-subdealers"
            aria-selected={subTab === "subdealers"}
            aria-controls="sales-reports-panel-subdealers"
            className={`challans-subtab ${subTab === "subdealers" ? "active" : ""}`}
            onClick={() => setSubTab("subdealers")}
          >
            Subdealers
          </button>
        ) : null}
      </nav>

      {subTab === "customers" ? (
        <div
          id="sales-reports-panel-customers"
          role="tabpanel"
          aria-labelledby="sales-reports-tab-customers"
          className="sales-reports-panel"
        >
          <SalesReportsCustomersTable rows={customerRows} loading={customerLoading} error={customerError} />
        </div>
      ) : null}

      {subTab === "subdealers" && showSubdealersTab ? (
        <div
          id="sales-reports-panel-subdealers"
          role="tabpanel"
          aria-labelledby="sales-reports-tab-subdealers"
          className="sales-reports-panel"
        >
          <SalesReportsSubdealersTable
            dealerId={dealerId}
            rows={subdealerRows}
            loading={subdealerLoading}
            error={subdealerError}
            tabActive={subTab === "subdealers"}
          />
        </div>
      ) : null}
    </div>
  );
}
