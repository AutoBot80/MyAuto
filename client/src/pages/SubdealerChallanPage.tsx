import { type ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import { parseSubdealerChallanScan, type SubdealerChallanLine } from "../api/subdealerChallan";

const ROWS_PER_TABLE = 13;
const TABLE_COUNT = 2;
const PAGE_SIZE = ROWS_PER_TABLE * TABLE_COUNT;

export type ChallanRow = {
  engineNo: string;
  chassisNo: string;
  status: string;
};

/**
 * POS Saathi — Subdealer Challan: From/To, dealer, upload scan (OCR), two 13-row tables per page (26 rows), pagination.
 */
export function SubdealerChallanPage() {
  const [fromTo, setFromTo] = useState<"from" | "to">("from");
  const [dealerSubdealer, setDealerSubdealer] = useState("");
  const [challanNo, setChallanNo] = useState<string | null>(null);
  const [challanDateRaw, setChallanDateRaw] = useState<string | null>(null);
  const [challanDateIso, setChallanDateIso] = useState<string | null>(null);
  const [rows, setRows] = useState<ChallanRow[]>([]);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE) || 1);
  const safePage = Math.min(page, totalPages - 1);
  const pageStart = safePage * PAGE_SIZE;
  const pageSlice = rows.slice(pageStart, pageStart + PAGE_SIZE);

  useEffect(() => {
    const tp = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    setPage((p) => Math.min(p, tp - 1));
  }, [rows.length]);

  const setRowAt = useCallback((globalIndex: number, field: keyof ChallanRow, value: string) => {
    setRows((prev) => {
      const next = [...prev];
      if (!next[globalIndex]) return prev;
      next[globalIndex] = { ...next[globalIndex], [field]: value };
      return next;
    });
  }, []);

  const onFileSelected = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setLoading(true);
    setError(null);
    setWarnings([]);
    try {
      const res = await parseSubdealerChallanScan(file);
      setChallanNo(res.challan_no);
      setChallanDateRaw(res.challan_date_raw);
      setChallanDateIso(res.challan_date_iso);
      setWarnings(res.warnings || []);
      const mapped: ChallanRow[] = (res.lines || []).map((line: SubdealerChallanLine) => ({
        engineNo: (line.engine_no || "").toUpperCase(),
        chassisNo: (line.chassis_no || "").toUpperCase(),
        status: line.status || "queued",
      }));
      setRows(mapped);
      setPage(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="subdealer-challan">
      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,application/pdf"
        className="subdealer-challan-file-input"
        aria-hidden
        tabIndex={-1}
        onChange={onFileSelected}
      />

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
          <button
            type="button"
            className="app-button subdealer-challan-inline-btn"
            disabled={loading}
            onClick={() => fileInputRef.current?.click()}
          >
            {loading ? "Processing…" : "Upload Scan"}
          </button>
          <button type="button" className="app-button subdealer-challan-inline-btn" disabled>
            From Scanner
          </button>
        </div>
        <button type="button" className="app-button app-button--primary subdealer-challan-add-btn">
          Create Challans
        </button>
      </div>

      {(challanNo || challanDateRaw || challanDateIso) && (
        <div className="subdealer-challan-extract-banner" aria-live="polite">
          {challanNo != null && challanNo !== "" && (
            <span className="subdealer-challan-extract-item">
              <span className="subdealer-challan-extract-label">Challan no.</span> {challanNo}
            </span>
          )}
          {(challanDateRaw || challanDateIso) && (
            <span className="subdealer-challan-extract-item">
              <span className="subdealer-challan-extract-label">Date</span>{" "}
              {challanDateRaw || challanDateIso}
            </span>
          )}
        </div>
      )}

      {error && (
        <div className="subdealer-challan-error" role="alert">
          {error}
        </div>
      )}
      {warnings.length > 0 && (
        <ul className="subdealer-challan-warnings">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      {rows.length > PAGE_SIZE && (
        <div className="subdealer-challan-pagination" role="navigation" aria-label="Challan rows pages">
          <button
            type="button"
            className="app-button subdealer-challan-page-btn"
            disabled={safePage <= 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </button>
          <span className="subdealer-challan-page-info">
            Page {safePage + 1} of {totalPages}
          </span>
          <button
            type="button"
            className="app-button subdealer-challan-page-btn"
            disabled={safePage >= totalPages - 1}
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
          >
            Next
          </button>
        </div>
      )}

      <div className="subdealer-challan-tables-scroll" role="region" aria-label="Challan line items">
        <div className="subdealer-challan-tables" role="group" aria-label="Engine and chassis numbers">
        {Array.from({ length: TABLE_COUNT }, (_, tableIdx) => {
          const offset = tableIdx * ROWS_PER_TABLE;
          return (
            <div key={tableIdx} className="subdealer-challan-table-wrap">
              <table className="subdealer-challan-table">
                <colgroup>
                  <col className="subdealer-challan-col-sno" />
                  <col className="subdealer-challan-col-engine" />
                  <col className="subdealer-challan-col-chassis" />
                  <col className="subdealer-challan-col-status" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col" className="subdealer-challan-th-sno">
                      S.No.
                    </th>
                    <th scope="col">Engine No.</th>
                    <th scope="col">Chassis No.</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: ROWS_PER_TABLE }, (_, r) => {
                    const slot = offset + r;
                    const globalIdx = pageStart + slot;
                    const row = pageSlice[slot];
                    const sno = globalIdx + 1;
                    return (
                      <tr key={`${safePage}-${tableIdx}-${r}`}>
                        <td className="subdealer-challan-sno">{sno}.</td>
                        <td className="subdealer-challan-engine-cell">
                          <input
                            type="text"
                            className="subdealer-challan-cell-input"
                            value={row?.engineNo ?? ""}
                            onChange={(e) =>
                              setRowAt(globalIdx, "engineNo", e.target.value.toUpperCase())
                            }
                            maxLength={32}
                            aria-label={`Engine No. row ${sno}`}
                          />
                        </td>
                        <td className="subdealer-challan-chassis-cell">
                          <input
                            type="text"
                            className="subdealer-challan-cell-input"
                            value={row?.chassisNo ?? ""}
                            onChange={(e) =>
                              setRowAt(globalIdx, "chassisNo", e.target.value.toUpperCase())
                            }
                            maxLength={32}
                            inputMode="text"
                            autoCapitalize="characters"
                            spellCheck={false}
                            aria-label={`Chassis No. row ${sno}`}
                          />
                        </td>
                        <td className="subdealer-challan-status-cell">
                          <input
                            type="text"
                            className="subdealer-challan-cell-input subdealer-challan-status-input"
                            value={row?.status ?? ""}
                            onChange={(e) => setRowAt(globalIdx, "status", e.target.value)}
                            maxLength={32}
                            aria-label={`Status row ${sno}`}
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
    </div>
  );
}
