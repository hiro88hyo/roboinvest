import type { RowEvent } from "./types";

export interface ApplyRowEventOptions<T> {
  compare?: (a: T, b: T) => number;
  limit?: number;
}

/**
 * Apply a Realtime postgres_changes event to an array of rows.
 *
 * - INSERT/UPDATE: upsert by `getKey` (dedupes at-least-once delivery)
 * - DELETE: remove by `getKey(old)` if old has enough fields to compute the key
 * - If `compare` is provided, the result is re-sorted; otherwise insert order is
 *   "new row to the front" so the most recent record stays on top of log views.
 * - If `limit` is provided, the array is truncated to that size after sorting.
 */
export function applyRowEvent<T>(
  rows: readonly T[],
  event: RowEvent<T>,
  getKey: (row: T) => string,
  options: ApplyRowEventOptions<T> = {},
): T[] {
  const { compare, limit } = options;
  let next: T[];

  if (event.eventType === "DELETE") {
    const key = tryGetKey(event.old, getKey);
    next = key === null ? [...rows] : rows.filter((r) => getKey(r) !== key);
  } else {
    const incoming = event.new;
    const key = getKey(incoming);
    const idx = rows.findIndex((r) => getKey(r) === key);
    if (idx >= 0) {
      next = [...rows];
      next[idx] = incoming;
    } else {
      next = [incoming, ...rows];
    }
  }

  if (compare) next.sort(compare);
  if (limit !== undefined && next.length > limit) next = next.slice(0, limit);
  return next;
}

/**
 * Apply a Realtime postgres_changes event to a single tracked row (e.g. system_status).
 *
 * - INSERT/UPDATE: replace with the new row
 * - DELETE: clear to null
 */
export function applySingletonEvent<T>(current: T | null, event: RowEvent<T>): T | null {
  if (event.eventType === "DELETE") return null;
  return event.new;
}

function tryGetKey<T>(old: Partial<T>, getKey: (row: T) => string): string | null {
  try {
    return getKey(old as T);
  } catch {
    return null;
  }
}
