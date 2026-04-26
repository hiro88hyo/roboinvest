import { TradeTypeTabs, parseTradeType } from "@/components/TradeTypeTabs";
import { formatDateTime, formatDecimal, formatInteger } from "@/lib/format";
import { getReadClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 100;

export default async function TradesPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const { type } = await searchParams;
  const tradeType = parseTradeType(type);
  const tableName = tradeType === "live" ? "trades_live" : "trades_paper";
  const supabase = getReadClient();
  const { data: trades } = await supabase
    .from(tableName)
    .select("*")
    .order("executed_at", { ascending: false })
    .limit(PAGE_SIZE);

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight">Trades</h1>
        <TradeTypeTabs basePath="/trades" current={tradeType} />
      </div>

      <p className="mt-2 text-xs text-neutral-500">直近 {PAGE_SIZE} 件</p>

      <div className="mt-4 overflow-x-auto rounded border border-neutral-800">
        <table className="min-w-full divide-y divide-neutral-800 text-sm">
          <thead className="bg-neutral-900/60 text-xs uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="px-3 py-2 text-left">Executed</th>
              <th className="px-3 py-2 text-left">Symbol</th>
              <th className="px-3 py-2 text-left">Side</th>
              <th className="px-3 py-2 text-right">Qty</th>
              <th className="px-3 py-2 text-right">Price</th>
              <th className="px-3 py-2 text-left">Source</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-900">
            {trades && trades.length > 0 ? (
              trades.map((t) => (
                <tr key={t.trade_id} className="hover:bg-neutral-900/40">
                  <td className="px-3 py-2 font-mono text-neutral-400">
                    {formatDateTime(t.executed_at)}
                  </td>
                  <td className="px-3 py-2 font-mono">{t.symbol}</td>
                  <td className="px-3 py-2">
                    <SideBadge side={t.side} />
                  </td>
                  <td className="px-3 py-2 text-right font-mono">{formatInteger(t.quantity)}</td>
                  <td className="px-3 py-2 text-right font-mono">{formatDecimal(t.price)}</td>
                  <td className="px-3 py-2 text-xs uppercase tracking-wider text-neutral-400">
                    {t.signal_source}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-neutral-500">
                  約定はありません
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </main>
  );
}

function SideBadge({ side }: { side: string }) {
  const cls =
    side === "BUY" ? "bg-emerald-900/40 text-emerald-300" : "bg-rose-900/40 text-rose-300";
  return <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>{side}</span>;
}
