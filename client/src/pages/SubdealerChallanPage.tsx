import { useState } from "react";

const ROWS_PER_TABLE = 12;
const TABLE_COUNT = 5;
const TOTAL_ROWS = ROWS_PER_TABLE * TABLE_COUNT;

/**
 * POS Saathi — Subdealer Challan: From/To, dealer field, scan actions, and 5×12 chassis rows (S.No. 1–60).
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
      <div className="subdealer-challan-top-grid">
        <label htmlFor="sdc-from-to" className="subdealer-challan-label subdealer-challan-l-from">
          From / To
        </label>
        <label htmlFor="sdc-dealer" className="subdealer-challan-label subdealer-challan-l-dealer">
          Dealer/ Sub-Dealer:
        </label>
        <select
          id="sdc-from-to"
          className="subdealer-challan-select"
          value={fromTo}
          onChange={(e) => setFromTo(e.target.value as "from" | "to")}
        >
          <option value="from">From</option>
          <option value="to">To</option>
        </select>
        <input
          id="sdc-dealer"
          type="text"
          className="subdealer-challan-input"
          value={dealerSubdealer}
          onChange={(e) => setDealerSubdealer(e.target.value)}
          autoComplete="off"
        />
        <div className="subdealer-challan-scan-btns" role="group" aria-label="Scan sources">
          <button type="button" className="app-button subdealer-challan-inline-btn">
            Upload Scan
          </button>
          <button type="button" className="app-button subdealer-challan-inline-btn">
            From Scanner
          </button>
        </div>
        <button type="button" className="app-button app-button--primary subdealer-challan-add-btn">
          Create Challans
        </button>
      </div>

      <div className="subdealer-challan-tables" role="group" aria-label="Chassis numbers 1 to 60">
        {Array.from({ length: TABLE_COUNT }, (_, tableIdx) => {
          const start = tableIdx * ROWS_PER_TABLE;
          return (
            <div key={tableIdx} className="subdealer-challan-table-wrap">
              <table className="subdealer-challan-table">
                <colgroup>
                  <col className="subdealer-challan-col-sno" />
                  <col className="subdealer-challan-col-chassis" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col" className="subdealer-challan-th-sno">
                      S.No.
                    </th>
                    <th scope="col">Chassis No.</th>
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: ROWS_PER_TABLE }, (_, r) => {
                    const globalIdx = start + r;
                    const sno = globalIdx + 1;
                    return (
                      <tr key={globalIdx}>
                        <td className="subdealer-challan-sno">{sno}.</td>
                        <td className="subdealer-challan-chassis-cell">
                          <input
                            type="text"
                            className="subdealer-challan-cell-input"
                            value={chassisValues[globalIdx]}
                            onChange={(e) => setChassisAt(globalIdx, e.target.value.toUpperCase())}
                            maxLength={17}
                            inputMode="text"
                            autoCapitalize="characters"
                            spellCheck={false}
                            size={17}
                            aria-label={`Chassis No. row ${sno}`}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
    </div>
  );
}
