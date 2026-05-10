import { PositionsTable } from "@/components/positions/PositionsTable";
import { parseTradeType, TradeTypeTabs } from "@/components/TradeTypeTabs";
import { getReadClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function PositionsPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const { type } = await searchParams;
  const tradeType = parseTradeType(type);
  const supabase = getReadClient();
  const { data: positions } = await supabase
    .from("positions")
    .select("*")
    .eq("trade_type", tradeType)
    .order("opened_at", { ascending: false });

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight">Positions</h1>
        <TradeTypeTabs basePath="/positions" current={tradeType} />
      </div>
      <PositionsTable initial={positions ?? []} tradeType={tradeType} />
    </main>
  );
}
