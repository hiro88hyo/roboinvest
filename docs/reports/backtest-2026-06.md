# Backtest Report 2026-06

作成日: 2026-06-13

## Scope

`scripts/parameter-sweep.py` を Cloud Supabase の `daily_ohlcv` export に対して実行した。

- input: `/tmp/roboinvest-daily-ohlcv-2y.csv`
- rows: 321,416
- symbols: 4,363
- date range: 2026-02-17 to 2026-06-11
- output: `/tmp/roboinvest-sweep-results.csv`
- parameter rows: 243

注意: `daily_ohlcv` は直近2年分ではなく 2026-02-17 以降しか取得できなかった。
この結果は短期サンプルの探索であり、live 資金増額や戦略採用の十分条件ではない。

## Method

固定 grid:

- RSI buy: `20`, `25`, `30`
- RSI sell: `70`, `75`, `80`
- SMA short: `5`, `10`, `20`
- SMA long: `25`, `50`, `75`
- Bollinger tolerance: `0.0`, `0.05`, `0.15`

期間は日付で前半 train / 後半 validation に分割した。PnL は `scripts/parameter-sweep.py`
の簡易日足シミュレーションで、手数料 `0.099%` とスリッページ `0.05%` を控除している。
数量は 1 株相当の比較用 PnL であり、実運用の lot sizing、板流動性、税、約定失敗は反映しない。

## Summary

- validation PF >= 1.0: 211 / 243
- train PF >= 1.2 and validation PF >= 1.2: 176 / 243
- validation total net PnL range: `-224,262.122197` to `395,951.690083`
- validation PF range: `0.700205` to `16.677721`
- validation max drawdown range: `8,451.21804` to `236,506.892666`
- validation Sharpe range: `-2.553769` to `8.55539`

上位は `sma_long=75` に強く偏った。これは短期サンプルの地合い依存の可能性が高い。

## Top By Validation Total Net PnL

| RSI buy | RSI sell | SMA short | SMA long | Bollinger tol | Val trades | Val PnL | Val PF | Val DD | Train PF |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 30 | 75 | 5 | 75 | 0.15 | 1260 | 395,951.690083 | 7.645263 | 12,308.7404 | 200.184629 |
| 30 | 75 | 10 | 75 | 0.15 | 1253 | 395,701.967093 | 7.642662 | 12,286.96411 | 200.184629 |
| 30 | 75 | 20 | 75 | 0.15 | 1260 | 395,653.468467 | 7.620483 | 12,286.96411 | 200.184629 |
| 30 | 80 | 5 | 75 | 0.15 | 1102 | 376,064.93228 | 7.747805 | 14,603.66215 | 433.593312 |
| 30 | 80 | 10 | 75 | 0.15 | 1094 | 375,646.60993 | 7.737768 | 14,735.29133 | 433.593312 |

## Top By Validation Profit Factor

| RSI buy | RSI sell | SMA short | SMA long | Bollinger tol | Val trades | Val PnL | Val PF | Val DD | Train PF |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 20 | 75 | 10 | 75 | 0.15 | 674 | 220,251.144159 | 16.677721 | 8,451.21804 | 128.993322 |
| 20 | 75 | 5 | 75 | 0.15 | 682 | 220,178.357344 | 16.240331 | 8,451.21804 | 128.993322 |
| 20 | 75 | 20 | 75 | 0.15 | 682 | 219,661.542223 | 16.011654 | 8,451.21804 | 128.993322 |
| 20 | 80 | 10 | 75 | 0.15 | 573 | 181,671.4475 | 15.630531 | 8,451.21804 | 1007.173284 |
| 20 | 80 | 5 | 75 | 0.15 | 581 | 181,785.338615 | 15.184584 | 8,451.21804 | 1007.173284 |

## Worst By Validation Total Net PnL

| RSI buy | RSI sell | SMA short | SMA long | Bollinger tol | Val trades | Val PnL | Val PF | Val DD |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| 30 | 70 | 20 | 25 | 0.0 | 6288 | -224,262.122197 | 0.700205 | 236,506.892666 |
| 30 | 75 | 20 | 25 | 0.0 | 6004 | -216,385.61273 | 0.715965 | 230,711.46253 |
| 30 | 80 | 20 | 25 | 0.0 | 5837 | -213,066.79501 | 0.714678 | 227,394.64183 |
| 20 | 70 | 20 | 25 | 0.15 | 5136 | -178,488.901944 | 0.707018 | 195,071.247723 |
| 30 | 70 | 20 | 25 | 0.05 | 6080 | -178,304.660925 | 0.757414 | 194,445.751054 |

