# Event Minute Data Capture

Created: 2026-07-04

Status: implemented as a minimal event-capture watchlist injection path.

## Goal

Start capturing 09:00-09:30 JST minute data for event candidates before this
data becomes unrecoverable. This is data capture only; it is not a live
promotion and does not change the event cluster strategy parameters.

## Current Storage Path

Feature Engine already has the required 1-minute storage path:

- `FeatureEngineSettings.storage_tick_resolution` accepts `raw`, `1s`, `1m`,
  and `5m`.
- `StreamRunner` records accepted `TickData` through `WarmWriter` when
  `warm_writer` is configured.
- `WarmWriter` groups ticks by `symbol` and JST date and writes Parquet under
  `storage_warm_dir/symbol=<SYMBOL>/date=<YYYY-MM-DD>/`.
- With `STORAGE_TICK_RESOLUTION=1m`, tick input is aggregated to
  `symbol,timestamp,open,high,low,close,volume`.
- `storage/cold.py` can migrate warm partitions into 1m/5m cold OHLCV
  partitions.

Operational requirement: Feeder and Feature Engine must both be running before
the open, and Feature Engine must be configured with `STORAGE_TICK_RESOLUTION=1m`.

## Capture Gap

Feeder registers only the symbols returned by Supabase `watchlist` for the
current `valid_date`. Event candidates detected after the previous close are not
guaranteed to be in the Universe Scanner watchlist, so Feeder might never
subscribe to their kabu.com WebSocket streams.

## Minimal Implementation

Use `scripts/upsert-event-candidates-watchlist.py` after the event detector:

```bash
uv run python scripts/upsert-event-candidates-watchlist.py \
  --candidates-json out/event-paper-observation/candidates.json \
  --output-json out/event-paper-observation/event-watchlist-upsert.json \
  --dry-run
```

Apply to Supabase:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/upsert-event-candidates-watchlist.py \
    --candidates-json out/event-paper-observation/candidates.json \
    --output-json out/event-paper-observation/event-watchlist-upsert.json
```

The script:

- uses candidate `entry_date` as `watchlist.valid_date`,
- inserts only missing `(symbol, valid_date)` rows,
- does not overwrite Universe Scanner rows,
- writes `selected_reasons.reasons=["event_capture"]`,
- caps event additions at `--max-symbols` with default `10`.

## Register Capacity Rule

The repository does not contain a verified kabu.com WebSocket registration hard
limit. The current Universe Scanner default is `SCAN_DYNAMIC_TOP_N=30`.

Until the real API limit is confirmed, use this conservative rule:

- keep Universe Scanner rows untouched,
- add at most 10 event-capture symbols per valid date,
- expected register set is normally `<= 40`,
- if Feeder logs kabu register failure, reduce `--max-symbols` before retrying.

Event-capture symbols are additive and low priority. They must not cause removal
of the normal daily watchlist.

## Manual Market-Day Check

Before 09:00 JST:

1. Run event detector.
2. Run watchlist upsert.
3. Confirm Feeder polls `watchlist` for the entry date and includes event
   symbols.
4. Confirm Feature Engine is running with `STORAGE_TICK_RESOLUTION=1m`.

After 09:30 JST, verify warm parquet exists:

```bash
find data/warm -path '*/date=YYYY-MM-DD/*.parquet' | rg 'symbol=<SYMBOL>'
```

If no parquet exists for an event symbol, record whether the failure was
watchlist insertion, Feeder register, raw market data, or Feature Engine
storage.
