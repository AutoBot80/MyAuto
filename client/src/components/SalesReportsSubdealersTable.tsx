import { useCallback, useEffect, useState } from "react";
import {
  listCommittedChallanInvoiceDetails,
  type ChallanInvoiceDetailLine,
  type ChallanInvoiceMasterRow,
} from "../api/subdealerChallan";
import {
  cell,
  formatChallanDateDisplay,
  formatInrAmount,
  formatLatestRunDisplay,
} from "../utils/formatDisplay";

export interface SalesReportsSubdealersTableProps {
  dealerId: number;
  rows: ChallanInvoiceMasterRow[];
  loading: boolean;
  error: string | null;
  tabActive: boolean;
}

export function SalesReportsSubdealersTable({
  dealerId,
  rows,
  loading,
  error,
  tabActive,
}: SalesReportsSubdealersTableProps) {
  const [selectedChallanId, setSelectedChallanId] = useState<number | null>(null);
  const [detailLines, setDetailLines] = useState<ChallanInvoiceDetailLine[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    if (!tabActive) return;
    setSelectedChallanId(null);
    setDetailLines([]);
    setDetailError(null);
  }, [rows, tabActive]);

  const loadDetails = useCallback(
    async (challanId: number) => {
      if (dealerId <= 0) return;
      setDetailError(null);
      setDetailLoading(true);
      try {
        const lines = await listCommittedChallanInvoiceDetails(challanId, dealerId);
        setDetailLines(lines);
      } catch (e) {
        setDetailError(e instanceof Error ? e.message : "Failed to load vehicle lines.");
        setDetailLines([]);
      } finally {
        setDetailLoading(false);
      }
    },
    [dealerId]
  );

  useEffect(() => {
    if (!tabActive || selectedChallanId === null) return;
    void loadDetails(selectedChallanId);
  }, [tabActive, selectedChallanId, loadDetails]);

  if (error) {
    return (
      <div className="subdealer-challan-error" role="alert">
        {error}
      </div>
    );
  }

  if (loading && rows.length === 0) {
    return <p className="app-table-empty challans-processed-loading-msg">Loading…</p>;
  }

  if (!loading && rows.length === 0) {
    return (
      <p className="app-table-empty challans-processed-loading-msg">
        No committed subdealer invoices in this period.
      </p>
    );
  }

  return (
    <div className="challans-processed-split">
      <div className="challans-processed-master">
        <div className="challans-processed-table-wrap" role="region" aria-label="Subdealer invoices">
          <table className="app-table">
            <thead>
              <tr>
                <th scope="col">Subdealer</th>
                <th scope="col">Vehicles</th>
                <th scope="col">Challan date</th>
                <th scope="col">Challan no.</th>
                <th scope="col">Order no.</th>
                <th scope="col">Invoice no.</th>
                <th scope="col">Created</th>
                <th scope="col" className="challans-proc-col--amount">
                  Total cost (ex-showroom)
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const sel = selectedChallanId === r.challan_id;
                return (
                  <tr
                    key={r.challan_id}
                    className={"challans-proc-master-row" + (sel ? " challans-proc-master-row--selected" : "")}
                    aria-selected={sel}
                    tabIndex={0}
                    onClick={() => setSelectedChallanId(r.challan_id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setSelectedChallanId(r.challan_id);
                      }
                    }}
                  >
                    <td>{(r.to_dealer_name || "").trim() || `Dealer ${r.dealer_to}`}</td>
                    <td>{r.num_vehicles ?? "—"}</td>
                    <td>{formatChallanDateDisplay(r.challan_date)}</td>
                    <td>{cell(r.challan_book_num)}</td>
                    <td>{cell(r.order_number)}</td>
                    <td>{cell(r.invoice_number)}</td>
                    <td>{formatLatestRunDisplay(r.created_at)}</td>
                    <td className="challans-proc-col--amount">{formatInrAmount(r.total_ex_showroom_price)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <div
        className="challans-processed-failed-section challans-invoices-vehicle-section"
        role="region"
        aria-labelledby="sales-reports-subdealer-detail-heading"
      >
        <h3
          className="challans-processed-failed-heading challans-invoices-vehicle-heading"
          id="sales-reports-subdealer-detail-heading"
        >
          Vehicles (discount per line)
        </h3>
        <div className="challans-processed-failed-table-wrap challans-invoices-vehicle-wrap">
          {selectedChallanId === null ? (
            <p className="app-table-empty challans-processed-failed-placeholder">
              Select an invoice row above to view vehicles.
            </p>
          ) : detailError ? (
            <div className="subdealer-challan-error" role="alert">
              {detailError}
            </div>
          ) : detailLoading ? (
            <p className="app-table-empty challans-processed-failed-placeholder">Loading vehicles…</p>
          ) : detailLines.length === 0 ? (
            <p className="app-table-empty challans-processed-failed-placeholder">No vehicle lines for this invoice.</p>
          ) : (
            <table className="app-table challans-invoices-vehicle-table">
              <thead>
                <tr>
                  <th scope="col">S.No.</th>
                  <th scope="col">Chassis</th>
                  <th scope="col">Engine</th>
                  <th scope="col">Model</th>
                  <th scope="col">Variant</th>
                  <th scope="col">Color</th>
                  <th scope="col">Ex-showroom</th>
                  <th scope="col">Discount</th>
                </tr>
              </thead>
              <tbody>
                {detailLines.map((ln, idx) => (
                  <tr key={ln.inventory_line_id}>
                    <td>{idx + 1}</td>
                    <td className="view-vehicles-mono">{cell(ln.chassis_no)}</td>
                    <td className="view-vehicles-mono">{cell(ln.engine_no)}</td>
                    <td>{cell(ln.model)}</td>
                    <td>{cell(ln.variant)}</td>
                    <td>{cell(ln.color)}</td>
                    <td>{formatInrAmount(ln.ex_showroom_price)}</td>
                    <td>{formatInrAmount(ln.discount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
