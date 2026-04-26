import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RealtimeStatusProvider } from "./statusContext";
import { useRealtimeRow } from "./useRealtimeRow";
import { useRealtimeRows } from "./useRealtimeRows";

type ChangesCallback = (payload: unknown) => void;
type SubscribeCallback = (state: string, err?: Error) => void;

interface FakeChannel {
  on: ReturnType<typeof vi.fn>;
  subscribe: ReturnType<typeof vi.fn>;
  emit: (payload: unknown) => void;
  flipState: (state: string) => void;
}

function makeFakeChannel(): FakeChannel {
  let changesCb: ChangesCallback = () => {};
  let subscribeCb: SubscribeCallback = () => {};
  const channel = {
    on: vi.fn((_type: string, _filter: unknown, cb: ChangesCallback) => {
      changesCb = cb;
      return channel;
    }),
    subscribe: vi.fn((cb: SubscribeCallback) => {
      subscribeCb = cb;
      return channel;
    }),
    emit: (payload: unknown) => changesCb(payload),
    flipState: (state: string) => subscribeCb(state),
  } as unknown as FakeChannel;
  return channel;
}

const channels: FakeChannel[] = [];
const fakeClient = {
  channel: vi.fn((_name: string) => {
    const ch = makeFakeChannel();
    channels.push(ch);
    return ch;
  }),
  removeChannel: vi.fn(() => Promise.resolve("ok")),
};

vi.mock("../supabase/client", () => ({
  getBrowserClient: () => fakeClient,
}));

const wrapper = ({ children }: { children: ReactNode }) => (
  <RealtimeStatusProvider>{children}</RealtimeStatusProvider>
);

beforeEach(() => {
  channels.length = 0;
  fakeClient.channel.mockClear();
  fakeClient.removeChannel.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

interface Trade {
  trade_id: string;
  executed_at: string;
  price: number;
  [key: string]: unknown;
}

const trades: Trade[] = [
  { trade_id: "a", executed_at: "2026-04-26T00:00:00Z", price: 100 },
  { trade_id: "b", executed_at: "2026-04-26T01:00:00Z", price: 200 },
];

describe("useRealtimeRows", () => {
  it("subscribes on mount and exposes initial rows + connecting status", () => {
    const { result } = renderHook(
      () =>
        useRealtimeRows<Trade>({
          channelName: "trades:test",
          table: "trades_paper",
          initial: trades,
          getKey: (t) => t.trade_id,
        }),
      { wrapper },
    );
    expect(result.current.rows).toHaveLength(2);
    expect(result.current.status).toBe("connecting");
    expect(fakeClient.channel).toHaveBeenCalledWith("trades:test");
    expect(channels[0].on).toHaveBeenCalled();
    expect(channels[0].subscribe).toHaveBeenCalled();
  });

  it("transitions to connected when SUBSCRIBED is signalled", () => {
    const { result } = renderHook(
      () =>
        useRealtimeRows<Trade>({
          channelName: "trades:test",
          table: "trades_paper",
          initial: trades,
          getKey: (t) => t.trade_id,
        }),
      { wrapper },
    );
    act(() => channels[0].flipState("SUBSCRIBED"));
    expect(result.current.status).toBe("connected");
  });

  it("appends an INSERT payload to the front", () => {
    const { result } = renderHook(
      () =>
        useRealtimeRows<Trade>({
          channelName: "trades:test",
          table: "trades_paper",
          initial: trades,
          getKey: (t) => t.trade_id,
        }),
      { wrapper },
    );
    act(() => {
      channels[0].emit({
        eventType: "INSERT",
        new: { trade_id: "c", executed_at: "2026-04-26T02:00:00Z", price: 300 },
        old: {},
      });
    });
    expect(result.current.rows.map((r) => r.trade_id)).toEqual(["c", "a", "b"]);
  });

  it("dedupes an INSERT for an already-known key", () => {
    const { result } = renderHook(
      () =>
        useRealtimeRows<Trade>({
          channelName: "trades:test",
          table: "trades_paper",
          initial: trades,
          getKey: (t) => t.trade_id,
        }),
      { wrapper },
    );
    act(() => {
      channels[0].emit({
        eventType: "INSERT",
        new: { trade_id: "a", executed_at: "2026-04-26T00:00:00Z", price: 999 },
        old: {},
      });
    });
    expect(result.current.rows).toHaveLength(2);
    expect(result.current.rows.find((r) => r.trade_id === "a")?.price).toBe(999);
  });

  it("removes a row on DELETE using the partial old payload", () => {
    const { result } = renderHook(
      () =>
        useRealtimeRows<Trade>({
          channelName: "trades:test",
          table: "trades_paper",
          initial: trades,
          getKey: (t) => t.trade_id,
        }),
      { wrapper },
    );
    act(() => {
      channels[0].emit({ eventType: "DELETE", new: {}, old: { trade_id: "a" } });
    });
    expect(result.current.rows.map((r) => r.trade_id)).toEqual(["b"]);
  });

  it("removes the channel on unmount", () => {
    const { unmount } = renderHook(
      () =>
        useRealtimeRows<Trade>({
          channelName: "trades:test",
          table: "trades_paper",
          initial: trades,
          getKey: (t) => t.trade_id,
        }),
      { wrapper },
    );
    unmount();
    expect(fakeClient.removeChannel).toHaveBeenCalledTimes(1);
  });

  it("re-hydrates when initial changes (e.g. tab switch)", () => {
    const next: Trade[] = [{ trade_id: "z", executed_at: "2026-04-26T05:00:00Z", price: 500 }];
    const { result, rerender } = renderHook(
      ({ initial }: { initial: Trade[] }) =>
        useRealtimeRows<Trade>({
          channelName: "trades:test",
          table: "trades_paper",
          initial,
          getKey: (t) => t.trade_id,
        }),
      { wrapper, initialProps: { initial: trades } },
    );
    rerender({ initial: next });
    expect(result.current.rows).toEqual(next);
  });
});

interface SystemStatus {
  id: number;
  is_trading_allowed: boolean;
  trade_mode: string;
  [key: string]: unknown;
}

describe("useRealtimeRow", () => {
  it("replaces the tracked row on UPDATE", () => {
    const initial: SystemStatus = { id: 1, is_trading_allowed: true, trade_mode: "live" };
    const { result } = renderHook(
      () =>
        useRealtimeRow<SystemStatus>({
          channelName: "system_status:1",
          table: "system_status",
          filter: "id=eq.1",
          initial,
        }),
      { wrapper },
    );
    act(() => {
      channels[0].emit({
        eventType: "UPDATE",
        new: { id: 1, is_trading_allowed: false, trade_mode: "paper" },
        old: { id: 1 },
      });
    });
    expect(result.current.row?.is_trading_allowed).toBe(false);
    expect(result.current.row?.trade_mode).toBe("paper");
  });
});
