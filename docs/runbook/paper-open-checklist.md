# Paper Open Checklist

作成日: 2026-05-19

production compose / Cloud Supabase / managed Pub/Sub を前提に、寄り付き前に paper trading を安全に始めるための最小手順。
明日は Universe Scanner を手動実行し、その結果を確認してから常駐 services を起動する。

最短版のコマンド一覧は [`paper-open-quickstart.md`](paper-open-quickstart.md) を参照。

## 1. Preconditions

- `main` が最新であること
- 1Password service account token が `infra/.op.service-account.env` で読めること
- `infra/env.production` が J-Quants API v2 / Supabase / kabu / GCP secrets を参照していること
- GCP Pub/Sub service account JSON は通常 tmpfs の
  `/dev/shm/roboinvest/gcp-pubsub-sa.json` を使う。host 側から読めない場合、
  `production-preopen-check.py` は 1Password から一時 credential を作って自己修復する。
  compose / Universe Scanner のように bind mount が必要なコマンドでは、
  読める実ファイルを `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH` で明示する

確認:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml config >/dev/null
```

`/dev/shm/roboinvest/gcp-pubsub-sa.json` が root-owned directory などで
実ファイルとして使えない場合は、代替 credential を materialize して使う。
`op run --env-file infra/env.production` は env file の値で shell 側の
`GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH` を上書きするため、compose コマンドでは
`op run ... -- env GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=... docker compose ...`
の形で渡す。

```bash
op read --out-file /tmp/roboinvest-gcp-pubsub-sa.json --force \
  op://roboinvest/production/GOOGLE_APPLICATION_CREDENTIALS_JSON
chmod 600 /tmp/roboinvest-gcp-pubsub-sa.json
uv run python -m json.tool /tmp/roboinvest-gcp-pubsub-sa.json >/dev/null
export GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=/tmp/roboinvest-gcp-pubsub-sa.json
```

## 2. Run Universe Scanner

寄り付き前に当日 watchlist を生成する。通常の手動起動は `bash scripts/run-production-universe-scanner.sh` を使ってよい。

```bash
cd /home/hiroyuki/workspaces/roboinvest
bash scripts/run-production-universe-scanner.sh
```

従来どおり compose を直接叩く場合は次。

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml --profile batch run --rm universe-scanner
```

成功条件:

- ログ末尾が `done: valid_date=YYYY-MM-DD watchlist_size=N`
- `watchlist_size` が 0 ではない

補足:

- `JQUANTS_API_VERSION=v2` では `JQUANTS_API_KEY` を使う
- `daily_ohlcv` は chunk upsert のため数分かかることがある

## 3. Verify Supabase State

当日の `watchlist` と `daily_ohlcv` の最新日付を確認する。

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production --   uv run python scripts/health-check.py --check supabase --timeout 30
```

最低限見たいこと:

- `watchlist` が空でない
- `daily_ohlcv` が直近営業日まで入っている
- `system_status` が読める

必要なら SQL / REST で当日 `watchlist` 件数も spot check する。

production compose / Supabase / managed Pub/Sub / container env をまとめて確認する場合は、
paper 期待値で pre-open check を実行する。

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py \
    --timeout 30 \
    --expected-trade-mode paper
```

前日や休日に翌営業日分を準備する場合は、当日ではなく検証対象日を明示する。

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py \
    --timeout 30 \
    --expected-trade-mode paper \
    --target-date YYYY-MM-DD \
    --kabu-offline
```

host 側の `/dev/shm/roboinvest/gcp-pubsub-sa.json` が root-owned、directory、
または missing などで読めない場合は、
スクリプトが 1Password の `production/GOOGLE_APPLICATION_CREDENTIALS_JSON` から
一時 credential を作って Pub/Sub check を継続し、終了時に削除する。
別の credential を使う特殊ケースだけ `--gcp-credentials <readable-host-path>` を追加する。
Pub/Sub smoke publish/pull/ack を避ける予行では `--no-pubsub-smoke` を追加する。

## 4. Start Production Compose

paper mode のまま compose services を起動する。

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml up -d --build
```

代替 credential path を使う場合:

```bash
op run --env-file infra/env.production -- \
  env GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH="$GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH" \
    docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml up -d --build
```

確認:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production --   uv run python scripts/health-check.py --check supabase services --timeout 30
```

## 5. First Checks After Start

寄り付き直後は次を重点確認する。

- `feeder` が kabu WebSocket に接続している
- `raw-market-data` が流れ始める
- `feature-engine -> strategy-rule / strategy-ai -> aggregator -> gateway -> oms-paper` が止まっていない
- `gateway` reject だけが増えていない

必要なログ確認:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml logs --tail=100 feeder feature-engine strategy-rule strategy-ai aggregator gateway oms-paper
```

### 5.1 Paper Checkpoint Report

寄り付き後はログだけでなく、Supabase / paper archive / compose logs をまとめた checkpoint report で
約定パイプラインを確認する。目安は 9:15 以降、前引け、大引け。

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/report-paper-checkpoint.py --checkpoint open

op run --env-file infra/env.production -- \
  uv run python scripts/report-paper-checkpoint.py --checkpoint midday

op run --env-file infra/env.production -- \
  uv run python scripts/report-paper-checkpoint.py --checkpoint close
