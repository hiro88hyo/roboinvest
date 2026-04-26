"use client";

import { formatDateTime, formatSignedDecimal, pnlColor } from "@/lib/format";
import { useRealtimeRow } from "@/lib/realtime/useRealtimeRow";
import type { Database } from "@contracts/generated/database.types";

type SystemStatus = Database["public"]["Tables"]["system_status"]["Row"];

export function SystemStatusOverview({ initial }: { initial: SystemStatus | null }) {
  const { row: status } = useRealtimeRow<SystemStatus>({
    channelName: "realtime:system_status",
    table: "system_status",
    filter: "id=eq.1",
    initial,
  });

  if (!status) {
    return <p className="mt-6 text-sm text-rose-400">system_status が未初期化です。</p>;
  }

  return (
    <section className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-3">
      <Card label="稼働状態">
        <span
          className={
            status.is_trading_allowed
              ? "rounded bg-emerald-900/40 px-2 py-0.5 text-sm text-emerald-300"
              : "rounded bg-rose-900/40 px-2 py-0.5 text-sm text-rose-300"
          }
        >
          {status.is_trading_allowed ? "稼働中" : "停止中"}
        </span>
      </Card>
      <Card label="トレードモード">
        <span className="rounded bg-neutral-800 px-2 py-0.5 text-sm uppercase text-neutral-100">
          {status.trade_mode}
        </span>
        <span className="ml-2 text-xs text-neutral-400">{status.trading_style}</span>
      </Card>
      <Card label="最終更新">
        <span className="font-mono text-sm text-neutral-300">
          {formatDateTime(status.updated_at)}
        </span>
      </Card>

      <PnlCard label="日次 P/L" pnl={status.daily_pnl} limit={status.daily_loss_limit} />
      <PnlCard label="週次 P/L" pnl={status.weekly_pnl} limit={status.weekly_loss_limit} />
      <PnlCard label="月次 P/L" pnl={status.monthly_pnl} limit={status.monthly_loss_limit} />
    </section>
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

function PnlCard({ label, pnl, limit }: { label: string; pnl: number; limit: number }) {
  return (
    <Card label={label}>
      <span className={`font-mono text-2xl ${pnlColor(pnl)}`}>{formatSignedDecimal(pnl)}</span>
      <span className="ml-2 text-xs text-neutral-500">
        / 損失上限 -{formatSignedDecimal(limit)}
      </span>
    </Card>
  );
}
