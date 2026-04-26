"use client";

import { type ReactNode, createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ChannelStatus } from "./types";

interface Setters {
  setChannelStatus: (channel: string, status: ChannelStatus) => void;
  removeChannel: (channel: string) => void;
}

const SettersContext = createContext<Setters | null>(null);
const ChannelsContext = createContext<Record<string, ChannelStatus>>({});

export function RealtimeStatusProvider({ children }: { children: ReactNode }) {
  const [channels, setChannels] = useState<Record<string, ChannelStatus>>({});

  const setters = useMemo<Setters>(
    () => ({
      setChannelStatus: (channel, status) =>
        setChannels((prev) => (prev[channel] === status ? prev : { ...prev, [channel]: status })),
      removeChannel: (channel) =>
        setChannels((prev) => {
          if (!(channel in prev)) return prev;
          const next = { ...prev };
          delete next[channel];
          return next;
        }),
    }),
    [],
  );

  return (
    <SettersContext.Provider value={setters}>
      <ChannelsContext.Provider value={channels}>{children}</ChannelsContext.Provider>
    </SettersContext.Provider>
  );
}

/**
 * Register a channel's status with the provider while the calling component is mounted.
 * Status updates push into the provider; unmount removes the channel from the aggregate.
 */
export function useRegisterChannelStatus(channel: string, status: ChannelStatus): void {
  const setters = useContext(SettersContext);
  const setChannelStatus = setters?.setChannelStatus;
  const removeChannel = setters?.removeChannel;

  useEffect(() => {
    if (!setChannelStatus) return;
    setChannelStatus(channel, status);
  }, [setChannelStatus, channel, status]);

  useEffect(() => {
    if (!removeChannel) return;
    return () => removeChannel(channel);
  }, [removeChannel, channel]);
}

export function useAggregatedRealtimeStatus(): ChannelStatus {
  const channels = useContext(ChannelsContext);
  return aggregateStatus(Object.values(channels));
}

export function useRealtimeChannels(): Record<string, ChannelStatus> {
  return useContext(ChannelsContext);
}

export function aggregateStatus(statuses: readonly ChannelStatus[]): ChannelStatus {
  if (statuses.length === 0) return "idle";
  if (statuses.some((s) => s === "error")) return "error";
  if (statuses.some((s) => s === "disconnected")) return "disconnected";
  if (statuses.some((s) => s === "connecting")) return "connecting";
  return "connected";
}
