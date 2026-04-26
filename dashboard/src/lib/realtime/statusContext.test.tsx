import { act, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  RealtimeStatusProvider,
  aggregateStatus,
  useAggregatedRealtimeStatus,
  useRegisterChannelStatus,
} from "./statusContext";
import type { ChannelStatus } from "./types";

describe("aggregateStatus", () => {
  it("returns idle when no channels are registered", () => {
    expect(aggregateStatus([])).toBe("idle");
  });

  it("returns error when any channel is in error", () => {
    expect(aggregateStatus(["connected", "error", "connecting"])).toBe("error");
  });

  it("returns disconnected when no error but any disconnected", () => {
    expect(aggregateStatus(["connected", "disconnected"])).toBe("disconnected");
  });

  it("returns connecting when no error/disconnected but any connecting", () => {
    expect(aggregateStatus(["connected", "connecting"])).toBe("connecting");
  });

  it("returns connected when all are connected", () => {
    expect(aggregateStatus(["connected", "connected"])).toBe("connected");
  });
});

describe("RealtimeStatusProvider", () => {
  function Probe({
    channel,
    status,
    sink,
  }: {
    channel: string;
    status: ChannelStatus;
    sink: (s: ChannelStatus) => void;
  }) {
    useRegisterChannelStatus(channel, status);
    sink(useAggregatedRealtimeStatus());
    return null;
  }

  it("aggregates registered channel statuses", () => {
    const seen: ChannelStatus[] = [];
    const sink = (s: ChannelStatus) => seen.push(s);

    const { rerender } = render(
      <RealtimeStatusProvider>
        <Probe channel="a" status="connecting" sink={sink} />
        <Probe channel="b" status="connecting" sink={sink} />
      </RealtimeStatusProvider>,
    );

    expect(seen.at(-1)).toBe("connecting");

    act(() => {
      rerender(
        <RealtimeStatusProvider>
          <Probe channel="a" status="connected" sink={sink} />
          <Probe channel="b" status="connected" sink={sink} />
        </RealtimeStatusProvider>,
      );
    });

    expect(seen.at(-1)).toBe("connected");

    act(() => {
      rerender(
        <RealtimeStatusProvider>
          <Probe channel="a" status="connected" sink={sink} />
          <Probe channel="b" status="error" sink={sink} />
        </RealtimeStatusProvider>,
      );
    });

    expect(seen.at(-1)).toBe("error");
  });
});
