# Copy to infra/env.production on the LAN host, then run via:
#   op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml config
#   op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml up -d --build
#
# Keep only op:// references or placeholders here. Do not commit materialized secrets.

TZ=Asia/Tokyo
APP_ENV=production
JSON_LOGS=true
LOG_LEVEL=INFO
MARKET_DATA_STALE_WARN_SECONDS=180

# Managed GCP Pub/Sub. Do not set PUBSUB_EMULATOR_HOST in production.
PUBSUB_PROJECT_ID=op://Trade AI/production/PUBSUB_PROJECT_ID
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-pubsub-sa.json
GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=/dev/shm/roboinvest/gcp-pubsub-sa.json
# Materialize op://Trade AI/production/GOOGLE_APPLICATION_CREDENTIALS_JSON to /dev/shm/roboinvest/gcp-pubsub-sa.json before compose up.

# Cloud Logging collector. Enable with docker compose --profile observability.
OTEL_COLLECTOR_IMAGE=otel/opentelemetry-collector-contrib:0.153.0

# Supabase Cloud
SUPABASE_URL=op://Trade AI/production/SUPABASE_URL
SUPABASE_SECRET_KEY=op://Trade AI/production/SUPABASE_SECRET_KEY

# Universe Scanner batch. J-Quants API v2 uses API key auth.
JQUANTS_API_KEY=op://Trade AI/jquants/JQUANTS_API_KEY
# Optional only for legacy v1 auth flow:
# JQUANTS_REFRESH_TOKEN=op://Trade AI/jquants/JQUANTS_REFRESH_TOKEN
JQUANTS_PLAN=standard
JQUANTS_API_VERSION=v2
JQUANTS_API_BASE=https://api.jquants.com/v2

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
LIVE_SIGNAL_MAX_AGE_SECONDS=300
LIVE_DAY_NEW_BUY_START_TIME=09:15
LIVE_DAY_NEW_BUY_CUTOFF_TIME=14:30
CLOSEOUT_ORDER_FILL_TIMEOUT_SECONDS=2400
OMS_LIVE_ALLOWED_SYMBOLS=7203

# Strategy parameters known from the verified local setup.
STRATEGIES_ENABLED=sma_crossover,rsi_threshold,bollinger_breakout
AI_TRIGGER_MIN_CONFIDENCE=0.8
PUBSUB_TOPIC_AI_TRIGGERS=strategy-ai-triggers
SMA_MIN_GAP_RATIO=0.005
RSI_BUY_THRESHOLD=25.0
RSI_SELL_THRESHOLD=75.0
BOLLINGER_BREAKOUT_TOLERANCE=0.15

# Aggregator thresholds. Keep consensus permissive while filtering weak single-source signals.
MIN_CONFIDENCE_RULE_ONLY=0.5
MIN_CONFIDENCE_AI_ONLY=0.5
MIN_CONFIDENCE_CONSENSUS=0.3

# AI strategy
LLM_PROVIDER=gemini
GEMINI_API_KEY=op://Trade AI/ai/GEMINI_API_KEY
GEMINI_MODEL=gemini-2.5-flash
AI_MIN_INTERVAL_SECONDS=300
AI_MAX_OUTPUT_TOKENS=2048
AI_PUBSUB_PULL_MAX_MESSAGES=5
PUBSUB_SUBSCRIPTION_AI_FEATURES=strategy-ai-rule-signals

# Feature storage
STORAGE_TICK_RESOLUTION=1s

# Risk defaults
CAPITAL=1000000
MAX_RISK_PER_TRADE_PCT=0.02
SWING_RISK_SCALE=0.5
DEFAULT_STOP_LOSS_SPREAD_PCT=0.02
MIN_LOT_SIZE=100
