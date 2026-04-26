import type { Database } from "@contracts/generated/database.types";
import type { SupabaseClient } from "@supabase/supabase-js";
import { describe, expect, it, vi } from "vitest";
import { isTradeMode, updateKillSwitch, updateTradeMode } from "./update";

type Capture = {
  table?: string;
  payload?: Record<string, unknown>;
  eqArgs?: [string, unknown];
};

function makeClient(result: { error: { message: string } | null } = { error: null }): {
  client: SupabaseClient<Database>;
  captured: Capture;
} {
  const captured: Capture = {};
  const eq = vi.fn((column: string, value: unknown) => {
    captured.eqArgs = [column, value];
    return Promise.resolve(result);
  });
  const update = vi.fn((payload: Record<string, unknown>) => {
    captured.payload = payload;
    return { eq };
  });
  const from = vi.fn((table: string) => {
    captured.table = table;
    return { update };
  });
  const client = { from } as unknown as SupabaseClient<Database>;
  return { client, captured };
}

describe("isTradeMode", () => {
  it("accepts live and paper", () => {
    expect(isTradeMode("live")).toBe(true);
    expect(isTradeMode("paper")).toBe(true);
  });

  it("rejects other strings and non-strings", () => {
    expect(isTradeMode("LIVE")).toBe(false);
    expect(isTradeMode("")).toBe(false);
    expect(isTradeMode(null)).toBe(false);
    expect(isTradeMode(undefined)).toBe(false);
    expect(isTradeMode(0)).toBe(false);
  });
});

describe("updateKillSwitch", () => {
  it("writes is_trading_allowed and updated_at to system_status row id=1", async () => {
    const { client, captured } = makeClient();
    const result = await updateKillSwitch(client, false);
    expect(result).toEqual({ ok: true });
    expect(captured.table).toBe("system_status");
    expect(captured.eqArgs).toEqual(["id", 1]);
    expect(captured.payload?.is_trading_allowed).toBe(false);
    expect(typeof captured.payload?.updated_at).toBe("string");
  });

  it("propagates supabase errors", async () => {
    const { client } = makeClient({ error: { message: "permission denied" } });
    const result = await updateKillSwitch(client, true);
    expect(result).toEqual({ ok: false, error: "permission denied" });
  });
});

describe("updateTradeMode", () => {
  it("writes trade_mode and updated_at", async () => {
    const { client, captured } = makeClient();
    const result = await updateTradeMode(client, "paper");
    expect(result).toEqual({ ok: true });
    expect(captured.payload?.trade_mode).toBe("paper");
    expect(typeof captured.payload?.updated_at).toBe("string");
  });

  it("rejects invalid mode without hitting the client", async () => {
    const { client, captured } = makeClient();
    const result = await updateTradeMode(client, "bogus" as never);
    expect(result.ok).toBe(false);
    expect(captured.table).toBeUndefined();
  });

  it("propagates supabase errors", async () => {
    const { client } = makeClient({ error: { message: "row not found" } });
    const result = await updateTradeMode(client, "live");
    expect(result).toEqual({ ok: false, error: "row not found" });
  });
});
