import { useState, useEffect } from "react";
import { listRtoPayments, payRtoPayment, type RtoPaymentRow } from "../api/rtoPaymentDetails";

interface RtoPaymentsPendingPageProps {
  /** When true, show Pay link column (only on RTO Payment Saathi tab). */
  showPayLink?: boolean;
}

export function RtoPaymentsPendingPage({ showPayLink = false }: RtoPaymentsPendingPageProps) {
  const [rows, setRows] = useState<RtoPaymentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [payingId, setPayingId] = useState<string | null>(null);

  const refreshRows = () => {
    setError(null);
    listRtoPayments()
      .then((data) => setRows(data))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
  };

  const fetchFromDb = (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError(null);
    listRtoPayments()
      .then((data) => setRows(data))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    let cancelled = false;
    fetchFromDb(true);
    const onVisible = () => {
      if (!cancelled && document.visibilityState === "visible") fetchFromDb(false);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const handlePay = async (applicationId: string) => {
    if (payingId) return;
    setPayingId(applicationId);
    try {
      await payRtoPayment(applicationId);
      refreshRows();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pay failed");
    } finally {
      setPayingId(null);
    }
  };

  if (loading && rows.length === 0) {
    return (
      <div className="app-placeholder rto-payments-page">
        <p>Loading…</p>
      </div>
    );
  }

  if (error && rows.length === 0) {
    return (
      <div className="app-placeholder rto-payments-page">
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
      {error && <p className="rto-payments-error">{error}</p>}
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
                      {r.status === "Paid" ? (
                        <span className="rto-payments-paid">{r.pay_txn_id ?? "Paid"}</span>
                      ) : (
                        <button
                          type="button"
                          className="rto-payments-pay-link"
                          disabled={payingId !== null}
                          onClick={() => handlePay(r.application_id)}
                        >
                          {payingId === r.application_id ? "Processing…" : "Pay"}
                        </button>
                      )}
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
