import type { AddSalesInvoiceRow } from "../api/addSales";
import { cell, formatCost } from "../utils/formatDisplay";

export interface SalesReportsCustomersTableProps {
  rows: AddSalesInvoiceRow[];
  loading: boolean;
  error: string | null;
}

export function SalesReportsCustomersTable({ rows, loading, error }: SalesReportsCustomersTableProps) {
  if (error) {
    return (
      <p className="view-vehicles-error" role="alert">
        {error}
      </p>
    );
  }

  if (loading && rows.length === 0) {
    return <p className="app-table-empty">Loading…</p>;
  }

  if (!loading && rows.length === 0) {
    return <p className="app-table-empty">No sales found in this period.</p>;
  }

  return (
    <div className="sales-reports-table-wrap">
      <table className="app-table add-sales-invoices-table">
        <thead>
          <tr>
            <th scope="col">Customer Name</th>
            <th scope="col">Mobile</th>
            <th scope="col">Model</th>
            <th scope="col">Invoice Date</th>
            <th scope="col">Invoice Number</th>
            <th scope="col">Insurance Policy No.</th>
            <th scope="col">CPA Policy No.</th>
            <th scope="col">Ex-Showroom</th>
            <th scope="col">Insurance Premium</th>
            <th scope="col">CPA Premium</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.sales_id}>
              <td>{cell(r.customer_name)}</td>
              <td>{cell(r.mobile)}</td>
              <td>{cell(r.model)}</td>
              <td>{cell(r.invoice_date)}</td>
              <td className="view-vehicles-mono">{cell(r.invoice_number)}</td>
              <td className="view-vehicles-mono">{cell(r.insurance_policy_num)}</td>
              <td className="view-vehicles-mono">{cell(r.cpa_policy_num)}</td>
              <td className="view-vehicles-mono">{formatCost(r.ex_showroom_amount)}</td>
              <td className="view-vehicles-mono">{formatCost(r.insurance_premium)}</td>
              <td className="view-vehicles-mono">{formatCost(r.cpa_premium)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
