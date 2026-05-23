const IST = "Asia/Kolkata";

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

/** ``billing_date`` etc. → ``23-May-2026`` in Asia/Kolkata (IST). */
export function formatDdMmmYyyyIst(value: unknown): string {
  if (value == null) return "—";
  const s = String(value).trim();
  if (!s) return "—";
  const ms = Date.parse(s);
  if (Number.isNaN(ms)) return "—";
  const d = new Date(ms);
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: IST,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const parts = fmt.formatToParts(d);
  const get = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((p) => p.type === type)?.value ?? "";
  const day = get("day");
  const monthNum = parseInt(get("month"), 10);
  const year = get("year");
  if (!day || monthNum < 1 || monthNum > 12 || !year) return "—";
  return `${day}-${MONTHS[monthNum - 1]}-${year}`;
}
