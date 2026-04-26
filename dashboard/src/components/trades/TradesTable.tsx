"use client";

import { formatDateTime, formatDecimal, formatInteger } from "@/lib/format";
import { useRealtimeRows } from "@/lib/realtime/useRealtimeRows";
import type { Database } from "@contracts/generated/database.types";

type TradeLive = Database["public"]["Tables"]["trades_live"]["Row"];
type TradePaper = Database["public"]["Tables"]["trades_paper"]["Row"];
type Trade = TradeLive | TradePaper;

const byExecutedDesc = (a: Trade, b: Trade) =>
  a.executed_at < b.executed_at ? 1 : a.executed_at > b.executed_at ? -1 : 0;

export function TradesTable({
  initial,
  tradeType,
  pageSize,
}: {
  initial: Trade[];
  tradeType: "live" | "paper";
  pageSize: number;
}) {
  const table = tradeType === "live" ? "trades_live" : "trades_paper";
  const { rows: trades } = useRealtimeRows<Trade>({
    channelName: `realtime:trades:${tradeType}`,
    table,
    initial,
    getKey: (t) => t.trade_id,
    compare: byExecutedDesc,
    limit: pageSize,
  });

  return (
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
          {trades.length > 0 ? (
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
  );
}

function SideBadge({ side }: { side: string }) {
  const cls =
    side === "BUY" ? "bg-emerald-900/40 text-emerald-300" : "bg-rose-900/40 text-rose-300";
  return <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>{side}</span>;
}
