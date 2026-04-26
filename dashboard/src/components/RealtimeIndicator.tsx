"use client";

import { useAggregatedRealtimeStatus } from "@/lib/realtime/statusContext";
import type { ChannelStatus } from "@/lib/realtime/types";

const STATUS_LABELS: Record<ChannelStatus, string> = {
  idle: "idle",
  connecting: "connecting",
  connected: "live",
  disconnected: "disconnected",
  error: "error",
};

const STATUS_DOT: Record<ChannelStatus, string> = {
  idle: "bg-neutral-500",
  connecting: "bg-amber-400 animate-pulse",
  connected: "bg-emerald-400",
  disconnected: "bg-neutral-400",
  error: "bg-rose-500",
};

const STATUS_TEXT: Record<ChannelStatus, string> = {
  idle: "text-neutral-500",
  connecting: "text-amber-300",
  connected: "text-emerald-300",
  disconnected: "text-neutral-400",
  error: "text-rose-300",
};

export function RealtimeIndicator() {
  const status = useAggregatedRealtimeStatus();
  return (
    <output
      className="inline-flex items-center gap-1.5 text-xs"
      aria-label={`realtime ${status}`}
      data-realtime-status={status}
    >
      <span className={`h-2 w-2 rounded-full ${STATUS_DOT[status]}`} />
      <span className={STATUS_TEXT[status]}>{STATUS_LABELS[status]}</span>
    </output>
  );
}