## Strategy-Level Validation

CHK-10 用に、同じ `daily_ohlcv` export と train / validation split で
`DEFAULT_STRATEGIES` の各戦略を個別に評価した。production default parameter は以下を使った。

- `sma_crossover`: short SMA `5`, long SMA `25`, `min_gap_ratio=0.005`
- `rsi_threshold`: buy `25`, sell `75`
- `bollinger_breakout`: period `20`, tolerance `0.15`

| Strategy | Train trades | Train PnL | Train PF | Val trades | Val PnL | Val PF | Val DD | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `sma_crossover` | 23 | -4,282.20023 | 0.004616 | 766 | -220,169.09149 | 0.404078 | 229,196.658616 | remove from default |
| `rsi_threshold` | 121 | 24,816.994818 | 72.423374 | 523 | 141,873.043728 | 9.2978 | 5,770.89127 | keep |
| `bollinger_breakout` | 10 | 2,560.58157 | n/a | 282 | 81,132.007035 | 8.711496 | 7,857.76338 | keep |

`sma_crossover` は validation PF < 1.0 かつ validation PnL が大きくマイナスだったため、
`DEFAULT_STRATEGIES` と production compose/template の既定値から除外する。戦略実装と registry
登録は残すため、明示的に `STRATEGIES_ENABLED=sma_crossover,...` を指定すれば再評価・再有効化できる。

## Interpretation

現行 default の `RSI_BUY_THRESHOLD=25`, `RSI_SELL_THRESHOLD=75`,
`Bollinger tolerance=0.15` は短期 validation では上位群に近い。ただし `SMA long=75`
が支配的で、現行 strategy-rule の SMA 窓設定と同一ではない可能性があるため、このレポートだけで
production parameter を変更しない。CHK-10 では parameter 増強ではなく、PF<1.0 の
`sma_crossover` を default 有効戦略から外すだけに留める。

PF < 1.0 の組み合わせは 32 / 243 あった。特に `SMA short=20`, `SMA long=25` は
複数条件で悪く、短期窓が近すぎる crossover は除外候補。

## Long-Horizon Indicator Filter Sweep

2026-06-13 に J-Quants から `daily_ohlcv` を追加取得し、Supabase の履歴を 500 営業日に拡張した。

- Supabase `daily_ohlcv`: 2,109,773 rows, date range `2024-05-27` to `2026-06-12`
- J-Quants ingest result: 2,109,772 fetched rows, 500 trading days, 4,649 symbols
- export: `/tmp/roboinvest-daily-ohlcv-500bd-bydate.csv`
- focused sweep output: `/tmp/roboinvest-sweep-indicator-500bd.csv`
- focused parameter rows: 48

検証は現行 default に近い `RSI buy=25`, `RSI sell=75`, `Bollinger tolerance=0.15` を固定し、
以下の追加フィルタだけを振った。

- ADX max: none, `20`, `25`, `30`
- volume ratio min: `1.0`, `1.5`, `2.0`
- ATR stop multiple: none, `1.0`, `1.5`, `2.0`

信頼性を上げるため、`scripts/parameter-sweep.py` は `open/high/low/turnover` を読み込み、
ATR / ADX / volume ratio と expanding walk-forward 3 fold の PF を出すようにした。
また、500 営業日データで実行可能にするため、指標は銘柄ごとに事前計算する。

### Long-Horizon Summary

- validation PF >= 1.0: 43 / 48
- train PF >= 1.2 and validation PF >= 1.2: 7 / 48
- train PF >= 1.2 and validation PF >= 1.2 and worst fold PF >= 1.0: 0 / 48
- all 3 validation folds PF >= 1.2: 0 / 48
- validation PF range: `0.881124` to `3.558420`
- validation total net PnL range: `-130,417.313278` to `698,414.432023`
- validation worst fold PF range: `0.224910` to `1.097942`

現行に最も近い条件 (`ADX none`, `volume_ratio_min=1.0`, `ATR stop none`) は
validation PF `2.714523` / validation PnL `698,414.432023` と良いが、
train PF は `0.927750`、worst fold PF は `0.486933`。これは regime 依存が強く、
「現行戦略が長期で安定して正しい」とは言えない。

