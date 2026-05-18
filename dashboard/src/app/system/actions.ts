"use server";

import { revalidatePath } from "next/cache";
import { getServerClient } from "@/lib/supabase/server";
import {
  type TradeMode,
  type UpdateResult,
  updateKillSwitch,
  updateTradeMode,
} from "@/lib/system/update";

export async function setKillSwitchAction(isTradingAllowed: boolean): Promise<UpdateResult> {
  const supabase = await getServerClient();
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) {
    return { ok: false, error: "not authenticated" };
  }
  const result = await updateKillSwitch(supabase, isTradingAllowed);
  if (result.ok) {
    revalidatePath("/system");
    revalidatePath("/");
  }
  return result;
}

export async function setTradeModeAction(mode: TradeMode): Promise<UpdateResult> {
  const supabase = await getServerClient();
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) {
    return { ok: false, error: "not authenticated" };
  }
  const result = await updateTradeMode(supabase, mode);
  if (result.ok) {
    revalidatePath("/system");
    revalidatePath("/");
  }
  return result;
}
