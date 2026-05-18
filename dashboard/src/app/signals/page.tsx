import { AggregatorLogsTable } from "@/components/signals/AggregatorLogsTable";
import { StrategyLogsTable } from "@/components/signals/StrategyLogsTable";
import { getServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const LIMIT = 50;

export default async function SignalsPage() {
  const supabase = await getServerClient();
  const [{ data: aggregator }, { data: strategy }] = await Promise.all([
    supabase
      .from("aggregator_logs")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(LIMIT),
    supabase
      .from("strategy_logs")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(LIMIT),
  ]);

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <h1 className="text-xl font-semibold tracking-tight">Signals</h1>

      <section className="mt-6">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neutral-400">
          Aggregator (直近 {LIMIT} 件)
        </h2>
        <AggregatorLogsTable initial={aggregator ?? []} limit={LIMIT} />
      </section>

      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neutral-400">
          Strategy A/B (直近 {LIMIT} 件)
        </h2>
        <StrategyLogsTable initial={strategy ?? []} limit={LIMIT} />
      </section>
    </main>
  );
}
