"use client";

import {
  REALTIME_LISTEN_TYPES,
  REALTIME_POSTGRES_CHANGES_LISTEN_EVENT,
  REALTIME_SUBSCRIBE_STATES,
} from "@supabase/supabase-js";
import { useEffect, useState } from "react";
import { getBrowserClient } from "../supabase/client";
import { applySingletonEvent } from "./diff";
import { useRegisterChannelStatus } from "./statusContext";
import type { ChannelStatus, RowEvent } from "./types";

export interface UseRealtimeRowOptions<T> {
  channelName: string;
  table: string;
  schema?: string;
  filter?: string;
  initial: T | null;
}

export function useRealtimeRow<T extends object>(
  opts: UseRealtimeRowOptions<T>,
): { row: T | null; status: ChannelStatus } {
  const { channelName, table, schema = "public", filter, initial } = opts;
  const [row, setRow] = useState<T | null>(initial);
  const [status, setStatus] = useState<ChannelStatus>("connecting");
  useRegisterChannelStatus(channelName, status);

  useEffect(() => {
    setRow(initial);
  }, [initial]);

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
      (payload) => {
        setRow((current) => applySingletonEvent(current, payload as unknown as RowEvent<T>));
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

  return { row, status };
}
