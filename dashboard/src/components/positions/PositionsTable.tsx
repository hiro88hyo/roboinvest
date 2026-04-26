"use client";

import {
  formatDateTime,
  formatDecimal,
  formatInteger,
  formatSignedDecimal,
  pnlColor,
} from "@/lib/format";
import { useRealtimeRows } from "@/lib/realtime/useRealtimeRows";
import type { Database } from "@contracts/generated/database.types";

type Position = Database["public"]["Tables"]["positions"]["Row"];

const byOpenedDesc = (a: Position, b: Position) =>
  a.opened_at < b.opened_at ? 1 : a.opened_at > b.opened_at ? -1 : 0;

export function PositionsTable({
  initial,
  tradeType,
}: {
  initial: Position[];
  tradeType: "live" | "paper";
}) {
  const { rows: positions } = useRealtimeRows<Position>({
    channelName: `realtime:positions:${tradeType}`,
    table: "positions",
    filter: `trade_type=eq.${tradeType}`,
    initial,
    getKey: (p) => `${p.symbol}:${p.trade_type}`,
    compare: byOpenedDesc,
  });

  return (
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
          {positions.length > 0 ? (
            positions.map((p) => (
              <tr key={`${p.symbol}-${p.trade_type}`} className="hover:bg-neutral-900/40">
                <td className="px-3 py-2 font-mono">{p.symbol}</td>
                <td className="px-3 py-2">{p.side}</td>
                <td className="px-3 py-2 text-right font-mono">{formatInteger(p.quantity)}</td>
                <td className="px-3 py-2 text-right font-mono">{formatDecimal(p.entry_price)}</td>
                <td className="px-3 py-2 text-right font-mono">{formatDecimal(p.current_price)}</td>
                <td className={`px-3 py-2 text-right font-mono ${pnlColor(p.unrealized_pnl)}`}>
                  {formatSignedDecimal(p.unrealized_pnl)}
                </td>
                <td className="px-3 py-2">{p.holding_type}</td>
                <td className="px-3 py-2 text-right font-mono">
                  {formatDecimal(p.stop_loss_price)}
                </td>
                <td className="px-3 py-2 text-right font-mono">{formatDecimal(p.target_price)}</td>
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
  );
}
