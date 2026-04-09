import { useState } from "react";
import { searchVehicles, type VehicleSearchMatch } from "../api/vehicleSearch";
import { DEALER_ID } from "../api/dealerId";

interface ViewVehiclesPageProps {
  dealerId?: number;
}

function formatKeyLabel(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
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
    <article className="view-vehicles-match" aria-labelledby={`${idPrefix}-title`}>
      <h3 className="view-vehicles-match-title" id={`${idPrefix}-title`}>
        Match {index + 1}
        {m.vehicle_master.vehicle_id != null
          ? ` — vehicle_id ${m.vehicle_master.vehicle_id}`
          : m.vehicle_inventory[0]?.inventory_line_id != null
            ? ` — inventory #${m.vehicle_inventory[0].inventory_line_id}`
            : ""}
      </h3>

      <section className="view-vehicles-section" aria-labelledby={`${idPrefix}-vm`}>
        <h4 className="view-vehicles-section-title" id={`${idPrefix}-vm`}>
          Vehicle Master
        </h4>
        <KeyValueGrid data={m.vehicle_master} idPrefix={`${idPrefix}-vm`} />
      </section>

      <section className="view-vehicles-section" aria-labelledby={`${idPrefix}-vim`}>
        <h4 className="view-vehicles-section-title" id={`${idPrefix}-vim`}>
          Vehicle inventory
        </h4>
        {m.vehicle_inventory.length === 0 ? (
          <p className="view-vehicles-empty">
            No inventory lines for this dealer matching this vehicle&apos;s chassis / engine.
          </p>
        ) : (
          m.vehicle_inventory.map((row, ri) => (
            <div key={row.inventory_line_id ?? ri} className="view-vehicles-inventory-block">
              <p className="view-vehicles-inventory-line-id">
                Inventory line {row.inventory_line_id != null ? `#${row.inventory_line_id}` : ri + 1}
              </p>
              <KeyValueGrid data={row} idPrefix={`${idPrefix}-vim-${ri}`} />
            </div>
          ))
        )}
      </section>

      <section className="view-vehicles-section" aria-labelledby={`${idPrefix}-sm`}>
        <h4 className="view-vehicles-section-title" id={`${idPrefix}-sm`}>
          Sales Master
        </h4>
        {m.sales_master ? (
          <KeyValueGrid data={m.sales_master} idPrefix={`${idPrefix}-sm`} />
        ) : (
          <p className="view-vehicles-empty">No sales row for this vehicle in this OEM scope.</p>
        )}
      </section>

      <section className="view-vehicles-section" aria-labelledby={`${idPrefix}-ch`}>
        <h4 className="view-vehicles-section-title" id={`${idPrefix}-ch`}>
          Challan (inventory lines linked to this chassis / engine)
        </h4>
        {m.challans.length === 0 ? (
          <p className="view-vehicles-empty">No challan line matched this vehicle in inventory.</p>
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

      <section className="view-vehicles-section" aria-labelledby={`${idPrefix}-stg`}>
        <h4 className="view-vehicles-section-title" id={`${idPrefix}-stg`}>
          Challan staging (subdealer batch lines)
        </h4>
        {(m.challan_details_staging?.length ?? 0) === 0 ? (
          <p className="view-vehicles-empty">No staging lines matched for this search.</p>
        ) : (
          <table className="view-vehicles-table view-vehicles-table--staging">
            <thead>
              <tr>
                <th>Staging ID</th>
                <th>Batch</th>
                <th>Status</th>
                <th>Raw chassis</th>
                <th>Raw engine</th>
                <th>Inv. line</th>
                <th>Book</th>
                <th>From → To</th>
              </tr>
            </thead>
            <tbody>
              {(m.challan_details_staging ?? []).map((row, i) => (
                <tr key={`${row.challan_detail_staging_id}-${i}`}>
                  <td>{row.challan_detail_staging_id ?? "—"}</td>
                  <td className="view-vehicles-mono">{String(row.challan_batch_id ?? "—").slice(0, 8)}…</td>
                  <td>{row.status ?? "—"}</td>
                  <td className="view-vehicles-mono">{row.raw_chassis ?? "—"}</td>
                  <td className="view-vehicles-mono">{row.raw_engine ?? "—"}</td>
                  <td>{row.inventory_line_id ?? "—"}</td>
                  <td>{row.challan_book_num ?? "—"}</td>
                  <td>
                    {row.from_dealer_id ?? "—"} → {row.to_dealer_id ?? "—"}
                  </td>
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
      <p className="view-vehicles-hint">
        Search by chassis (VIN) and/or engine. Use <strong>*</strong> as a wildcard, or enter 4–6 digits alone to
        match numbers ending in those digits. Results are scoped to your dealer&apos;s OEM; each hit shows Vehicle
        Master, yard inventory, Sales Master, committed challans, and subdealer challan staging lines when they match.
      </p>
      <section className="view-vehicles-search">
        <div className="view-vehicles-search-row">
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
        <div className="view-vehicles-search-row">
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
