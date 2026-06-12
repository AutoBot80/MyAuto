import { useEffect, useMemo, useRef, useState } from "react";
import { listSaleDocumentNames, openDocumentFileInNewTab } from "../api/customerSearch";
import { DEALER_ID } from "../api/dealerId";
import type { ConsolidatedFsArchiveContext } from "../utils/scannerArchive";

export interface UploadScansPanelProps {
  isUploading: boolean;
  uploadStatus: string;
  uploadedFiles: string[];
  /** When set with uploadedFiles, shows pre-uploaded state (e.g. from Re-Try) */
  savedTo?: string | null;
  /** Single multi-page PDF or multiple JPEG/PNG pages: pre-OCR classify/split then Textract (mobile from document). */
  onUploadConsolidated: (
    consolidatedFiles: File[],
    fsArchive?: ConsolidatedFsArchiveContext | null
  ) => Promise<void>;
  /** Reverse countdown (e.g. 00m:40s → 00m:00s) while OCR runs; omit or null to hide. */
  ocrCountdownSeconds?: number | null;
  /** For `/documents/...` links to saved scans (Add Sales). */
  dealerId?: number;
}

/** Detail sheet, then Aadhaar front, then back — only entries with a matching `uploadedFiles` name. */
function resolveIdentifiedDocumentLinks(files: readonly string[]): { label: string; filename: string }[] {
  if (!files.length) return [];
  const lowerToOriginal = new Map<string, string>();
  for (const f of files) {
    if (!f.includes("/")) lowerToOriginal.set(f.toLowerCase(), f);
  }
  const pick = (candidates: string[]): string | undefined => {
    for (const c of candidates) {
      const hit = lowerToOriginal.get(c.toLowerCase());
      if (hit) return hit;
    }
    return undefined;
  };
  const detail =
    pick(["Details.jpg", "Details.jpeg", "Sales_Detail_Sheet.pdf"]) ??
    files.find((f) => !f.includes("/") && /^details\.(jpe?g|png)$/i.test(f)) ??
    files.find((f) => !f.includes("/") && /^sales_detail_sheet\.pdf$/i.test(f));
  const front = pick(["Aadhar_front.jpg", "Aadhar_front.jpeg", "Aadhar.jpg", "Aadhar.jpeg"]);
  const back = pick(["Aadhar_back.jpg", "Aadhar_back.jpeg"]);
  const form20Cover =
    pick(["Form_20_Cover_Page.jpg", "Form_20_Cover_Page.jpeg"]) ??
    files.find((f) => !f.includes("/") && /^form_20_cover_page\.(jpe?g)$/i.test(f));
  const out: { label: string; filename: string }[] = [];
  if (detail) out.push({ label: "Detail Sheet", filename: detail });
  if (front) out.push({ label: "Aadhaar front", filename: front });
  if (back) out.push({ label: "Aadhaar back", filename: back });
  if (form20Cover) out.push({ label: "Form 20 Cover Page", filename: form20Cover });
  const unused = pick(["unused.pdf"]);
  if (unused) out.push({ label: "Unused pages", filename: unused });
  return out;
}

function form20CoverMissingWarning(files: readonly string[]): string | null {
  if (!files.length) return null;
  const hasForm20 = files.some(
    (f) => !f.includes("/") && /^form_20_cover_page\.(jpe?g)$/i.test(f)
  );
  if (hasForm20) return null;
  const hasUnused = files.some((f) => !f.includes("/") && f.toLowerCase() === "unused.pdf");
  if (hasUnused) {
    return "Form 20 Cover Page was not identified. Check unused.pdf for pages that may need a re-upload.";
  }
  return null;
}

