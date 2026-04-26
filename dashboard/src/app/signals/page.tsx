import { formatDateTime, formatDecimal } from "@/lib/format";
import { getReadClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const LIMIT = 50;

export default async function SignalsPage() {
  const supabase = getReadClient();
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
        <div className="mt-3 overflow-x-auto rounded border border-neutral-800">
          <table className="min-w-full divide-y divide-neutral-800 text-sm">
            <thead className="bg-neutral-900/60 text-xs uppercase tracking-wider text-neutral-500">
              <tr>
                <th className="px-3 py-2 text-left">Created</th>
                <th className="px-3 py-2 text-left">Symbol</th>
                <th className="px-3 py-2 text-left">Action</th>
                <th className="px-3 py-2 text-right">Confidence</th>
                <th className="px-3 py-2 text-left">Source</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-900">
              {aggregator && aggregator.length > 0 ? (
                aggregator.map((a) => (
                  <tr key={a.signal_id} className="hover:bg-neutral-900/40">
                    <td className="px-3 py-2 font-mono text-neutral-400">
                      {formatDateTime(a.created_at)}
                    </td>
                    <td className="px-3 py-2 font-mono">{a.symbol}</td>
                    <td className="px-3 py-2">
                      <ActionBadge action={a.action} />
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatDecimal(a.confidence)}
                    </td>
                    <td className="px-3 py-2 text-xs uppercase tracking-wider text-neutral-400">
                      {a.signal_source}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-neutral-500">
                    シグナルはありません
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neutral-400">
          Strategy A/B (直近 {LIMIT} 件)
        </h2>
        <div className="mt-3 overflow-x-auto rounded border border-neutral-800">
          <table className="min-w-full divide-y divide-neutral-800 text-sm">
            <thead className="bg-neutral-900/60 text-xs uppercase tracking-wider text-neutral-500">
              <tr>
                <th className="px-3 py-2 text-left">Created</th>
                <th className="px-3 py-2 text-left">Source</th>
                <th className="px-3 py-2 text-left">Symbol</th>
                <th className="px-3 py-2 text-left">Action</th>
                <th className="px-3 py-2 text-right">Confidence</th>
                <th className="px-3 py-2 text-left">Reasoning</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-900">
              {strategy && strategy.length > 0 ? (
                strategy.map((s) => (
                  <tr key={s.signal_id} className="hover:bg-neutral-900/40">
                    <td className="px-3 py-2 font-mono text-neutral-400">
                      {formatDateTime(s.created_at)}
                    </td>
                    <td className="px-3 py-2 text-xs uppercase tracking-wider text-neutral-400">
                      {s.source}
                    </td>
                    <td className="px-3 py-2 font-mono">{s.symbol}</td>
                    <td className="px-3 py-2">
                      <ActionBadge action={s.action} />
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatDecimal(s.confidence)}
                    </td>
                    <td className="max-w-md truncate px-3 py-2 text-neutral-300">
                      {s.reasoning ?? "-"}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-neutral-500">
                    シグナルはありません
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}

function ActionBadge({ action }: { action: string }) {
  const cls =
    action === "BUY"
      ? "bg-emerald-900/40 text-emerald-300"
      : action === "SELL"
        ? "bg-rose-900/40 text-rose-300"
        : "bg-neutral-800 text-neutral-300";
  return <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>{action}</span>;
}
