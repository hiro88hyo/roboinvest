import { describe, expect, it } from "vitest";
import { applyRowEvent, applySingletonEvent } from "./diff";
import type { RowEvent } from "./types";

interface Trade {
  trade_id: string;
  executed_at: string;
  price: number;
}

const byExecutedDesc = (a: Trade, b: Trade) =>
  a.executed_at < b.executed_at ? 1 : a.executed_at > b.executed_at ? -1 : 0;
const tradeKey = (t: Trade) => t.trade_id;

const initial: Trade[] = [
  { trade_id: "b", executed_at: "2026-04-26T01:00:00Z", price: 200 },
  { trade_id: "a", executed_at: "2026-04-26T00:00:00Z", price: 100 },
];

describe("applyRowEvent", () => {
  it("inserts a new row to the front by default", () => {
    const event: RowEvent<Trade> = {
      eventType: "INSERT",
      new: { trade_id: "c", executed_at: "2026-04-26T02:00:00Z", price: 300 },
      old: {},
    };
    const next = applyRowEvent(initial, event, tradeKey);
    expect(next.map(tradeKey)).toEqual(["c", "b", "a"]);
  });

  it("dedupes a re-delivered INSERT (at-least-once safety)", () => {
    const event: RowEvent<Trade> = {
      eventType: "INSERT",
      new: { trade_id: "b", executed_at: "2026-04-26T01:00:00Z", price: 200 },
      old: {},
    };
    const next = applyRowEvent(initial, event, tradeKey);
    expect(next).toHaveLength(2);
    expect(next.map(tradeKey)).toEqual(["b", "a"]);
  });

  it("replaces a row in place on UPDATE", () => {
    const event: RowEvent<Trade> = {
      eventType: "UPDATE",
      new: { trade_id: "a", executed_at: "2026-04-26T00:00:00Z", price: 150 },
      old: { trade_id: "a" },
    };
    const next = applyRowEvent(initial, event, tradeKey);
    expect(next.find((t) => t.trade_id === "a")?.price).toBe(150);
    expect(next.map(tradeKey)).toEqual(["b", "a"]);
  });

  it("removes a row on DELETE using the partial old payload", () => {
    const event: RowEvent<Trade> = {
      eventType: "DELETE",
      new: {},
      old: { trade_id: "b" },
    };
    const next = applyRowEvent(initial, event, tradeKey);
    expect(next.map(tradeKey)).toEqual(["a"]);
  });

  it("re-sorts after each event when compare is provided", () => {
    const event: RowEvent<Trade> = {
      eventType: "INSERT",
      new: { trade_id: "c", executed_at: "2026-04-26T00:30:00Z", price: 250 },
      old: {},
    };
    const next = applyRowEvent(initial, event, tradeKey, { compare: byExecutedDesc });
    expect(next.map(tradeKey)).toEqual(["b", "c", "a"]);
  });

  it("truncates to limit after applying", () => {
    const event: RowEvent<Trade> = {
      eventType: "INSERT",
      new: { trade_id: "c", executed_at: "2026-04-26T02:00:00Z", price: 300 },
      old: {},
    };
    const next = applyRowEvent(initial, event, tradeKey, { compare: byExecutedDesc, limit: 2 });
    expect(next.map(tradeKey)).toEqual(["c", "b"]);
  });

  it("does not mutate the input array", () => {
    const snapshot = initial.map((r) => ({ ...r }));
    applyRowEvent(
      initial,
      {
        eventType: "INSERT",
        new: { trade_id: "z", executed_at: "2026-04-26T03:00:00Z", price: 99 },
        old: {},
      },
      tradeKey,
    );
    expect(initial).toEqual(snapshot);
  });
});

describe("applySingletonEvent", () => {
  type Status = { id: number; mode: string };

  it("replaces the row on UPDATE", () => {
    const current: Status = { id: 1, mode: "live" };
    const next = applySingletonEvent(current, {
      eventType: "UPDATE",
      new: { id: 1, mode: "paper" },
      old: { id: 1 },
    });
    expect(next).toEqual({ id: 1, mode: "paper" });
  });

  it("returns the new row on INSERT when current was null", () => {
    const next = applySingletonEvent<Status>(null, {
      eventType: "INSERT",
      new: { id: 1, mode: "live" },
      old: {},
    });
    expect(next).toEqual({ id: 1, mode: "live" });
  });

  it("clears to null on DELETE", () => {
    const next = applySingletonEvent<Status>(
      { id: 1, mode: "live" },
      { eventType: "DELETE", new: {}, old: { id: 1 } },
    );
    expect(next).toBeNull();
  });
});
