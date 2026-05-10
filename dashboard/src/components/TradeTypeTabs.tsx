import Link from "next/link";

export type TradeType = "live" | "paper";

export function parseTradeType(value: string | string[] | undefined): TradeType {
  return value === "paper" ? "paper" : "live";
}

export function TradeTypeTabs({ basePath, current }: { basePath: string; current: TradeType }) {
  const tabs: { type: TradeType; label: string }[] = [
    { type: "live", label: "Live" },
    { type: "paper", label: "Paper" },
  ];
  return (
    <div className="inline-flex overflow-hidden rounded border border-neutral-800">
      {tabs.map((t) => (
        <Link
          key={t.type}
          href={`${basePath}?type=${t.type}`}
          className={
            current === t.type
              ? "bg-neutral-100 px-4 py-1.5 text-sm font-medium text-neutral-900"
              : "px-4 py-1.5 text-sm text-neutral-300 hover:bg-neutral-800"
          }
        >
          {t.label}
        </Link>
      ))}
    </div>
  );
}