```

重点確認:

- `watchlist_count` が 0 ではない
- `aggregator by source/action` で BUY が極端に偏っていない
- `orders archived_total / archived_buy` が aggregator BUY に対して極端に少なくない
- `trades_paper_total` と open positions が増えているか
- gateway reject reason に `paper_symbol_order_cooldown` 以外の想定外理由が増えていないか
- oms-paper no-fill reason が `stale_book` / `no_book` に寄っていないか
- `latest_market_data_summary.latest_book_age_seconds` が大きくなっていないか

現在の paper 設定:

- `PAPER_BUY_LIMIT_OFFSET_TICKS=0`
  - 2026-06-18 archive replay では original limit の fill が 14/77、`orig+3t` が 62/77
  - 平均 fill price は original 比 +10.80 bps、最大 +54.95 bps
- `PAPER_SYMBOL_ORDER_COOLDOWN_SECONDS=300`
  - 2026-06-18 archive simulation では 77 BUY のうち kept 40 / rejected 37
- `OMS_PAPER_RAW_BOOK_DRAIN_MAX_BATCHES=10`
  - oms-paper が古い book batch だけを見て no-fill に寄るのを避ける

当日後の深掘り:

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/report-paper-execution-diagnostics.py --date YYYY-MM-DD

uv run python scripts/explore-paper-limit-prices.py \
  --date YYYY-MM-DD \
  --output-csv out/paper-archive-YYYY-MM-DD/limit-price-sweep.csv
```

任意の paper 実験:

- `ENTRY_VOLUME_RATIO_MIN=2.0` を設定すると、RSI / Bollinger の BUY entry は直近出来高が
  20 日平均の 2.0 倍以上のときだけ出る。2026-06 の長期日足バックテストでは次の paper 候補だが、
  fold 安定性が不足しているため live には設定しない。
- feature-engine は板スナップショットを `/data/books` に Parquet 保存する。paper 後に
  `uv run python scripts/order-book-archive-to-jsonl.py --book-dir <books> --date YYYY-MM-DD --output books.jsonl`
  で OMS Paper backtest 用の `OrderBookSnapshot` JSONL に変換できる。
- gateway は承認済み `OrderRequest` を `/data/orders/trade_mode=paper/date=YYYY-MM-DD/orders.jsonl`
  に追記保存する。paper 後はこの `orders.jsonl` と変換済み `books.jsonl` を
  `oms-paper backtest --orders ... --books ...` に渡して再現検証する。
- Docker volume からホストへ取り出す場合は
  `bash scripts/export-paper-archives.sh --date YYYY-MM-DD --output-dir out/paper-archive-YYYY-MM-DD`
  を使う。`gateway:/data/orders` と `feature-engine:/data/books` をコピーする。
- 通常の postmortem は
  `bash scripts/run-paper-postmortem.sh --date YYYY-MM-DD --output-dir out/paper-archive-YYYY-MM-DD`
  を使う。archive export、OMS Paper backtest、gate、Markdown summary まで実行する。
  `backtest/metadata.json` には archive 注文件数、板件数、fill / no-fill 件数が残る。
- gate 閾値だけ変えて再実行する場合は
  `bash scripts/run-paper-postmortem.sh --date YYYY-MM-DD --output-dir out/paper-archive-YYYY-MM-DD --skip-export --gate-arg --min-profit-factor --gate-arg 1.2`
  のように `--skip-export` で既存 archive を再利用する。
- export 済み archive を再検証する場合は
  `uv run python scripts/run-paper-archive-backtest.py --date YYYY-MM-DD --orders-dir out/paper-archive-YYYY-MM-DD/orders --book-dir out/paper-archive-YYYY-MM-DD/books --output-dir out/paper-archive-YYYY-MM-DD/backtest --run-gate --summary`
  を使う。`books.jsonl` 生成から OMS Paper backtest report / gate report / Markdown summary 出力まで実行する。
- report の機械判定は
  `uv run python scripts/check-paper-backtest-report.py --report out/paper-YYYY-MM-DD/backtest_report.json --output out/paper-YYYY-MM-DD/gate_report.json`
  を使う。必要なら `--min-profit-factor`, `--max-drawdown`, `--max-average-spread-bps` で閾値を厳しくする。
- summary だけ作り直す場合は
  `uv run python scripts/summarize-paper-backtest.py --date YYYY-MM-DD --report out/paper-YYYY-MM-DD/backtest_report.json --gate out/paper-YYYY-MM-DD/gate_report.json --metadata out/paper-YYYY-MM-DD/metadata.json --output out/paper-YYYY-MM-DD/summary.md`
  を使う。
- 判断記録は `docs/reports/paper-postmortem-template.md` をコピーして使う。`summary.md` を貼り、
  gate status と `no live change | continue paper | prepare live change proposal` の判断を明記する。

## 6. Abort Conditions

次のどれかなら寄り付き前でも無理に進めない。

- `universe-scanner` が失敗する
- `watchlist` が空、または当日 `valid_date` でない
- `daily_ohlcv` が極端に古い
- `health-check.py` で service / Supabase エラーが出る
- `feeder` が kabu 接続できない

## 7. Tomorrow's Minimum Path

明日はまず次の順で十分。

1. `universe-scanner` を手動実行
2. `health-check.py --check supabase` で `watchlist` / `daily_ohlcv` を確認
3. `docker compose ... up -d --build` で paper services 起動
4. `health-check.py --check supabase services` を確認
5. 寄り付き後に logs を監視
6. 9:15 以降に `report-paper-checkpoint.py --checkpoint open` を実行
7. 前引けに `--checkpoint midday`、大引けに `--checkpoint close` を実行
