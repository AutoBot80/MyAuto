/** Shared display formatters for sales / challan tables. */

const LATEST_RUN_TZ = "Asia/Kolkata";

export function cell(value: string | null | undefined): string {
  const s = (value ?? "").trim();
  return s || "—";
}

export function formatCost(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString("en-IN");
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function formatChallanDateDisplay(s: string | null | undefined): string {
  const t = (s || "").trim();
  if (!t) return "—";
  const iso = t.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) {
    const [, y, m, d] = iso;
    return `${d}/${m}/${y}`;
  }
  const slash = t.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (slash) {
    const d = parseInt(slash[1], 10);
    const m = parseInt(slash[2], 10);
    const y = slash[3];
    if (d >= 1 && d <= 31 && m >= 1 && m <= 12) {
      return `${pad2(d)}/${pad2(m)}/${y}`;
    }
  }
  const digitsOnly = t.replace(/\D/g, "");
  if (digitsOnly.length === 8) {
    const dd = digitsOnly.slice(0, 2);
    const mm = digitsOnly.slice(2, 4);
    const yyyy = digitsOnly.slice(4, 8);
    const d = parseInt(dd, 10);
    const m = parseInt(mm, 10);
    if (d >= 1 && d <= 31 && m >= 1 && m <= 12) {
      return `${dd}/${mm}/${yyyy}`;
    }
    const yIso = digitsOnly.slice(0, 4);
    const mmIso = digitsOnly.slice(4, 6);
    const ddIso = digitsOnly.slice(6, 8);
    const mi = parseInt(mmIso, 10);
    const di = parseInt(ddIso, 10);
    if (mi >= 1 && mi <= 12 && di >= 1 && di <= 31) {
      return `${ddIso}/${mmIso}/${yIso}`;
    }
  }
  return t;
}

export function formatLatestRunDisplay(iso: string | null | undefined): string {
  const t = (iso || "").trim();
  if (!t) return "—";
  const d = new Date(t);
  if (Number.isNaN(d.getTime())) return "—";
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: LATEST_RUN_TZ,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return fmt.format(d).replace(",", "").replace(/\s+/g, " ").trim();
}

export function formatInrAmount(n: number | null | undefined): string {
  if (n == null || Number.isNaN(Number(n))) return "—";
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 2,
    }).format(Number(n));
  } catch {
    return String(n);
  }
}

export type ChallanDiscountReductionRow = {
  add_transport_cost?: boolean;
  reduce_discount_by_percent?: number | null;
  transport_cost_per_vehicle?: number | null;
};

export function formatDiscountReductionDisplay(r: ChallanDiscountReductionRow): string {
  if (!r.add_transport_cost) return "—";
  const n = Number(r.reduce_discount_by_percent);
  if (!Number.isFinite(n)) return "—";
  const rounded = Math.round(n * 100) / 100;
  const text = Number.isInteger(rounded) ? String(rounded) : String(rounded);
  return `${text}%`;
}

export function formatCostPerVehicleDisplay(r: ChallanDiscountReductionRow): string {
  if (!r.add_transport_cost) return "—";
  const n = Number(r.transport_cost_per_vehicle);
  if (!Number.isFinite(n)) return "—";
  return formatInrAmount(n);
}

const REDUCE_DISCOUNT_EXAMPLE_BASE = 1000;

export function formatReduceDiscountExample(
  percentRaw: string,
  costRaw: string
): string | null {
  const pct = Number(percentRaw.trim());
  const cost = Number(costRaw.trim());
  if (!Number.isFinite(pct) || !Number.isFinite(cost)) return null;
  const base = REDUCE_DISCOUNT_EXAMPLE_BASE;
  const pctPart = (base * pct) / 100;
  const result = base - pctPart - cost;
  const baseFmt = formatCost(base);
  const costFmt = formatCost(Math.round(cost));
  const resultFmt = formatCost(Math.round(result * 100) / 100);
  const pctText = Number.isInteger(pct) ? String(pct) : String(pct);
  return `Discount ${baseFmt} - (${baseFmt} × ${pctText}%) - ${costFmt} = Discount of ${resultFmt}`;
}
