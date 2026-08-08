# Liquidity Research Archive

`LIQIMP1M_LOGDIFF_V0` の実現可能性検証に使うJ-Quants入力を、productionの
`daily_ohlcv` / `master_stocks` と分離して保存する。

このarchiveは `PAPER_INSPIRED_NOT_REPLICATION` のresearch入力であり、signal、
label、backtest、paper/live evidenceを生成しない。現行Project Kill Switch、
cluster-v1、凍結済みOOSの判定にも使用しない。

## Contents

`scripts/export-jquants-liquidity-research.py` は指定directoryへ次を保存する。

- `bars-daily-raw.jsonl`: J-Quants v2 `/equities/bars/daily` のraw行。未調整値に加え
  `AdjFactor`, `AdjO/H/L/C`, `AdjVo`, `Va`を保持する。
- `master-month-end-raw.jsonl`: 各完了月の最終TSE営業日時点の
  `/equities/master` raw行。`ProdCat`, `Mkt`, `S17`, `S33`, `ScaleCat`を保持する。
- `manifest.json`: ファイルSHA-256、byte size、完了fetch数、取得範囲、commit、
  API versionを記録する。

各API responseには一意なfetch IDとUTCの`source_received_at`を付け、末尾の
`fetch_metadata` markerで件数とraw payload SHA-256を固定する。markerがない中断
fetchは`--resume`時に完了扱いしない。

## Full historical export

1Password service account tokenは既存の専用ファイルだけから読み込む。値を表示しない。

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/export-jquants-liquidity-research.py \
  --start-date 2021-08-09 \
  --end-date 2026-06-30 \
  --output-dir data/liquidity-research-v0 \
  --resume \
  --concurrency 2
```

J-Quants Standardの現在の取得境界より前は再取得できない。2021-06-25から存在する
旧CSVを、調整済み列がないままこのarchiveへ混ぜない。

## Smoke export

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/export-jquants-liquidity-research.py \
  --start-date 2022-03-31 \
  --end-date 2022-03-31 \
  --output-dir /tmp/roboinvest-liquidity-research-smoke \
  --resume \
  --limit-bar-dates 1 \
  --limit-master-dates 1
```

`manifest.json`のhash確認前に、特徴量やforward returnを計算しない。exact LIQC1Mの
式が後日判明しても、このraw archiveは変更せず、変換artifactを別identityで作る。

## Validate and normalize

raw archiveのSHA-256、fetch marker、payload hash、date/code一意性、型、OHLC整合性を
検査し、research専用のtyped Parquetへ変換する。無売買日のnullは補完しない。

```bash
uv run python scripts/normalize-liquidity-research-archive.py \
  --input-dir data/liquidity-research-v0 \
  --output-dir data/liquidity-research-normalized-v0
```

出力は`normalized-manifest.json`と`validation-report.json`を含む。変換CLIはfeature、
forward return、rank、signal、portfolio outcomeを計算しない。V0の研究条件は
`docs/reports/liquidity-improvement-v0-prereg-2026-08-08.md`と
`research/liquidity/liqimp1m-logdiff-v0.json`を正とする。

## Build the outcome-blind feature cohort

事前登録済みV0のfeatureと月末cross-sectionだけを生成する。forward return、entry/exit、
PnL、PF、drawdownは結合・計算しない。

```bash
uv run python scripts/build-liquidity-improvement-features.py \
  --normalized-dir data/liquidity-research-normalized-v0 \
  --config research/liquidity/liqimp1m-logdiff-v0.json \
  --output-dir data/liquidity-research-features-v0
```

出力の`feature-manifest.json`はconfig、normalized manifest、builder、feature Parquetを
SHA-256で結ぶ。`cohort-audit.json`は件数と除外理由のみを持ち、銘柄別outcomeを持たない。
実績値は`docs/reports/liquidity-improvement-v0-feature-audit-2026-08-08.md`を参照する。
