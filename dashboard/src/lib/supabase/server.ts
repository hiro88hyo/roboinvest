import type { Database } from "@contracts/generated/database.types";
import { createClient } from "@supabase/supabase-js";

export function getReadClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) {
    throw new Error("Missing NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY env vars");
  }
  return createClient<Database>(url, key, { auth: { persistSession: false } });
}
