"use client";

import type { Database } from "@contracts/generated/database.types";
import { formatDateTime, formatDecimal } from "@/lib/format";
import { useRealtimeRows } from "@/lib/realtime/useRealtimeRows";
import { ActionBadge } from "./ActionBadge";

type AggregatorLog = Database["public"]["Tables"]["aggregator_logs"]["Row"];

const byCreatedDesc = (a: AggregatorLog, b: AggregatorLog) =>
  a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0;

export function AggregatorLogsTable({
  initial,
  limit,
}: {
  initial: AggregatorLog[];
  limit: number;
}) {
  const { rows } = useRealtimeRows<AggregatorLog>({
    channelName: "realtime:aggregator_logs",
    table: "aggregator_logs",
    initial,
    getKey: (a) => a.signal_id,
    compare: byCreatedDesc,
    limit,
  });

  return (
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
          {rows.length > 0 ? (
            rows.map((a) => (
              <tr key={a.signal_id} className="hover:bg-neutral-900/40">
                <td className="px-3 py-2 font-mono text-neutral-400">
                  {formatDateTime(a.created_at)}
                </td>
                <td className="px-3 py-2 font-mono">{a.symbol}</td>
                <td className="px-3 py-2">
                  <ActionBadge action={a.action} />
                </td>
                <td className="px-3 py-2 text-right font-mono">{formatDecimal(a.confidence)}</td>
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
  );
}
