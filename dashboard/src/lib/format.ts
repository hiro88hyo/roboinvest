const TIMEZONE = process.env.NEXT_PUBLIC_APP_TIMEZONE ?? "Asia/Tokyo";

const dateTimeFormatter = new Intl.DateTimeFormat("ja-JP", {
  timeZone: TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const dateFormatter = new Intl.DateTimeFormat("ja-JP", {
  timeZone: TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const integerFormatter = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 0 });

const decimalFormatter = new Intl.NumberFormat("ja-JP", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

const signedDecimalFormatter = new Intl.NumberFormat("ja-JP", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});

export function formatDateTime(value: string | Date | null | undefined): string {
  if (value == null) return "-";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "-";
  return dateTimeFormatter.format(d);
}

export function formatDate(value: string | Date | null | undefined): string {
  if (value == null) return "-";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "-";
  return dateFormatter.format(d);
}

export function formatInteger(value: number | string | null | undefined): string {
  if (value == null) return "-";
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return "-";
  return integerFormatter.format(n);
}

export function formatDecimal(value: number | string | null | undefined): string {
  if (value == null) return "-";
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return "-";
  return decimalFormatter.format(n);
}

export function formatSignedDecimal(value: number | string | null | undefined): string {
  if (value == null) return "-";
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return "-";
  return signedDecimalFormatter.format(n);
}

export function pnlColor(value: number | string | null | undefined): string {
  if (value == null) return "text-neutral-400";
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n) || n === 0) return "text-neutral-400";
  return n > 0 ? "text-emerald-400" : "text-rose-400";
}