出来高フィルタは候補としては最も自然だった。`volume_ratio_min=2.0`, `ADX none`, `ATR stop none` は
train PF `1.449523`, validation PF `2.499580`, validation max drawdown `13,855.564190` で、
現行近似より取引数と DD を減らした。ただし worst fold PF は `0.635340` で、live 採用条件には届かない。

ATR stop は今回の OHLCV 日足シミュレーションでは validation を悪化させた。特に ADX なしで
`ATR stop=1.0` は train PF `1.707797` に対して validation PF `0.881124` / validation PnL
`-130,417.313278` となり、過剰適合の疑いが強い。

ADX max は validation PF を上げる組み合わせがある一方、train PF が弱いものが多く、robust 条件を満たさない。
例: `ADX max=20`, `volume_ratio_min=1.0`, `ATR stop none` は validation PF `3.558420` だが
train PF `1.011901`、worst fold PF `0.726951`。

### Long-Horizon Decision

この長期バックテストだけでは live parameter を変更しない。現行 RSI/Bollinger は直近 validation では
利益が出ているが、train と fold 安定性が不足しているため、戦略が正しいというより「直近 regime に合っていた」
と見るのが妥当。

次に paper で試す候補は `volume_ratio_min=2.0` を entry filter として追加する案。ただし live 反映はせず、
tick / order book 由来の spread、imbalance、約定可能性を含む別バックテストで再確認する。

2026-06-13 implementation note: `volume_ratio` は `ProcessedFeatures` と feature-engine に追加済み。
strategy-rule 側は `ENTRY_VOLUME_RATIO_MIN` で RSI / Bollinger の BUY entry のみを絞る。
デフォルトは unset / empty で無効のため、live 挙動は変えない。

OMS Paper backtest には execution quality metrics を追加済み。注文時点の最新板から
`spread_bps`, `fill_ratio`, `partial_fill_count`, opposite-side depth, order-book imbalance を
`backtest_report.json` に出す。これにより、日足 OHLCV の PnL だけでなく、板が薄い・スプレッドが広い・
部分約定が多い条件を別途落とせる。

feature-engine stream は板スナップショットを `/data/books` に Parquet 保存するようにした。
`scripts/order-book-archive-to-jsonl.py` で日付・銘柄ごとに `OrderBookSnapshot` JSONL へ戻せるため、
次回 paper の実データを OMS Paper backtest の `--books` に直接渡せる。

gateway stream は承認済み `OrderRequest` を `/data/orders/trade_mode=<mode>/date=YYYY-MM-DD/orders.jsonl`
に追記保存するようにした。これで次回 paper からは `orders.jsonl` と `books.jsonl` を揃えて、
OMS Paper backtest を実運用注文・実板データで再実行できる。
`scripts/run-paper-archive-backtest.py` は `/data/orders` と `/data/books` から `books.jsonl` を生成し、
OMS Paper backtest の `fills.jsonl`, `rejected.jsonl`, `positions.json`, `backtest_report.json` まで
まとめて出力する。
`scripts/check-paper-backtest-report.py` は `backtest_report.json` に対して PnL / PF / drawdown /
fill ratio / partial fill rate / spread の gate を判定する。`ENTRY_VOLUME_RATIO_MIN=2.0` を live に
進める判断は、この gate と長期 walk-forward の両方を満たすまで行わない。
Docker named volume 上の archive は `scripts/export-paper-archives.sh` でホスト側へコピーする。
`scripts/summarize-paper-backtest.py` または `run-paper-archive-backtest.py --summary` で Markdown summary を残す。
通常の paper 後検証は `scripts/run-paper-postmortem.sh` で export / backtest / gate / summary をまとめて実行する。

## Next Actions

1. paper で `ENTRY_VOLUME_RATIO_MIN=2.0` を設定し、live には入れず観測する。
2. 次回 paper で `/data/orders` と `/data/books` を採取し、OMS Paper backtest の execution quality を見る。
3. `scripts/parameter-sweep.py` の戦略ロジックを production `strategy-rule` とさらに近づける。
4. `sma_crossover` は default から外した状態で paper-mode 観測し、次回月次 sweep で再採用可否を判断する。
5. 長期 OHLCV でも robust 条件を満たさないため、この結果だけでは live 資金増額や live parameter 変更をしない。
