/** IST calendar helpers for Sales Reports date presets (dd-mm-yyyy). */

const IST_TZ = "Asia/Kolkata";

export type SalesReportsDatePreset =
  | "current_month"
  | "previous_month"
  | "current_fy"
  | "previous_fy";

export const DEFAULT_SALES_REPORTS_PRESET: SalesReportsDatePreset = "current_month";

function istCalendarParts(ref: Date = new Date()): { y: number; m: number; d: number } {
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: IST_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const parts = fmt.formatToParts(ref);
  const y = Number(parts.find((p) => p.type === "year")?.value ?? "0");
  const m = Number(parts.find((p) => p.type === "month")?.value ?? "0");
  const d = Number(parts.find((p) => p.type === "day")?.value ?? "0");
  return { y, m, d };
}

function dateFromParts(y: number, m: number, d: number): Date {
  return new Date(Date.UTC(y, m - 1, d));
}

function addDaysUtc(d: Date, days: number): Date {
  const out = new Date(d.getTime());
  out.setUTCDate(out.getUTCDate() + days);
  return out;
}

export function formatDdMmYyyy(d: Date): string {
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const yyyy = d.getUTCFullYear();
  return `${dd}-${mm}-${yyyy}`;
}

export function parseDdMmYyyy(s: string): Date | null {
  const t = s.trim();
  const m = /^(\d{2})-(\d{2})-(\d{4})$/.exec(t);
  if (!m) return null;
  const d = Number(m[1]);
  const mo = Number(m[2]);
  const y = Number(m[3]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
  return dateFromParts(y, mo, d);
}

export function istToday(): Date {
  const { y, m, d } = istCalendarParts();
  return dateFromParts(y, m, d);
}

export function istYesterday(): Date {
  return addDaysUtc(istToday(), -1);
}

function currentFyStart(y: number, m: number): Date {
  if (m >= 4) return dateFromParts(y, 4, 1);
  return dateFromParts(y - 1, 4, 1);
}

export function presetDateRange(preset: SalesReportsDatePreset): { from: string; to: string } {
  const { y, m } = istCalendarParts();
  const yesterday = istYesterday();

  switch (preset) {
    case "current_month": {
      const from = dateFromParts(y, m, 1);
      return { from: formatDdMmYyyy(from), to: formatDdMmYyyy(yesterday) };
    }
    case "previous_month": {
      const firstThis = dateFromParts(y, m, 1);
      const endPrev = addDaysUtc(firstThis, -1);
      const ep = {
        y: endPrev.getUTCFullYear(),
        m: endPrev.getUTCMonth() + 1,
      };
      const from = dateFromParts(ep.y, ep.m, 1);
      return { from: formatDdMmYyyy(from), to: formatDdMmYyyy(endPrev) };
    }
    case "current_fy": {
      const from = currentFyStart(y, m);
      return { from: formatDdMmYyyy(from), to: formatDdMmYyyy(yesterday) };
    }
    case "previous_fy": {
      const currStart = currentFyStart(y, m);
      const from = dateFromParts(currStart.getUTCFullYear() - 1, 4, 1);
      const endPrev = addDaysUtc(currStart, -1);
      return { from: formatDdMmYyyy(from), to: formatDdMmYyyy(endPrev) };
    }
    default: {
      const from = dateFromParts(y, m, 1);
      return { from: formatDdMmYyyy(from), to: formatDdMmYyyy(yesterday) };
    }
  }
}

export function defaultSalesReportsDateRange(): { from: string; to: string; preset: SalesReportsDatePreset } {
  const preset = DEFAULT_SALES_REPORTS_PRESET;
  const range = presetDateRange(preset);
  return { ...range, preset };
}
