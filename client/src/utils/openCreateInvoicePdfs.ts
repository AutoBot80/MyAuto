import { getDocumentsFolderFiles } from "../api/bulkLoads";
import { getBaseUrl } from "../api/client";
import { getAccessToken } from "../auth/token";

/** Extract ``file.pdf`` from a POSIX or Windows absolute path. */
export function basenameFromSavedPath(p: string): string | null {
  const s = (p || "").trim();
  if (!s) return null;
  const normalized = s.replace(/\\/g, "/");
  const seg = normalized.split("/").pop() || "";
  if (!seg.toLowerCase().endsWith(".pdf")) return null;
  return seg;
}

/** Form 21 / 22 / Invoice Details / Run Report PDFs saved under the sale folder after Create Invoice. */
export function isLikelyDmsSalePdf(name: string): boolean {
  const low = name.toLowerCase();
  if (!low.endsWith(".pdf")) return false;
  if (/form[\s_]*21/i.test(name)) return true;
  if (/form[\s_]*22/i.test(name)) return true;
  if (/invoice[\s_]*details/i.test(low)) return true;
  if (low.includes("gst") && low.includes("retail")) return true;
  if (low.includes("sale") && low.includes("certificate")) return true;
  return false;
}

function sortDmsPdfOpenOrder(names: string[]): string[] {
  const rank = (n: string): number => {
    const low = n.toLowerCase();
    if (/form[\s_]*21/i.test(low)) return 0;
    if (/form[\s_]*22/i.test(low)) return 1;
    if (/invoice/.test(low) && /details/.test(low)) return 2;
    if (low.includes("gst") && low.includes("retail")) return 3;
    if (low.includes("sale") && low.includes("certificate")) return 4;
    return 5;
  };
  return [...names].sort((a, b) => rank(a) - rank(b));
}

const PDF_TAB_STAGGER_MS = 150;

async function fetchPdfBlob(subfolder: string, filename: string, dealerId: number): Promise<Blob> {
  const base = getBaseUrl().replace(/\/$/, "");
  const params = new URLSearchParams({ dealer_id: String(dealerId) });
  const url = `${base}/documents/${encodeURIComponent(subfolder)}/${encodeURIComponent(filename)}?${params}`;
  const headers = new Headers();
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(url, { headers });
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error(t.trim() || `HTTP ${res.status}`);
  }
  return res.blob();
}

function openBlobInNewTab(blob: Blob): void {
  const objUrl = URL.createObjectURL(blob);
  const w = window.open(objUrl, "_blank", "noopener,noreferrer");
  if (!w) {
    URL.revokeObjectURL(objUrl);
    throw new Error("Popup blocked — allow popups for this site to view PDFs.");
  }
  window.setTimeout(() => URL.revokeObjectURL(objUrl), 120_000);
}

/**
 * Open DMS sale-folder PDFs in new browser tabs (fetches via ``/documents/...``).
 *
 * **Add Sales** does not call this after Create Invoice — multiple tabs and popup blockers
 * were disruptive; PDFs remain under Uploaded scans and **Print Forms & Queue RTO** handles printing.
 * Keep this helper for a future explicit “Open PDFs” action or other pages if needed.
 */
export async function openCreateInvoicePdfsInBrowser(
  subfolder: string,
  dealerId: number,
  pdfsSaved: string[]
): Promise<{ opened: number; candidateCount: number; hint?: string }> {
  let files: { name: string }[] = [];
  try {
    const res = await getDocumentsFolderFiles(subfolder, dealerId);
    files = res.files ?? [];
  } catch {
    return { opened: 0, candidateCount: 0, hint: "could not list sale folder files" };
  }

  const onDisk = new Set(files.map((f) => f.name));
  const want = new Set<string>();

  for (const p of pdfsSaved) {
    const b = basenameFromSavedPath(p);
    if (b && onDisk.has(b)) want.add(b);
  }
  for (const f of files) {
    if (isLikelyDmsSalePdf(f.name)) want.add(f.name);
  }

  const ordered = sortDmsPdfOpenOrder([...want]);
  const candidateCount = ordered.length;
  if (candidateCount === 0) {
    return { opened: 0, candidateCount: 0, hint: "no DMS PDFs found in folder yet" };
  }

  const blobs = await Promise.all(
    ordered.map((name) =>
      fetchPdfBlob(subfolder, name, dealerId).catch(() => null)
    )
  );

  let opened = 0;
  for (let i = 0; i < ordered.length; i++) {
    const blob = blobs[i];
    if (!blob) {
      continue;
    }
    try {
      openBlobInNewTab(blob);
      opened++;
      if (i < ordered.length - 1) {
        await new Promise((r) => setTimeout(r, PDF_TAB_STAGGER_MS));
      }
    } catch {
      /* try next */
    }
  }

  return {
    opened,
    candidateCount,
    hint:
      opened === 0
        ? "allow popups or open PDFs from the sale folder"
        : opened < candidateCount
          ? "some PDFs could not open (popup blocker or missing file)"
          : undefined,
  };
}
