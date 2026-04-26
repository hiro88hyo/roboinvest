import type { Database } from "@contracts/generated/database.types";
import { act, render, screen } from "@testing-library/react";
import { type MockInstance, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type SystemStatus = Database["public"]["Tables"]["system_status"]["Row"];

const setKillSwitchAction = vi.fn();
const setTradeModeAction = vi.fn();

vi.mock("@/app/system/actions", () => ({
  setKillSwitchAction: (...args: unknown[]) => setKillSwitchAction(...args),
  setTradeModeAction: (...args: unknown[]) => setTradeModeAction(...args),
}));

vi.mock("@/lib/realtime/useRealtimeRow", () => ({
  useRealtimeRow: <T,>({ initial }: { initial: T | null }) => ({
    row: initial,
    status: "connected" as const,
  }),
}));

import { SystemControls } from "./SystemControls";

function makeStatus(overrides: Partial<SystemStatus> = {}): SystemStatus {
  return {
    id: 1,
    is_trading_allowed: true,
    trade_mode: "paper",
    trading_style: "day",
    daily_pnl: 0,
    weekly_pnl: 0,
    monthly_pnl: 0,
    daily_loss_limit: 100000,
    weekly_loss_limit: 300000,
    monthly_loss_limit: 1000000,
    updated_at: "2026-04-26T00:00:00Z",
    ...overrides,
  };
}

describe("SystemControls", () => {
  let confirmSpy: MockInstance<typeof window.confirm>;

  beforeEach(() => {
    setKillSwitchAction.mockReset();
    setTradeModeAction.mockReset();
    setKillSwitchAction.mockResolvedValue({ ok: true });
    setTradeModeAction.mockResolvedValue({ ok: true });
    confirmSpy = vi.spyOn(window, "confirm");
  });

  afterEach(() => {
    confirmSpy.mockRestore();
  });

  it("renders nothing when status is missing", () => {
    const { container } = render(<SystemControls initial={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("calls setKillSwitchAction with negated value after confirm", async () => {
    confirmSpy.mockReturnValue(true);
    render(<SystemControls initial={makeStatus({ is_trading_allowed: true })} />);
    const btn = screen.getByRole("button", { name: /停止する/ });
    await act(async () => {
      btn.click();
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(setKillSwitchAction).toHaveBeenCalledWith(false);
  });

  it("does not call action when user cancels confirm", async () => {
    confirmSpy.mockReturnValue(false);
    render(<SystemControls initial={makeStatus({ is_trading_allowed: false })} />);
    const btn = screen.getByRole("button", { name: /再開する/ });
    await act(async () => {
      btn.click();
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(setKillSwitchAction).not.toHaveBeenCalled();
  });

  it("switches to paper without confirm", async () => {
    render(<SystemControls initial={makeStatus({ trade_mode: "live" })} />);
    const paperBtn = screen.getByRole("button", { name: /paper/i });
    await act(async () => {
      paperBtn.click();
    });
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(setTradeModeAction).toHaveBeenCalledWith("paper");
  });

  it("requires confirmation when switching to live", async () => {
    confirmSpy.mockReturnValue(false);
    render(<SystemControls initial={makeStatus({ trade_mode: "paper" })} />);
    const liveBtn = screen.getByRole("button", { name: /live/i });
    await act(async () => {
      liveBtn.click();
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(setTradeModeAction).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    await act(async () => {
      liveBtn.click();
    });
    expect(setTradeModeAction).toHaveBeenCalledWith("live");
  });

  it("disables the active trade_mode button (idempotent click)", async () => {
    render(<SystemControls initial={makeStatus({ trade_mode: "paper" })} />);
    const paperBtn = screen.getByRole("button", { name: /paper/i });
    expect(paperBtn).toBeDisabled();
    await act(async () => {
      paperBtn.click();
    });
    expect(setTradeModeAction).not.toHaveBeenCalled();
  });

  it("surfaces errors returned by the action", async () => {
    confirmSpy.mockReturnValue(true);
    setKillSwitchAction.mockResolvedValueOnce({ ok: false, error: "permission denied" });
    render(<SystemControls initial={makeStatus({ is_trading_allowed: true })} />);
    const btn = screen.getByRole("button", { name: /停止する/ });
    await act(async () => {
      btn.click();
    });
    expect(await screen.findByText(/permission denied/)).toBeInTheDocument();
  });
});
