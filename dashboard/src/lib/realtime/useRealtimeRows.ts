"use client";

import {
  REALTIME_LISTEN_TYPES,
  REALTIME_POSTGRES_CHANGES_LISTEN_EVENT,
  REALTIME_SUBSCRIBE_STATES,
  type RealtimePostgresChangesPayload,
} from "@supabase/supabase-js";
import { useEffect, useRef, useState } from "react";
import { getBrowserClient } from "../supabase/client";
import { type ApplyRowEventOptions, applyRowEvent } from "./diff";
import { useRegisterChannelStatus } from "./statusContext";
import type { ChannelStatus, RowEvent } from "./types";

export interface UseRealtimeRowsOptions<T> extends ApplyRowEventOptions<T> {
  channelName: string;
  table: string;
  schema?: string;
  filter?: string;
  initial: T[];
  getKey: (row: T) => string;
}

export function useRealtimeRows<T extends Record<string, unknown>>(
  opts: UseRealtimeRowsOptions<T>,
): { rows: T[]; status: ChannelStatus } {
  const { channelName, table, schema = "public", filter, initial, getKey, compare, limit } = opts;
  const [rows, setRows] = useState<T[]>(initial);
  const [status, setStatus] = useState<ChannelStatus>("connecting");
  useRegisterChannelStatus(channelName, status);

  // Re-hydrate when the parent supplies a new initial array (e.g. searchParams change).
  useEffect(() => {
    setRows(initial);
  }, [initial]);

  const optsRef = useRef({ getKey, compare, limit });
  optsRef.current = { getKey, compare, limit };

  useEffect(() => {
    const supabase = getBrowserClient();
    const channel = supabase.channel(channelName);

    channel.on(
      REALTIME_LISTEN_TYPES.POSTGRES_CHANGES,
      {
        event: REALTIME_POSTGRES_CHANGES_LISTEN_EVENT.ALL,
        schema,
        table,
        ...(filter ? { filter } : {}),
      },
      (payload: RealtimePostgresChangesPayload<T>) => {
        setRows((current) =>
          applyRowEvent(current, payload as unknown as RowEvent<T>, optsRef.current.getKey, {
            compare: optsRef.current.compare,
            limit: optsRef.current.limit,
          }),
        );
      },
    );

    setStatus("connecting");
    channel.subscribe((state) => {
      switch (state) {
        case REALTIME_SUBSCRIBE_STATES.SUBSCRIBED:
          setStatus("connected");
          break;
        case REALTIME_SUBSCRIBE_STATES.CHANNEL_ERROR:
        case REALTIME_SUBSCRIBE_STATES.TIMED_OUT:
          setStatus("error");
          break;
        case REALTIME_SUBSCRIBE_STATES.CLOSED:
          setStatus("disconnected");
          break;
      }
    });

    return () => {
      void supabase.removeChannel(channel);
    };
  }, [channelName, schema, table, filter]);

  return { rows, status };
}
