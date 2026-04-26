"use server";

import { getServiceClient } from "@/lib/supabase/server";
import {
  type TradeMode,
  type UpdateResult,
  updateKillSwitch,
  updateTradeMode,
} from "@/lib/system/update";
import { revalidatePath } from "next/cache";

export async function setKillSwitchAction(isTradingAllowed: boolean): Promise<UpdateResult> {
  const result = await updateKillSwitch(getServiceClient(), isTradingAllowed);
  if (result.ok) {
    revalidatePath("/system");
    revalidatePath("/");
  }
  return result;
}

export async function setTradeModeAction(mode: TradeMode): Promise<UpdateResult> {
  const result = await updateTradeMode(getServiceClient(), mode);
  if (result.ok) {
    revalidatePath("/system");
    revalidatePath("/");
  }
  return result;
}
