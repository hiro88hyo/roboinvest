"use client";

import { formatDateTime, formatDecimal } from "@/lib/format";
import { useRealtimeRows } from "@/lib/realtime/useRealtimeRows";
import type { Database } from "@contracts/generated/database.types";
import { ActionBadge } from "./ActionBadge";

type StrategyLog = Database["public"]["Tables"]["strategy_logs"]["Row"];

const byCreatedDesc = (a: StrategyLog, b: StrategyLog) =>
  a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0;

export function StrategyLogsTable({
  initial,
  limit,
}: {
  initial: StrategyLog[];
  limit: number;
}) {
  const { rows } = useRealtimeRows<StrategyLog>({
    channelName: "realtime:strategy_logs",
    table: "strategy_logs",
    initial,
    getKey: (s) => s.signal_id,
    compare: byCreatedDesc,
    limit,
  });

  return (
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
          {rows.length > 0 ? (
            rows.map((s) => (
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
                <td className="px-3 py-2 text-right font-mono">{formatDecimal(s.confidence)}</td>
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
  );
}
