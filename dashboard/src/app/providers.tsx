"use client";

import { RealtimeStatusProvider } from "@/lib/realtime/statusContext";
import type { ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  return <RealtimeStatusProvider>{children}</RealtimeStatusProvider>;
}
