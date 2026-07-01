/** Shared helpers for insurance add-on preset dropdowns (display_label, not raw id). */

export type InsuranceAddonSelectRow = {
  insurance_addon_id: number;
  display_label: string;
};

export function normalizeInsuranceAddonRows(raw: unknown): InsuranceAddonSelectRow[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((row) => {
      if (!row || typeof row !== "object") return null;
      const r = row as Record<string, unknown>;
      const insurance_addon_id = Number(r.insurance_addon_id);
      const display_label = String(r.display_label ?? "").trim();
      if (!Number.isFinite(insurance_addon_id) || insurance_addon_id <= 0 || !display_label) {
        return null;
      }
      return { insurance_addon_id, display_label };
    })
    .filter((row): row is InsuranceAddonSelectRow => row != null);
}

/** Ensure the saved selection appears in the list with a human label (not a bare id). */
export function mergeSelectedInsuranceAddonOption(
  options: InsuranceAddonSelectRow[],
  selectedId: number | "",
  labelLookup: InsuranceAddonSelectRow[] = []
): InsuranceAddonSelectRow[] {
  if (selectedId === "") return options;
  const id = Number(selectedId);
  if (!Number.isFinite(id) || id <= 0) return options;
  if (options.some((o) => o.insurance_addon_id === id)) return options;
  const hit = labelLookup.find((o) => o.insurance_addon_id === id);
  const display_label = hit?.display_label?.trim() || `Preset #${id}`;
  return [{ insurance_addon_id: id, display_label }, ...options];
}

export function insuranceAddonDisplayLabel(
  selectedId: number | "",
  options: InsuranceAddonSelectRow[]
): string {
  if (selectedId === "") return "—";
  const id = Number(selectedId);
  const hit = options.find((o) => o.insurance_addon_id === id);
  return hit?.display_label?.trim() || "—";
}
