import { useState } from "react";

const ROWS_PER_TABLE = 20;
const TABLE_COUNT = 3;
const TOTAL_ROWS = ROWS_PER_TABLE * TABLE_COUNT;

/**
 * POS Saathi — Subdealer Challan: From/To, dealer field, scan actions, and 3×20 chassis rows (S.No. 1–60).
 */
export function SubdealerChallanPage() {
  const [fromTo, setFromTo] = useState<"from" | "to">("from");
  const [dealerSubdealer, setDealerSubdealer] = useState("");
  const [chassisValues, setChassisValues] = useState<string[]>(() => Array(TOTAL_ROWS).fill(""));

  function setChassisAt(index: number, value: string) {
    setChassisValues((prev) => {
      const next = [...prev];
      next[index] = value;
      return next;
    });
  }

  return (
    <div className="subdealer-challan">
      <div className="subdealer-challan-toolbar">
        <label className="subdealer-challan-field">
          <span className="subdealer-challan-label">From / To</span>
          <select
            className="subdealer-challan-select"
            value={fromTo}
            onChange={(e) => setFromTo(e.target.value as "from" | "to")}
            aria-label="From or To"
          >
            <option value="from">From</option>
            <option value="to">To</option>
          </select>
        </label>
        <label className="subdealer-challan-field subdealer-challan-field--dealer">
          <span className="subdealer-challan-label">Dealer/ Sub-Dealer:</span>
          <input
            type="text"
            className="subdealer-challan-input"
            value={dealerSubdealer}
            onChange={(e) => setDealerSubdealer(e.target.value)}
            autoComplete="off"
          />
        </label>
      </div>

      <div className="subdealer-challan-actions">
        <div className="subdealer-challan-actions-left">
          <button type="button" className="app-button">
            Upload Scan
          </button>
          <button type="button" className="app-button">
            From Scanner
          </button>
        </div>
        <div className="subdealer-challan-actions-right">
          <button type="button" className="app-button app-button--primary">
            Add Challans
          </button>
        </div>
      </div>

      <div className="subdealer-challan-tables" role="group" aria-label="Chassis numbers 1 to 60">
        {Array.from({ length: TABLE_COUNT }, (_, tableIdx) => {
          const start = tableIdx * ROWS_PER_TABLE;
          return (
            <table key={tableIdx} className="subdealer-challan-table">
              <thead>
                <tr>
                  <th scope="col">S.No.</th>
                  <th scope="col">Chassis No.</th>
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: ROWS_PER_TABLE }, (_, r) => {
                  const globalIdx = start + r;
                  const sno = globalIdx + 1;
                  return (
                    <tr key={globalIdx}>
                      <td className="subdealer-challan-sno">
                        {sno}.
                      </td>
                      <td>
                        <input
                          type="text"
                          className="subdealer-challan-cell-input"
                          value={chassisValues[globalIdx]}
                          onChange={(e) => setChassisAt(globalIdx, e.target.value)}
                          aria-label={`Chassis No. row ${sno}`}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          );
        })}
      </div>
    </div>
  );
}
