import { TradeTypeTabs, parseTradeType } from "@/components/TradeTypeTabs";
import {
  formatDateTime,
  formatDecimal,
  formatInteger,
  formatSignedDecimal,
  pnlColor,
} from "@/lib/format";
import { getReadClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function PositionsPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const { type } = await searchParams;
  const tradeType = parseTradeType(type);
  const supabase = getReadClient();
  const { data: positions } = await supabase
    .from("positions")
    .select("*")
    .eq("trade_type", tradeType)
    .order("opened_at", { ascending: false });

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight">Positions</h1>
        <TradeTypeTabs basePath="/positions" current={tradeType} />
      </div>

      <div className="mt-6 overflow-x-auto rounded border border-neutral-800">
        <table className="min-w-full divide-y divide-neutral-800 text-sm">
          <thead className="bg-neutral-900/60 text-xs uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="px-3 py-2 text-left">Symbol</th>
              <th className="px-3 py-2 text-left">Side</th>
              <th className="px-3 py-2 text-right">Qty</th>
              <th className="px-3 py-2 text-right">Entry</th>
              <th className="px-3 py-2 text-right">Current</th>
              <th className="px-3 py-2 text-right">Unrealized P/L</th>
              <th className="px-3 py-2 text-left">Holding</th>
              <th className="px-3 py-2 text-right">Stop</th>
              <th className="px-3 py-2 text-right">Target</th>
              <th className="px-3 py-2 text-left">Opened</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-900">
            {positions && positions.length > 0 ? (
              positions.map((p) => (
                <tr key={`${p.symbol}-${p.trade_type}`} className="hover:bg-neutral-900/40">
                  <td className="px-3 py-2 font-mono">{p.symbol}</td>
                  <td className="px-3 py-2">{p.side}</td>
                  <td className="px-3 py-2 text-right font-mono">{formatInteger(p.quantity)}</td>
                  <td className="px-3 py-2 text-right font-mono">{formatDecimal(p.entry_price)}</td>
                  <td className="px-3 py-2 text-right font-mono">
                    {formatDecimal(p.current_price)}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${pnlColor(p.unrealized_pnl)}`}>
                    {formatSignedDecimal(p.unrealized_pnl)}
                  </td>
                  <td className="px-3 py-2">{p.holding_type}</td>
                  <td className="px-3 py-2 text-right font-mono">
                    {formatDecimal(p.stop_loss_price)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono">
                    {formatDecimal(p.target_price)}
                  </td>
                  <td className="px-3 py-2 font-mono text-neutral-400">
                    {formatDateTime(p.opened_at)}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={10} className="px-3 py-8 text-center text-neutral-500">
                  ポジションはありません
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </main>
  );
}