function formatOcrCountdown(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}m:${String(s).padStart(2, "0")}s`;
}

export function UploadScansPanel({
  isUploading,
  uploadStatus,
  uploadedFiles,
  savedTo,
  onUploadConsolidated,
  ocrCountdownSeconds = null,
  dealerId,
}: UploadScansPanelProps) {
  const [selectedConsolidatedFiles, setSelectedConsolidatedFiles] = useState<File[]>([]);
  const [docOpenErr, setDocOpenErr] = useState<string | null>(null);
  const [serverFiles, setServerFiles] = useState<string[]>([]);
  const consolidatedInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!savedTo) {
      setServerFiles([]);
      return;
    }
    let cancelled = false;
    void listSaleDocumentNames(savedTo, dealerId ?? DEALER_ID)
      .then((names) => {
        if (!cancelled) setServerFiles(names);
      })
      .catch(() => {
        if (!cancelled) setServerFiles([]);
      });
    return () => {
      cancelled = true;
    };
  }, [savedTo, dealerId, isUploading]);

  const displayFiles = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const f of [...uploadedFiles, ...serverFiles]) {
      const key = f.toLowerCase();
      if (!seen.has(key)) {
        seen.add(key);
        out.push(f);
      }
    }
    return out;
  }, [uploadedFiles, serverFiles]);

  const isPreUploaded = Boolean(savedTo && displayFiles.length > 0);
  const canUploadConsolidated = selectedConsolidatedFiles.length > 0 && !isUploading;

  const identifiedDocLinks =
    savedTo && displayFiles.length > 0 ? resolveIdentifiedDocumentLinks(displayFiles) : [];
  const form20Warning =
    savedTo && displayFiles.length > 0 ? form20CoverMissingWarning(displayFiles) : null;

  return (
    <section className="app-panel">
      <div className="app-panel-title">Upload scans</div>
      {isPreUploaded ? (
        <div className="app-panel-row app-panel-pre-uploaded">
          <div className="app-panel-pre-uploaded-files">
            {displayFiles.map((f) => (
              <span key={f} className="app-panel-pre-uploaded-file">
                {f}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <>
          <p className="app-panel-hint-consolidated" role="note">
            Upload a multi-page PDF or select multiple JPEG/PNG page images (Sales Detail Sheet + Aadhaar Front + Back + optional Form 20 Cover Page)
          </p>
          <div className="app-panel-row app-panel-scan-row">
            <label className="app-panel-scan-label" htmlFor="upload-scan-consolidated">
              Consolidated Scan
            </label>
            <input
              id="upload-scan-consolidated"
              ref={consolidatedInputRef}
              type="file"
              accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
              multiple
              style={{ display: "none" }}
              onChange={(e) => {
                const files = e.target.files;
                if (files && files.length > 0) {
                  setSelectedConsolidatedFiles(Array.from(files));
                }
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className="app-button app-panel-scan-button"
              disabled={isUploading}
              onClick={() => consolidatedInputRef.current?.click()}
            >
              {selectedConsolidatedFiles.length > 0
                ? selectedConsolidatedFiles.length === 1
                  ? selectedConsolidatedFiles[0].name
                  : `${selectedConsolidatedFiles.length} files selected`
                : "Choose file(s)"}
            </button>
          </div>
          <div className="app-panel-row app-panel-actions app-panel-actions--stack">
            <button
              type="button"
              className="app-button app-button--primary"
              disabled={!canUploadConsolidated}
              onClick={() => {
                if (selectedConsolidatedFiles.length > 0) {
                  void onUploadConsolidated(selectedConsolidatedFiles, null);
                }
              }}
            >
              {isUploading ? "Uploading…" : "Upload documents"}
            </button>
            {ocrCountdownSeconds != null ? (
              <div className="app-panel-ocr-countdown" role="timer" aria-live="polite">
                {formatOcrCountdown(ocrCountdownSeconds)}
              </div>
            ) : null}
          </div>
        </>
      )}
      {savedTo && identifiedDocLinks.length > 0 ? (
        <div className="app-panel-identified-docs">
          <div className="app-panel-identified-docs-title">Identified documents</div>
          <ul className="app-panel-identified-docs-list">
            {identifiedDocLinks.map(({ label, filename }) => (
              <li key={`${label}:${filename}`}>
                <button
                  type="button"
                  className="doc-open-link"
                  onClick={() => {
                    setDocOpenErr(null);
                    void openDocumentFileInNewTab(savedTo, filename, dealerId ?? DEALER_ID).catch((e) => {
                      setDocOpenErr(e instanceof Error ? e.message : "Could not open document");
                    });
                  }}
                >
                  {label}
                </button>
                <span className="app-panel-identified-docs-filename">{filename}</span>
              </li>
            ))}
          </ul>
          {docOpenErr ? (
            <div className="app-panel-identified-docs-error" role="alert">
              {docOpenErr}
            </div>
          ) : null}
          {form20Warning ? (
            <div className="app-panel-identified-docs-error" role="note">
              {form20Warning}
            </div>
          ) : null}
        </div>
      ) : null}
      {uploadStatus ? <div className="app-panel-status">{uploadStatus}</div> : null}
    </section>
  );
}
