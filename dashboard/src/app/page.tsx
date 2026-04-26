import { SystemStatusOverview } from "@/components/system/SystemStatusOverview";
import { getReadClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const supabase = getReadClient();

  const [{ data: status }, { count: positionsLive }, { count: positionsPaper }] = await Promise.all(
    [
      supabase.from("system_status").select("*").eq("id", 1).maybeSingle(),
      supabase
        .from("positions")
        .select("symbol", { count: "exact", head: true })
        .eq("trade_type", "live"),
      supabase
        .from("positions")
        .select("symbol", { count: "exact", head: true })
        .eq("trade_type", "paper"),
    ],
  );

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <h1 className="text-xl font-semibold tracking-tight">Overview</h1>

      <SystemStatusOverview initial={status ?? null} />

      <section className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card label="現在ポジション (live)">
          <span className="font-mono text-2xl">{positionsLive ?? 0}</span>
        </Card>
        <Card label="現在ポジション (paper)">
          <span className="font-mono text-2xl">{positionsPaper ?? 0}</span>
        </Card>
      </section>
    </main>
  );
}

function Card({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-neutral-800 bg-neutral-900/50 px-4 py-3">
      <div className="text-xs uppercase tracking-wider text-neutral-500">{label}</div>
      <div className="mt-2">{children}</div>
    </div>
  );
}
