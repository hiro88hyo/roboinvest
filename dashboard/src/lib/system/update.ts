import type { Database } from "@contracts/generated/database.types";
import type { SupabaseClient } from "@supabase/supabase-js";

export const TRADE_MODES = ["live", "paper"] as const;
export type TradeMode = (typeof TRADE_MODES)[number];

export type UpdateResult = { ok: true } | { ok: false; error: string };

export function isTradeMode(value: unknown): value is TradeMode {
  return typeof value === "string" && (TRADE_MODES as readonly string[]).includes(value);
}

export async function updateKillSwitch(
  client: SupabaseClient<Database>,
  isTradingAllowed: boolean,
): Promise<UpdateResult> {
  const { error } = await client
    .from("system_status")
    .update({
      is_trading_allowed: isTradingAllowed,
      updated_at: new Date().toISOString(),
    })
    .eq("id", 1);
  if (error) {
    return { ok: false, error: error.message };
  }
  return { ok: true };
}

export async function updateTradeMode(
  client: SupabaseClient<Database>,
  mode: TradeMode,
): Promise<UpdateResult> {
  if (!isTradeMode(mode)) {
    return { ok: false, error: `invalid trade_mode: ${String(mode)}` };
  }
  const { error } = await client
    .from("system_status")
    .update({
      trade_mode: mode,
      updated_at: new Date().toISOString(),
    })
    .eq("id", 1);
  if (error) {
    return { ok: false, error: error.message };
  }
  return { ok: true };
}
