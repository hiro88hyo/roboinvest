# 2026-06-17 Paper Hardening Handoff

## Context

2026-06-17 の paper test は注文/約定まで進まなかった。主因は次の2つ。

- OMS Paper が `raw-market-data` の tick/book 混在 subscription を読んでおり、book backlog で stale book を使っていた。
- Gateway 側の注文条件では `CAPITAL=500000` 相当の実行環境になっており、値がさ株が `below_min_lot` で落ちやすかった。

gpt5.5pro feedback では、RULE-only の素朴なBUY、MARKET/執行品質、薄商い、地合いフィルタが主なリスクとして指摘されていた。今回のセッションでは、明日の paper 観測に間に合う範囲で safety guard と entry filter を強化した。

## Production Runtime State

反映済み。

- `aggregator`: recreated
- `gateway`: recreated
- `strategy-rule`: rebuilt and recreated
- `universe-scanner`: batch image rebuilt
- `oms-paper`: raw book subscription fix reflected earlier in this session

Current important env values verified in running containers:

```text
strategy-rule:
  STRATEGIES_ENABLED=rsi_threshold,bollinger_breakout
  ENTRY_VOLUME_RATIO_MIN=2.0
  ENTRY_MAX_SPREAD_TICKS=2
  ENTRY_MIN_ASK_DEPTH_5=300
  ENTRY_MIN_MINUTES_FROM_OPEN=15

gateway:
  CAPITAL=1000000
  MARKET_REGIME_PAPER_GUARD_ENABLED=true
  SOFT_LOSS_THROTTLE_GUARD_ENABLED=true
  EXECUTION_GATE_GUARD_ENABLED=true

aggregator:
  MIN_CONFIDENCE_RULE_ONLY=0.45
```

Latest pre-open check:

```text
production-preopen-check --kabu-offline --no-pubsub-smoke --expected-trade-mode paper
OK 80 / WARN 0 / NG 0
```

## Main Changes

### OMS Paper raw book subscription

Files:

- `infra/pubsub/subscriptions.json`
- `infra/pubsub/init-topics.sh`
- `scripts/gcp-pubsub-admin.py`
- `infra/docker-compose.prod.yml`
- `infra/env.production.tpl`

Changes:

- Added `oms-paper-raw-books` subscription with filter `attributes.kind = "book"`.
- Changed OMS Paper production default subscription to `oms-paper-raw-books`.
- Added `OMS_PAPER_PUBSUB_PULL_MAX_MESSAGES=500`.
- Extended Pub/Sub admin tooling and emulator init to preserve subscription filters.

Purpose:

- Avoid tick messages starving OMS Paper book consumption.
- Prevent stale book no-fill like the 2026-06-17 morning test.

### Capital and confidence threshold

Files:

- `infra/env.production`
- `infra/env.production.tpl`
- `infra/docker-compose.prod.yml`
- `scripts/production-preopen-check.py`
- docs/runbooks

Changes:

- `CAPITAL=1000000`
- `MIN_CONFIDENCE_RULE_ONLY=0.45`
- Pre-open check expects these values.

Observed counterfactual from 2026-06-17 data:

- Expected fills under `CAPITAL=1000000`, `MIN_CONFIDENCE_RULE_ONLY=0.45`: 6
- Approx PnL assuming buy at best ask and closeout sell at best bid: about `-3,000円`
- This setting is useful for paper flow, not proven profitable.

### Gateway safety guards now active for paper

Files:

- `infra/env.production`
- `infra/env.production.tpl`
- `infra/docker-compose.prod.yml`
- `scripts/production-preopen-check.py`
- docs/runbooks

Changes:

- `MARKET_REGIME_PAPER_GUARD_ENABLED=true`
- `SOFT_LOSS_THROTTLE_GUARD_ENABLED=true`
- `EXECUTION_GATE_GUARD_ENABLED=true`

Impact:

- RISK_OFF/CRASH paper BUY should be rejected.
- RULE-only BUY after soft daily loss should be rejected.
- Wide spread / insufficient depth BUY should be rejected.

Important consequence:

- Tomorrow's paper may produce fewer orders/fills than the 0.45/100万円 counterfactual.
- This is intentional. The goal is to avoid low-quality BUYs, not to maximize paper fill count.

### Strategy Rule entry hardening

Files:

- `services/strategy-rule/src/strategy_rule/config.py`
- `services/strategy-rule/src/strategy_rule/strategies/entry_filters.py`
- `services/strategy-rule/src/strategy_rule/strategies/rsi_threshold.py`
- `services/strategy-rule/src/strategy_rule/strategies/bollinger_breakout.py`
- `services/strategy-rule/src/strategy_rule/strategies/sma_crossover.py`
- `services/strategy-rule/src/strategy_rule/strategies/__init__.py`
- strategy-rule unit tests
- `infra/env.production`
- `infra/env.production.tpl`
- `infra/docker-compose.prod.yml`

Production entry filter values:

```text
ENTRY_VOLUME_RATIO_MIN=2.0
ENTRY_MAX_SPREAD_BPS=30
ENTRY_MAX_SPREAD_TICKS=2
ENTRY_MIN_ASK_DEPTH_5=300
ENTRY_MIN_BOOK_IMBALANCE_5=-0.5
ENTRY_MIN_MINUTES_FROM_OPEN=15
ENTRY_MIN_MINUTES_TO_CLOSE=60
RSI_BUY_REQUIRE_PRICE_ABOVE_VWAP=true
RSI_BUY_REQUIRE_SMA_UPTREND=true
BOLLINGER_BUY_REQUIRE_PRICE_ABOVE_VWAP=true
BOLLINGER_BUY_REQUIRE_SMA_UPTREND=true
```

