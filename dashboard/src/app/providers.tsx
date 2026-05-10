"use client";

import type { ReactNode } from "react";
import { RealtimeStatusProvider } from "@/lib/realtime/statusContext";

export function Providers({ children }: { children: ReactNode }) {
  return <RealtimeStatusProvider>{children}</RealtimeStatusProvider>;
}
