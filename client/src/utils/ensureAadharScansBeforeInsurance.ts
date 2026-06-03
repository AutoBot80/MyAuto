import { pullAadharScanJpegsFromServer } from "../api/printRtoSidecar";
import { isElectron } from "../electron";

/**
 * Pull Aadhaar JPEGs from server to local uploads before Generate Insurance (Electron only).
 * Returns a warning message when pull fails; null when skipped or successful.
 */
export async function pullAadharScansForInsurance(
  dealerId: number,
  subfolder: string
): Promise<string | null> {
  const sf = (subfolder || "").trim();
  if (!isElectron() || !sf || dealerId <= 0) {
    return null;
  }
  const pull = await pullAadharScanJpegsFromServer({ dealer_id: dealerId, subfolder: sf });
  if (!pull.success) {
    return `Could not download Aadhaar scans — ${pull.error ?? "unknown"}`;
  }
  return null;
}
