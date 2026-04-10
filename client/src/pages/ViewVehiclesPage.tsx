import { useState } from "react";
import { searchVehicles, type VehicleSearchMatch } from "../api/vehicleSearch";
import { DEALER_ID } from "../api/dealerId";

interface ViewVehiclesPageProps {
  dealerId?: number;
}

/** vehicle_master API keys in display order (left → right, then wrap). */
const VEHICLE_MASTER_FIELDS: { key: string; label: string }[] = [
  { key: "chassis", label: "CHASSIS" },
  { key: "engine", label: "Engine" },
  { key: "battery", label: "Battery" },
  { key: "key_num", label: "Key" },
  { key: "model", label: "Model" },
  { key: "colour", label: "Colour" },
  { key: "year_of_mfg", label: "YEAR OF MFG" },
  { key: "place_of_registeration", label: "Place of registration" },
  { key: "plate_num", label: "Plate Number" },
];

const SALES_MASTER_FIELDS: { key: string; label: string }[] = [
  { key: "order_number", label: "Order" },
  { key: "dealer_name", label: "Dealer Name" },
  { key: "billing_date", label: "Billing Date" },
  { key: "invoice_number", label: "Invoice Number" },
  { key: "customer_name", label: "Customer Name" },
  { key: "customer_address", label: "Customer Address" },
  { key: "customer_mobile", label: "Customer Mobile" },
  { key: "alt_phone_num", label: "Alt phone num" },
  { key: "financier_name", label: "Financier name" },
];

