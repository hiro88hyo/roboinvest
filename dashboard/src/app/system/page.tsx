import { SystemControls } from "@/components/system/SystemControls";
import { SystemStatusDetail } from "@/components/system/SystemStatusDetail";
import { getServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function SystemPage() {
  const supabase = await getServerClient();
  const { data: status } = await supabase
    .from("system_status")
    .select("*")
    .eq("id", 1)
    .maybeSingle();

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="text-xl font-semibold tracking-tight">System</h1>

      <SystemStatusDetail initial={status ?? null} />
      <SystemControls initial={status ?? null} />
    </main>
  );
}
