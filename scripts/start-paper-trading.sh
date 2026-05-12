#!/usr/bin/env bash
# 毎朝の paper trading 起動スクリプト
# 使い方: bash scripts/start-paper-trading.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') start-paper-trading ==="

# --- 1. インフラ確認 ---------------------------------------------------------
echo "[1/5] インフラ確認..."

if ! curl -sf "http://localhost:8085" >/dev/null 2>&1; then
  echo "  Pub/Sub エミュレータ未起動。起動してください:"
  echo "    docker compose -f infra/docker-compose.dev.yml up -d"
  exit 1
fi
echo "  Pub/Sub OK"

if ! curl -sf "http://localhost:54321/rest/v1/" \
    -H "apikey: $(grep SUPABASE_SECRET_KEY services/feeder/.env | cut -d= -f2)" \
    >/dev/null 2>&1; then
  echo "  Supabase 未起動。起動してください:"
  echo "    cd infra && supabase start"
  exit 1
fi
echo "  Supabase OK"

# --- 2. system_status 初期化 -------------------------------------------------
echo "[2/5] system_status 確認..."

SUPA_URL=$(grep SUPABASE_URL services/feature-engine/.env | cut -d= -f2)
SUPA_KEY=$(grep SUPABASE_SECRET_KEY services/feature-engine/.env | cut -d= -f2)

STATUS=$(curl -sf "${SUPA_URL}/rest/v1/system_status?id=eq.1&select=trade_mode,is_trading_allowed,daily_loss_limit" \
    -H "apikey: ${SUPA_KEY}" -H "Authorization: Bearer ${SUPA_KEY}")
echo "  system_status: $STATUS"

# --- 3. daily_ohlcv 確認 -----------------------------------------------------
echo "[3/5] daily_ohlcv 確認 (今日のデータ有無)..."
TODAY=$(TZ=Asia/Tokyo date '+%Y-%m-%d')
OHLCV_COUNT=$(curl -sf "${SUPA_URL}/rest/v1/daily_ohlcv?date=eq.${TODAY}&select=symbol" \
    -H "apikey: ${SUPA_KEY}" -H "Authorization: Bearer ${SUPA_KEY}" \
    -H "Prefer: count=exact" -I 2>/dev/null | grep -i "content-range" | grep -oP '\d+(?=/)' || echo "0")

if [ "${OHLCV_COUNT:-0}" -eq 0 ]; then
  echo "  WARNING: daily_ohlcv に今日(${TODAY})のデータがありません。"
  echo "  Universe Scanner が設定済みなら実行してください:"
  echo "    cd services/universe-scanner && uv run python -m universe_scanner"
  echo "  または手動で INSERT してください (gateway の entry_price フォールバック用)。"
  echo "  続行しますか？ [y/N]"
  read -r ans
  [ "${ans:-N}" = "y" ] || exit 1
else
  echo "  daily_ohlcv: ${TODAY} に ${OHLCV_COUNT} 件あり"
fi

# --- 4. watchlist 確認 -------------------------------------------------------
echo "[4/5] watchlist 確認 (今日のデータ有無)..."
WL_COUNT=$(curl -sf "${SUPA_URL}/rest/v1/watchlist?valid_date=eq.${TODAY}&select=symbol" \
    -H "apikey: ${SUPA_KEY}" -H "Authorization: Bearer ${SUPA_KEY}" \
    -H "Prefer: count=exact" -I 2>/dev/null | grep -i "content-range" | grep -oP '\d+(?=/)' || echo "0")

if [ "${WL_COUNT:-0}" -eq 0 ]; then
  echo "  WARNING: watchlist に今日(${TODAY})のデータがありません。"
  echo "  手動 seed 例 (7203のみ):"
  echo "    psql postgresql://postgres:postgres@127.0.0.1:54322/postgres -c \\"
  echo "      \"INSERT INTO watchlist (symbol, valid_date, symbol_name, score) VALUES ('7203', '${TODAY}', 'トヨタ自動車', 1.0) ON CONFLICT DO NOTHING;\""
  echo "  続行しますか？ [y/N]"
  read -r ans
  [ "${ans:-N}" = "y" ] || exit 1
else
  echo "  watchlist: ${TODAY} に ${WL_COUNT} 件あり"
fi

# --- 5. サービス起動 ---------------------------------------------------------
echo "[5/5] サービス起動..."

_start() {
  local name="$1" pkg="$2" dir="$3"
  (cd "$dir" && set -a && source .env && set +a && \
    nohup uv run python -m "$pkg" stream > "/tmp/${name}.log" 2>&1 &)
  echo "  ${name} started (log: /tmp/${name}.log)"
}

_start feature-engine  feature_engine  "$REPO_ROOT/services/feature-engine"
sleep 1
_start strategy-rule   strategy_rule   "$REPO_ROOT/services/strategy-rule"
_start aggregator      aggregator      "$REPO_ROOT/services/aggregator"
_start gateway         gateway         "$REPO_ROOT/services/gateway"
_start oms-paper       oms_paper       "$REPO_ROOT/services/oms-paper"
sleep 1
# feeder は本番 kabu 接続のため env ファイルを明示
if [ -f /tmp/feeder-prod.env ]; then
  (cd "$REPO_ROOT/services/feeder" && \
    set -a && source /tmp/feeder-prod.env && set +a && \
    nohup uv run python -m feeder stream > /tmp/feeder.log 2>&1 &)
  echo "  feeder started (log: /tmp/feeder.log)"
else
  echo "  WARNING: /tmp/feeder-prod.env が見つかりません。feeder を手動で起動してください。"
fi

echo ""
echo "=== 起動完了 ==="
echo "ログ確認: tail -f /tmp/feature-engine.log /tmp/strategy-rule.log /tmp/oms-paper.log"
echo "停止:     kill \$(pgrep -f 'python.*-m (feeder|feature_engine|strategy_rule|aggregator|gateway|oms_paper)')"
echo "health:   uv run --script scripts/health-check.py"