Behavior:

- RSI BUY and Bollinger BUY now require volume/VWAP/SMA/spread/depth/imbalance/time filters.
- SMA crossover BUY also uses the shared entry filter if it is ever re-enabled.
- SELL signals are not blocked by these entry filters.

Also fixed production drift:

- `infra/env.production` had `STRATEGIES_ENABLED=sma_crossover,rsi_threshold,bollinger_breakout`.
- Changed to `STRATEGIES_ENABLED=rsi_threshold,bollinger_breakout`.
- Reason: docs/reports already concluded `sma_crossover` validation PF was poor and removed it from default.

### Universe Scanner risk penalty

Files:

- `services/universe-scanner/src/universe_scanner/filters/dynamic.py`
- `services/universe-scanner/src/universe_scanner/config.py`
- `services/universe-scanner/src/universe_scanner/pipeline.py`
- `services/universe-scanner/.env.example`
- `infra/env.production`
- `infra/env.production.tpl`
- `infra/docker-compose.prod.yml`
- universe-scanner unit tests

Behavior:

- Dynamic score is now:

```text
score = opportunity_score - risk_penalty * SCAN_WEIGHT_RISK_PENALTY
```

- Risk penalty includes:
  - high volatility z-score
  - negative momentum z-score
  - extreme volume surge z-score
  - overheat momentum z-score

Configured production defaults:

```text
SCAN_WEIGHT_RISK_PENALTY=1.0
SCAN_RISK_VOLATILITY_Z_WEIGHT=0.75
SCAN_RISK_NEGATIVE_MOMENTUM_Z_WEIGHT=1.0
SCAN_RISK_VOLUME_SURGE_Z_WEIGHT=0.5
SCAN_RISK_OVERHEAT_MOMENTUM_Z_WEIGHT=0.5
SCAN_RISK_VOLUME_SURGE_Z=1.5
SCAN_RISK_OVERHEAT_MOMENTUM_Z=1.5
```

`watchlist.selected_reasons` now includes `opportunity_score` and `risk_penalty`.

## Verification Run

Tests run successfully:

```text
uv run pytest services/gateway/tests/unit/test_stream_runner.py \
  services/gateway/tests/unit/test_order_builder.py \
  services/oms-paper/tests/unit/test_paper_fill_simulator.py
# 80 passed

uv run pytest services/strategy-rule/tests/unit/test_config.py \
  services/strategy-rule/tests/unit/test_rsi_threshold.py \
  services/strategy-rule/tests/unit/test_bollinger_breakout.py
# 30 passed

uv run pytest services/strategy-rule/tests/unit/test_sma_crossover.py \
  services/strategy-rule/tests/unit/test_config.py \
  services/strategy-rule/tests/unit/test_registry.py
# 22 passed

uv run pytest services/strategy-rule/tests/unit/test_rsi_threshold.py \
  services/strategy-rule/tests/unit/test_bollinger_breakout.py \
  services/universe-scanner/tests/unit/test_dynamic_filter.py
# 29 passed

uv run pytest services/universe-scanner/tests/unit/test_dynamic_filter.py \
  services/universe-scanner/tests/unit/test_pipeline_market_regime.py
# 9 passed
```

Compose/pre-open:

```text
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml config --quiet

op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py \
  --timeout 30 --kabu-offline --no-pubsub-smoke --expected-trade-mode paper
# OK 80 / WARN 0 / NG 0
```

Production recreate/build commands already run:

```text
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml \
  up -d --no-deps --force-recreate aggregator gateway

op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml \
  up -d --build --no-deps strategy-rule

op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml \
  --profile batch build universe-scanner
```

## Expected Next Paper Behavior

Compared with 2026-06-17:

- More robust OMS Paper book consumption.
- More capital headroom for 100-share orders.
- Lower confidence threshold for RULE-only signals.
- But stricter entry filters and Gateway guards will reject weak/wide/thin/early BUYs.

Likely observation points:

- `strategy_logs`: BUY count may drop because entry filters now suppress signals before Aggregator.
- `aggregator_logs`: fewer rule-only BUY candidates.
- `gateway` logs:
  - `signal_rejected` reasons should include execution or regime reasons if quality is bad.
  - `order_published` should represent higher-quality surviving BUYs.
- `trades_paper`: fills should happen if surviving orders reach OMS Paper and fresh book is available.

## Remaining Larger Work

Not implemented in this session:

- Stop-loss as an OMS/Gateway-side always-on safety mechanism independent of strategy SELL.
- OMS Live/paper retry and open-order reconciliation for stop-loss orders.
- More realistic passive/marketable-limit queue simulation.
- Market regime live guard enablement. Current live guard remains off; paper guard is on.
- Full long-window validation of the new entry filters and Universe Scanner risk penalty.

Recommended next session:

1. During next paper day, monitor `strategy_logs`, `aggregator_logs`, `gateway` reject reasons, `order_published`, `trades_paper`.
2. If zero orders all morning, temporarily inspect which filter is binding most often before loosening anything.
3. Do not re-enable `sma_crossover` unless explicitly running an experiment.
4. Plan stop-loss safety mechanism as a separate PR; do not squeeze it into paper-open config changes.

## Current Worktree Note

This session left many modified files and one untracked feedback memo:

```text
?? docs/handoff/2026-06-16-gpt55pro-feedback.md
```

That file contains the original gpt5.5pro review text used as implementation guidance. Decide in the next session whether to commit it, rename it, or leave it untracked.
