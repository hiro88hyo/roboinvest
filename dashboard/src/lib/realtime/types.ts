export type ChannelStatus = "idle" | "connecting" | "connected" | "disconnected" | "error";

export type RowEvent<T> =
  | { eventType: "INSERT"; new: T; old: Partial<T> | Record<string, never> }
  | { eventType: "UPDATE"; new: T; old: Partial<T> | Record<string, never> }
  | { eventType: "DELETE"; new: Record<string, never>; old: Partial<T> };
