import { pullAadharScanJpegsFromServer } from "../api/printRtoSidecar";
import { isElectron } from "../electron";

/**
 * Pull Aadhaar JPEGs from server to local uploads before Generate Insurance (Electron only).
 * Throws when pull fails; no-op when skipped (non-Electron, missing subfolder/dealer).
 */
export async function pullAadharScansForInsurance(
  dealerId: number,
  subfolder: string
): Promise<void> {
  const sf = (subfolder || "").trim();
  if (!isElectron() || !sf || dealerId <= 0) {
    return;
  }
  const pull = await pullAadharScanJpegsFromServer({ dealer_id: dealerId, subfolder: sf });
  if (!pull.success) {
    throw new Error(
      `Could not download Aadhaar scans — ${pull.error ?? "unknown"}`
    );
  }
}
