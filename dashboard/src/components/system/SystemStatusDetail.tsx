"use client";

import { formatDateTime, formatSignedDecimal, pnlColor } from "@/lib/format";
import { useRealtimeRow } from "@/lib/realtime/useRealtimeRow";
import type { Database } from "@contracts/generated/database.types";

type SystemStatus = Database["public"]["Tables"]["system_status"]["Row"];

export function SystemStatusDetail({ initial }: { initial: SystemStatus | null }) {
  const { row: status } = useRealtimeRow<SystemStatus>({
    channelName: "realtime:system_status",
    table: "system_status",
    filter: "id=eq.1",
    initial,
  });

  if (!status) {
    return <p className="mt-4 text-sm text-rose-400">system_status が未初期化です。</p>;
  }

  return (
    <dl className="mt-6 grid grid-cols-1 gap-3 rounded border border-neutral-800 bg-neutral-900/50 p-4 sm:grid-cols-2">
      <Row label="is_trading_allowed">
        <span
          className={
            status.is_trading_allowed
              ? "rounded bg-emerald-900/40 px-2 py-0.5 text-sm text-emerald-300"
              : "rounded bg-rose-900/40 px-2 py-0.5 text-sm text-rose-300"
          }
        >
          {status.is_trading_allowed ? "true" : "false"}
        </span>
      </Row>
      <Row label="trade_mode">
        <span className="rounded bg-neutral-800 px-2 py-0.5 text-sm uppercase">
          {status.trade_mode}
        </span>
      </Row>
      <Row label="trading_style">
        <span className="rounded bg-neutral-800 px-2 py-0.5 text-sm">{status.trading_style}</span>
      </Row>
      <Row label="updated_at">
        <span className="font-mono text-sm text-neutral-300">
          {formatDateTime(status.updated_at)}
        </span>
      </Row>

      <Row label="daily_pnl">
        <Pnl value={status.daily_pnl} />
      </Row>
      <Row label="daily_loss_limit">
        <Limit value={status.daily_loss_limit} />
      </Row>
      <Row label="weekly_pnl">
        <Pnl value={status.weekly_pnl} />
      </Row>
      <Row label="weekly_loss_limit">
        <Limit value={status.weekly_loss_limit} />
      </Row>
      <Row label="monthly_pnl">
        <Pnl value={status.monthly_pnl} />
      </Row>
      <Row label="monthly_loss_limit">
        <Limit value={status.monthly_loss_limit} />
      </Row>
    </dl>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-neutral-800/50 pb-2 last:border-0">
      <dt className="font-mono text-xs text-neutral-500">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Pnl({ value }: { value: number }) {
  return (
    <span className={`font-mono text-sm ${pnlColor(value)}`}>{formatSignedDecimal(value)}</span>
  );
}

function Limit({ value }: { value: number }) {
  return <span className="font-mono text-sm text-neutral-300">-{formatSignedDecimal(value)}</span>;
}
