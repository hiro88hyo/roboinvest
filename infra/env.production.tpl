# Copy to infra/env.production on the LAN host, then run via:
#   op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml config
#   op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml up -d --build
#
# Keep only op:// references or placeholders here. Do not commit materialized secrets.

TZ=Asia/Tokyo
LOG_LEVEL=INFO

# Managed GCP Pub/Sub. Do not set PUBSUB_EMULATOR_HOST in production.
PUBSUB_PROJECT_ID=op://Trade AI/production/PUBSUB_PROJECT_ID
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-pubsub-sa.json
# Materialize op://Trade AI/production/GOOGLE_APPLICATION_CREDENTIALS_JSON to infra/secrets/gcp-pubsub-sa.json before compose up.

# Supabase Cloud
SUPABASE_URL=op://Trade AI/production/SUPABASE_URL
SUPABASE_SECRET_KEY=op://Trade AI/production/SUPABASE_SECRET_KEY

# Universe Scanner batch. Paid-plan production automation is a later ADR-0001 step.
JQUANTS_REFRESH_TOKEN=op://Trade AI/jquants/JQUANTS_REFRESH_TOKEN
JQUANTS_PLAN=standard
JQUANTS_API_BASE=https://api.jquants.com/v1

# kabu Station via LAN Caddy reverse proxy
KABU_API_BASE_URL=http://<windows-host-ip>:28080/kabusapi
KABU_WS_URL=ws://<windows-host-ip>:28080/kabusapi/websocket
KABU_API_PASSWORD=op://Trade AI/kabu/KABU_API_PASSWORD
KABU_ORDER_PASSWORD=op://Trade AI/kabu/KABU_ORDER_PASSWORD
FEEDER_KABU_DEFAULT_EXCHANGE=1
KABU_DEFAULT_EXCHANGE=9
KABU_ACCOUNT_TYPE=4

# Start production deployments in paper mode.
TRADE_MODE=paper

# OMS Live safety knobs. Keep dry-run true until the live readiness gate passes.
OMS_LIVE_DRY_RUN=true
OMS_LIVE_MAX_QTY_PER_ORDER=100
OMS_LIVE_ALLOWED_SYMBOLS=7203

# Strategy parameters known from the verified local setup.
STRATEGIES_ENABLED=sma_crossover,rsi_threshold,bollinger_breakout
SMA_MIN_GAP_RATIO=0.005
RSI_BUY_THRESHOLD=25.0
RSI_SELL_THRESHOLD=75.0
BOLLINGER_BREAKOUT_TOLERANCE=0.15

# AI strategy
LLM_PROVIDER=gemini
GEMINI_API_KEY=op://Trade AI/ai/GEMINI_API_KEY
GEMINI_MODEL=gemini-2.5-flash

# Feature storage
STORAGE_TICK_RESOLUTION=1s

# Risk defaults
CAPITAL=1000000
MAX_RISK_PER_TRADE_PCT=0.02
SWING_RISK_SCALE=0.5
DEFAULT_STOP_LOSS_SPREAD_PCT=0.02
MIN_LOT_SIZE=100
