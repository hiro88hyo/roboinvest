# daily_ohlcv CSV Archive

`/tmp/roboinvest-daily-ohlcv-500bd-bydate.csv` は再利用用に次へ退避する。

```bash
mkdir -p data/reference
cp /tmp/roboinvest-daily-ohlcv-500bd-bydate.csv data/reference/daily_ohlcv_500bd_bydate.csv
```

`data/` は `.gitignore` 対象なので、約 129MB のCSV本体はgitに載せない。

CSVを確認するだけなら:

```bash
uv run python scripts/import-daily-ohlcv-csv.py --dry-run
```

Supabase `daily_ohlcv` に再投入する場合:

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/import-daily-ohlcv-csv.py
```

このスクリプトは `(symbol, date)` でupsertするため、同じCSVの再実行は冪等。
J-Quants ingestion の代替ではなく、復旧・検証・バックテスト用のローカルアーカイブとして扱う。
