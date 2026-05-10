import { parseTradeType, TradeTypeTabs } from "@/components/TradeTypeTabs";
import { TradesTable } from "@/components/trades/TradesTable";
import { getReadClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 100;

export default async function TradesPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const { type } = await searchParams;
  const tradeType = parseTradeType(type);
  const tableName = tradeType === "live" ? "trades_live" : "trades_paper";
  const supabase = getReadClient();
  const { data: trades } = await supabase
    .from(tableName)
    .select("*")
    .order("executed_at", { ascending: false })
    .limit(PAGE_SIZE);

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight">Trades</h1>
        <TradeTypeTabs basePath="/trades" current={tradeType} />
      </div>

      <p className="mt-2 text-xs text-neutral-500">直近 {PAGE_SIZE} 件</p>

      <TradesTable initial={trades ?? []} tradeType={tradeType} pageSize={PAGE_SIZE} />
    </main>
  );
}
