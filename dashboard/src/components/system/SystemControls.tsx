"use client";

import { setKillSwitchAction, setTradeModeAction } from "@/app/system/actions";
import { useRealtimeRow } from "@/lib/realtime/useRealtimeRow";
import { TRADE_MODES, type TradeMode } from "@/lib/system/update";
import type { Database } from "@contracts/generated/database.types";
import { useState, useTransition } from "react";

type SystemStatus = Database["public"]["Tables"]["system_status"]["Row"];

export function SystemControls({ initial }: { initial: SystemStatus | null }) {
  const { row: status } = useRealtimeRow<SystemStatus>({
    channelName: "realtime:system_status:controls",
    table: "system_status",
    filter: "id=eq.1",
    initial,
  });
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  if (!status) return null;

  const handleToggleKillSwitch = () => {
    const next = !status.is_trading_allowed;
    const msg = next
      ? "is_trading_allowed を true にします（取引が再開されます）。よろしいですか？"
      : "is_trading_allowed を false にします（全注文が即時停止されます）。よろしいですか？";
    if (!window.confirm(msg)) return;
    setError(null);
    startTransition(async () => {
      const result = await setKillSwitchAction(next);
      if (!result.ok) setError(result.error);
    });
  };

  const handleSetTradeMode = (mode: TradeMode) => {
    if (mode === status.trade_mode) return;
    if (mode === "live") {
      if (!window.confirm("trade_mode を live にします。実取引が再開されます。よろしいですか？")) {
        return;
      }
    }
    setError(null);
    startTransition(async () => {
      const result = await setTradeModeAction(mode);
      if (!result.ok) setError(result.error);
    });
  };

  const killSwitchOn = status.is_trading_allowed;

  return (
    <section className="mt-6 rounded border border-neutral-800 bg-neutral-900/50 p-4">
      <h2 className="text-sm font-semibold text-neutral-200">操作</h2>
      <p className="mt-1 text-xs text-neutral-500">
        書き込みは Server Action 経由（service-role キー）。Realtime で即時反映されます。
      </p>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <div className="font-mono text-xs text-neutral-500">is_trading_allowed</div>
          <div className="mt-2 flex items-center gap-3">
            <span
              className={
                killSwitchOn
                  ? "rounded bg-emerald-900/40 px-2 py-0.5 text-sm text-emerald-300"
                  : "rounded bg-rose-900/40 px-2 py-0.5 text-sm text-rose-300"
              }
            >
              {killSwitchOn ? "true" : "false"}
            </span>
            <button
              type="button"
              onClick={handleToggleKillSwitch}
              disabled={pending}
              className={`rounded px-3 py-1 text-sm transition ${
                killSwitchOn
                  ? "bg-rose-900/60 text-rose-100 hover:bg-rose-800/70"
                  : "bg-emerald-900/60 text-emerald-100 hover:bg-emerald-800/70"
              } disabled:cursor-not-allowed disabled:opacity-50`}
            >
              {killSwitchOn ? "停止する (false)" : "再開する (true)"}
            </button>
          </div>
        </div>

        <div>
          <div className="font-mono text-xs text-neutral-500">trade_mode</div>
          <div className="mt-2 inline-flex overflow-hidden rounded border border-neutral-700">
            {TRADE_MODES.map((mode) => {
              const active = status.trade_mode === mode;
              return (
                <button
                  key={mode}
                  type="button"
                  onClick={() => handleSetTradeMode(mode)}
                  disabled={pending || active}
                  className={`px-3 py-1 text-sm uppercase transition ${
                    active
                      ? mode === "live"
                        ? "bg-rose-900/60 text-rose-100"
                        : "bg-sky-900/60 text-sky-100"
                      : "bg-neutral-900 text-neutral-300 hover:bg-neutral-800"
                  } disabled:cursor-not-allowed`}
                >
                  {mode}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {error && (
        <p className="mt-3 rounded bg-rose-950/40 px-3 py-2 text-xs text-rose-300">
          エラー: {error}
        </p>
      )}
      {pending && <p className="mt-3 text-xs text-neutral-500">送信中...</p>}
    </section>
  );
}
