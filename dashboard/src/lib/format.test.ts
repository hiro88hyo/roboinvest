import { describe, expect, it } from "vitest";
import {
  formatDate,
  formatDateTime,
  formatDecimal,
  formatInteger,
  formatSignedDecimal,
  pnlColor,
} from "./format";

describe("format helpers", () => {
  it("formats UTC timestamps in JST", () => {
    // 2026-04-26T03:30:00Z → 2026-04-26 12:30:00 JST
    expect(formatDateTime("2026-04-26T03:30:00Z")).toContain("2026");
    expect(formatDateTime("2026-04-26T03:30:00Z")).toContain("12:30:00");
  });

  it("returns '-' for null/undefined/invalid", () => {
    expect(formatDateTime(null)).toBe("-");
    expect(formatDateTime(undefined)).toBe("-");
    expect(formatDateTime("not-a-date")).toBe("-");
    expect(formatDate(null)).toBe("-");
    expect(formatInteger(null)).toBe("-");
    expect(formatDecimal(null)).toBe("-");
    expect(formatSignedDecimal(null)).toBe("-");
  });

  it("formats integers with thousands separators", () => {
    expect(formatInteger(1234567)).toBe("1,234,567");
    expect(formatInteger("1000")).toBe("1,000");
  });

  it("formats signed decimals with explicit sign", () => {
    expect(formatSignedDecimal(1234.5)).toMatch(/^\+1,234\.5$/);
    expect(formatSignedDecimal(-50)).toMatch(/^-50$/);
    expect(formatSignedDecimal(0)).toMatch(/^0$/);
  });

  it("returns pnl color classes by sign", () => {
    expect(pnlColor(100)).toBe("text-emerald-400");
    expect(pnlColor(-100)).toBe("text-rose-400");
    expect(pnlColor(0)).toBe("text-neutral-400");
    expect(pnlColor(null)).toBe("text-neutral-400");
  });
});
