export function ActionBadge({ action }: { action: string }) {
  const cls =
    action === "BUY"
      ? "bg-emerald-900/40 text-emerald-300"
      : action === "SELL"
        ? "bg-rose-900/40 text-rose-300"
        : "bg-neutral-800 text-neutral-300";
  return <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>{action}</span>;
}
