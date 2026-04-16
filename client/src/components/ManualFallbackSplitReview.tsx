import { useCallback, useEffect, useRef, useState } from "react";
import { applyConsolidatedManualFallback, fetchManualSessionPageObjectUrl } from "../api/uploads";
import type { ManualFallbackPayload, UploadScansResponse } from "../types";

const ROLE_OPTIONS = [
  { value: "aadhar_front", label: "Aadhar_front.jpg" },
  { value: "aadhar_back", label: "Aadhar_back.jpg" },
  { value: "details", label: "Sales_Detail_Sheet (from page)" },
  { value: "unused", label: "Unused (append to unused.pdf)" },
] as const;

function defaultRoles(pageCount: number): string[] {
  return Array.from({ length: pageCount }, (_, i) => {
    if (i === 0) return "aadhar_front";
    if (i === 1) return "aadhar_back";
    if (i === 2) return "details";
    return "unused";
  });
}

function initialRolesFromPayload(payload: ManualFallbackPayload): string[] {
  const { page_count: pageCount, suggested_roles: sr } = payload;
  if (Array.isArray(sr) && sr.length === pageCount) {
    return sr.map((r) => (typeof r === "string" ? r : "unused"));
  }
  return defaultRoles(pageCount);
}

export interface ManualFallbackSplitReviewProps {
  payload: ManualFallbackPayload;
  dealerId: number;
  mobile: string;
  isMobileValid: boolean;
  onApplied: (savedTo: string, savedFiles: string[], extraction?: UploadScansResponse["extraction"]) => void;
  onDismiss: () => void;
}

export function ManualFallbackSplitReview({
  payload,
  dealerId,
  mobile,
  isMobileValid,
  onApplied,
  onDismiss,
}: ManualFallbackSplitReviewProps) {
  const { session_id: sessionId, page_count: pageCount } = payload;
  const [roles, setRoles] = useState<string[]>(() => initialRolesFromPayload(payload));
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const blobUrlsRef = useRef<string[]>([]);

  const suggestedRolesKey = Array.isArray(payload.suggested_roles)
    ? payload.suggested_roles.join("|")
    : "";
  useEffect(() => {
    setRoles(initialRolesFromPayload(payload));
  }, [sessionId, pageCount, suggestedRolesKey]);

  useEffect(() => {
    blobUrlsRef.current.forEach((u) => URL.revokeObjectURL(u));
    blobUrlsRef.current = [];
    let alive = true;
    void (async () => {
      const next: string[] = [];
      for (let p = 1; p <= pageCount; p++) {
        try {
          next.push(await fetchManualSessionPageObjectUrl(sessionId, p, dealerId));
        } catch {
          next.push("");
        }
      }
      if (!alive) {
        next.forEach((u) => {
          if (u) URL.revokeObjectURL(u);
        });
        return;
      }
      blobUrlsRef.current = next.filter(Boolean);
      setPreviewUrls(next);
    })();
    return () => {
      alive = false;
      blobUrlsRef.current.forEach((u) => URL.revokeObjectURL(u));
      blobUrlsRef.current = [];
    };
  }, [sessionId, pageCount, dealerId]);

  const setRole = useCallback((index: number, value: string) => {
    setRoles((prev) => {
      const next = [...prev];
      next[index] = value;
      return next;
    });
  }, []);

  async function handleApply() {
    setApplyError(null);
    if (!isMobileValid) {
      setApplyError("Enter 10-digit Customer Mobile in Section 2 first.");
      return;
    }
    if (pageCount < 3) {
      setApplyError("Need at least 3 pages to assign Aadhar front, back, and Details.");
      return;
    }
    const assignments: Record<string, string> = {};
    for (let i = 0; i < pageCount; i++) {
      assignments[String(i)] = roles[i] ?? "unused";
    }
    setApplying(true);
    try {
      const data = await applyConsolidatedManualFallback(sessionId, mobile, assignments, dealerId);
      const to = data.saved_to;
      if (!to) throw new Error("Server did not return saved_to");
      onApplied(to, data.saved_files ?? [], data.extraction);
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "Apply failed.");
    } finally {
      setApplying(false);
    }
  }

  if (pageCount < 3) {
    return (
      <div className="manual-fallback-split-review manual-fallback-split-review--error" role="alert">
        <p>Manual split has fewer than 3 pages; cannot assign Aadhar front, back, and Details.</p>
        <button type="button" className="app-button" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    );
  }

  return (
    <div className="manual-fallback-split-review">
      <h3 className="manual-fallback-split-review__title">Confirm document pages</h3>
      <p className="manual-fallback-split-review__hint">
        If the details sheet was already read, Section 2 may be partially filled. Assign each page to{" "}
        <strong>Aadhaar front</strong>, <strong>Aadhaar back</strong>, or <strong>Sales Detail Sheet</strong>, then
        press <strong>Apply document layout</strong> to run Aadhaar OCR and save scans. Fix any remaining fields,
        then <strong>Submit Info.</strong>
      </p>
      {applyError && (
        <div className="manual-fallback-split-review__error" role="alert">
          {applyError}
        </div>
      )}
      <ul className="manual-fallback-split-review__pages">
        {Array.from({ length: pageCount }, (_, i) => (
          <li key={i} className="manual-fallback-split-review__page">
            <div className="manual-fallback-split-review__thumb-wrap">
              {previewUrls[i] ? (
                <img
                  className="manual-fallback-split-review__thumb"
                  src={previewUrls[i]}
                  alt={`Page ${i + 1}`}
                />
              ) : (
                <span className="manual-fallback-split-review__thumb-ph">Loading…</span>
              )}
            </div>
            <div className="manual-fallback-split-review__assign">
              <label className="manual-fallback-split-review__label" htmlFor={`manual-page-role-${i}`}>
                Page {i + 1}
              </label>
              <select
                id={`manual-page-role-${i}`}
                className="manual-fallback-split-review__select"
                value={roles[i] ?? "unused"}
                onChange={(e) => setRole(i, e.target.value)}
              >
                {ROLE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </li>
        ))}
      </ul>
      <div className="manual-fallback-split-review__actions">
        <button
          type="button"
          className="app-button app-button--primary"
          disabled={applying || !isMobileValid}
          onClick={() => void handleApply()}
        >
          {applying ? "Applying…" : "Apply document layout"}
        </button>
        <button type="button" className="app-button" disabled={applying} onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
