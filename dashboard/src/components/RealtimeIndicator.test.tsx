import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RealtimeStatusProvider, useRegisterChannelStatus } from "@/lib/realtime/statusContext";
import type { ChannelStatus } from "@/lib/realtime/types";
import { RealtimeIndicator } from "./RealtimeIndicator";

function Stub({ name, status }: { name: string; status: ChannelStatus }) {
  useRegisterChannelStatus(name, status);
  return null;
}

describe("RealtimeIndicator", () => {
  it("shows idle when no channels are registered", () => {
    render(
      <RealtimeStatusProvider>
        <RealtimeIndicator />
      </RealtimeStatusProvider>,
    );
    const node = screen.getByRole("status");
    expect(node.dataset.realtimeStatus).toBe("idle");
    expect(node).toHaveTextContent("idle");
  });

  it("shows live when all registered channels are connected", () => {
    render(
      <RealtimeStatusProvider>
        <Stub name="a" status="connected" />
        <Stub name="b" status="connected" />
        <RealtimeIndicator />
      </RealtimeStatusProvider>,
    );
    const node = screen.getByRole("status");
    expect(node.dataset.realtimeStatus).toBe("connected");
    expect(node).toHaveTextContent("live");
  });

  it("escalates to error when any channel reports error", () => {
    const { rerender } = render(
      <RealtimeStatusProvider>
        <Stub name="a" status="connected" />
        <Stub name="b" status="connecting" />
        <RealtimeIndicator />
      </RealtimeStatusProvider>,
    );
    expect(screen.getByRole("status").dataset.realtimeStatus).toBe("connecting");

    act(() => {
      rerender(
        <RealtimeStatusProvider>
          <Stub name="a" status="connected" />
          <Stub name="b" status="error" />
          <RealtimeIndicator />
        </RealtimeStatusProvider>,
      );
    });

    const node = screen.getByRole("status");
    expect(node.dataset.realtimeStatus).toBe("error");
    expect(node).toHaveTextContent("error");
  });
});
