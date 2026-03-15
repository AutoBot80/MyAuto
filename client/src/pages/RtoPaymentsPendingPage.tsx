import { useState, useEffect } from "react";
import { listRtoPayments, getVahanPayUrl, type RtoPaymentRow } from "../api/rtoPaymentDetails";

interface RtoPaymentsPendingPageProps {
  /** When true, show Pay link column (only on RTO Payment Saathi tab). */
  showPayLink?: boolean;
}

export function RtoPaymentsPendingPage({ showPayLink = false }: RtoPaymentsPendingPageProps) {
  const [rows, setRows] = useState<RtoPaymentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listRtoPayments()
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="app-placeholder rto-payments-page">
        <h2>RTO Payments Pending</h2>
        <p>Loading…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="app-placeholder rto-payments-page">
        <h2>RTO Payments Pending</h2>
        <p className="rto-payments-error">{error}</p>
      </div>
    );
  }

  const columns = [
    "Customer ID",
    "Customer Name",
    "Mobile",
    "Vehicle ID",
    "Chassis No.",
    "Application ID",
    "Register Date",
    "RTO Fees",
    "Status",
    ...(showPayLink ? ["Pay"] : []),
  ];

  return (
    <div className="rto-payments-page">
      <h2 className="rto-payments-title">RTO Payments Pending</h2>
      <div className="rto-payments-table-wrap">
        <table className="rto-payments-table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>No records.</td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.application_id}>
                  <td>{r.customer_id}</td>
                  <td>{r.name ?? "—"}</td>
                  <td>{r.mobile ?? "—"}</td>
                  <td>{r.vehicle_id}</td>
                  <td>{r.chassis_num ?? "—"}</td>
                  <td>{r.application_id}</td>
                  <td>{r.register_date}</td>
                  <td>{r.rto_fees != null ? `₹${r.rto_fees}` : "—"}</td>
                  <td>{r.status ?? "Pending"}</td>
                  {showPayLink && (
                    <td>
                      <a
                        href={getVahanPayUrl(r.application_id)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rto-payments-pay-link"
                      >
                        Pay
                      </a>
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