function formatKeyLabel(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function vehicleMasterValue(
  data: Record<string, string | number | null | undefined>,
  key: string
): string {
  const direct = data[key];
  if (direct != null && String(direct).trim() !== "") return String(direct);
  if (key === "chassis" && data.chassis_no != null && String(data.chassis_no).trim() !== "") {
    return String(data.chassis_no);
  }
  if (key === "engine" && data.engine_no != null && String(data.engine_no).trim() !== "") {
    return String(data.engine_no);
  }
  if (key === "key_num" && data.raw_key_num != null && String(data.raw_key_num).trim() !== "") {
    return String(data.raw_key_num);
  }
  return "—";
}

function VehicleMasterFieldGrid({
  data,
  idPrefix,
}: {
  data: Record<string, string | number | null | undefined>;
  idPrefix: string;
}) {
  return (
    <div className="view-vehicles-kv-grid view-vehicles-kv-grid--ordered">
      {VEHICLE_MASTER_FIELDS.map(({ key, label }) => (
        <div key={key} className="view-vehicles-kv-item">
          <span className="view-vehicles-kv-label" id={`${idPrefix}-${key}-l`}>
            {label}
          </span>
          <span className="view-vehicles-kv-value" aria-labelledby={`${idPrefix}-${key}-l`}>
            {vehicleMasterValue(data, key)}
          </span>
        </div>
      ))}
    </div>
  );
}

function salesMasterValue(data: Record<string, string | number | null | undefined>, key: string): string {
  const v = data[key];
  if (v != null && String(v).trim() !== "") return String(v);
  return "—";
}

function SalesFieldGrid({
  data,
  idPrefix,
}: {
  data: Record<string, string | number | null | undefined>;
  idPrefix: string;
}) {
  return (
    <div className="view-vehicles-kv-grid view-vehicles-kv-grid--ordered">
      {SALES_MASTER_FIELDS.map(({ key, label }) => (
        <div key={key} className="view-vehicles-kv-item">
          <span className="view-vehicles-kv-label" id={`${idPrefix}-${key}-l`}>
            {label}
          </span>
          <span className="view-vehicles-kv-value" aria-labelledby={`${idPrefix}-${key}-l`}>
            {salesMasterValue(data, key)}
          </span>
        </div>
      ))}
    </div>
  );
}

function KeyValueGrid({ data, idPrefix }: { data: Record<string, string | number | null>; idPrefix: string }) {
  const keys = Object.keys(data).sort();
  return (
    <div className="view-vehicles-kv-grid">
      {keys.map((k) => (
        <div key={k} className="view-vehicles-kv-item">
          <span className="view-vehicles-kv-label" id={`${idPrefix}-${k}-l`}>
            {formatKeyLabel(k)}
          </span>
          <span className="view-vehicles-kv-value" aria-labelledby={`${idPrefix}-${k}-l`}>
            {data[k] == null || data[k] === "" ? "—" : String(data[k])}
          </span>
        </div>
      ))}
    </div>
  );
}

function MatchBlock({ m, index }: { m: VehicleSearchMatch; index: number }) {
  const idPrefix = `vv-${index}`;
  return (
    <article className="view-vehicles-match">
      <section className="view-vehicles-section" aria-labelledby={`${idPrefix}-vm`}>
        <h4 className="view-vehicles-section-title" id={`${idPrefix}-vm`}>
          Vehicle Master
        </h4>
        <VehicleMasterFieldGrid data={m.vehicle_master} idPrefix={`${idPrefix}-vm`} />
      </section>

      <section className="view-vehicles-section" aria-labelledby={`${idPrefix}-sm`}>
        <h4 className="view-vehicles-section-title" id={`${idPrefix}-sm`}>
          Sales Master
        </h4>
        {m.sales_master ? (
          <SalesFieldGrid data={m.sales_master} idPrefix={`${idPrefix}-sm`} />
        ) : (
          <p className="view-vehicles-empty">No sales row for this vehicle.</p>
        )}
      </section>

      <section className="view-vehicles-section" aria-labelledby={`${idPrefix}-vim`}>
        <h4 className="view-vehicles-section-title" id={`${idPrefix}-vim`}>
          Vehicle Inventory
        </h4>
        {m.vehicle_inventory.length === 0 ? (
          <p className="view-vehicles-empty">
            No inventory lines matching this vehicle&apos;s chassis / engine (all dealers).
          </p>
        ) : (
          m.vehicle_inventory.map((row, ri) => (
            <div key={row.inventory_line_id ?? ri} className="view-vehicles-inventory-block">
              <p className="view-vehicles-inventory-line-id">
                Inventory line {row.inventory_line_id != null ? `#${row.inventory_line_id}` : ri + 1}
                {row.dealer_id != null ? ` · dealer_id ${row.dealer_id}` : ""}
              </p>
              <KeyValueGrid data={row} idPrefix={`${idPrefix}-vim-${ri}`} />
            </div>
          ))
        )}
      </section>

      <section className="view-vehicles-section" aria-labelledby={`${idPrefix}-ch`}>
        <h4 className="view-vehicles-section-title" id={`${idPrefix}-ch`}>
          Challan Master
        </h4>
        {m.challans.length === 0 ? (
          <p className="view-vehicles-empty">No committed challan lines for this chassis / engine in inventory.</p>
        ) : (
          <table className="view-vehicles-table">
            <thead>
              <tr>
                <th>Challan ID</th>
                <th>Date</th>
                <th>Book</th>
                <th>From → To</th>
                <th>Order #</th>
                <th>Invoice #</th>
                <th>Inv. chassis</th>
                <th>Inv. engine</th>
              </tr>
            </thead>
            <tbody>
              {m.challans.map((row, i) => (
                <tr key={`${row.challan_id}-${row.inventory_line_id}-${i}`}>
                  <td>{row.challan_id ?? "—"}</td>
                  <td>{row.challan_date ?? "—"}</td>
                  <td>{row.challan_book_num ?? "—"}</td>
                  <td>
                    {row.dealer_from ?? "—"} → {row.dealer_to ?? "—"}
                  </td>
                  <td>{row.order_number ?? "—"}</td>
                  <td>{row.invoice_number ?? "—"}</td>
                  <td className="view-vehicles-mono">{row.chassis_no ?? "—"}</td>
                  <td className="view-vehicles-mono">{row.engine_no ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </article>
  );
}

export function ViewVehiclesPage({ dealerId = DEALER_ID }: ViewVehiclesPageProps) {
  const [chassis, setChassis] = useState("");
  const [engine, setEngine] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [matches, setMatches] = useState<VehicleSearchMatch[]>([]);
  const [searched, setSearched] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const handleSearch = async () => {
    const c = chassis.trim();
    const e = engine.trim();
    if (!c && !e) {
      setError("Enter chassis and/or engine (use * for wildcards; 4–6 digits alone match as a suffix).");
      return;
    }
    setError(null);
    setMessage(null);
    setLoading(true);
    setSearched(true);
    setMatches([]);
    try {
      const res = await searchVehicles({ chassis: c || null, engine: e || null, dealer_id: dealerId });
      setMatches(res.matches);
      if (!res.found && res.message) setMessage(res.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="view-vehicles-page">
      <section className="view-vehicles-search">
        <div className="view-vehicles-search-field">
          <label htmlFor="vv-chassis">Chassis / VIN</label>
          <input
            id="vv-chassis"
            type="text"
            autoComplete="off"
            placeholder="e.g. MB* or last 5 digits"
            value={chassis}
            onChange={(e) => setChassis(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <div className="view-vehicles-search-field">
          <label htmlFor="vv-engine">Engine</label>
          <input
            id="vv-engine"
            type="text"
            autoComplete="off"
            placeholder="e.g. *12345 or partial"
            value={engine}
            onChange={(e) => setEngine(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <button
          type="button"
          className="app-button app-button--primary view-vehicles-search-btn"
          onClick={handleSearch}
          disabled={loading}
        >
          {loading ? "Searching…" : "Search"}
        </button>
      </section>

      {error && <p className="view-vehicles-error">{error}</p>}
      {message && <p className="view-vehicles-message">{message}</p>}

      {searched && !loading && matches.length === 0 && !error && !message && (
        <p className="view-vehicles-message">No matches.</p>
      )}

      {matches.map((m, i) => (
        <MatchBlock
          key={m.vehicle_master.vehicle_id ?? m.vehicle_inventory[0]?.inventory_line_id ?? i}
          m={m}
          index={i}
        />
      ))}
    </div>
  );
}
