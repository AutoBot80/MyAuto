import { useState, useEffect } from "react";
import { listRtoPayments, type RtoPaymentRow } from "../api/rtoPaymentDetails";

export function RtoPaymentsPendingPage() {
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

  return (
    <div className="rto-payments-page">
      <h2 className="rto-payments-title">RTO Payments Pending</h2>
      <div className="rto-payments-table-wrap">
        <table className="rto-payments-table">
          <thead>
            <tr>
              <th>Customer ID</th>
              <th>Name</th>
              <th>Mobile</th>
              <th>Chassis num</th>
              <th>Application Num</th>
              <th>Date</th>
              <th>RTO Payment Due</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={8}>No records.</td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id}>
                  <td>{r.customer_id}</td>
                  <td>{r.name ?? "—"}</td>
                  <td>{r.mobile ?? "—"}</td>
                  <td>{r.chassis_num ?? "—"}</td>
                  <td>{r.application_num}</td>
                  <td>{r.submission_date}</td>
                  <td>{r.rto_payment_due != null ? `₹${r.rto_payment_due}` : "—"}</td>
                  <td>{r.status ?? "Pending"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
