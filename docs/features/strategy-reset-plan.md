# Strategy Reset Plan

作成日: 2026-06-23

2026-06-23 の paper 観測をもって、既存の intraday RULE / AI judge stack は
live 候補から外す。ここでは、既存 RSI / SMA / Bollinger / LLM judge の延命ではなく、
新しい戦略仮説を作るための評価契約を定義する。

関連する判断記録:

- [2026-06-23 Strategy Reset Decision](../handoff/2026-06-23-strategy-reset.md)
- [Trading Loss Control Review](trading-loss-control-review.md)
- [Execution Safety Improvement Plan](execution-safety-improvement-plan.md)

## Decision

次の戦略開発は、既存 strategy の threshold tuning ではなく strategy reset として扱う。

- 現行 RULE BUY は、明示的に再評価されるまで live entry 根拠にしない。
- AI は当面、entry accelerator ではなく、説明、否定、除外、事後分類に使う。
- 新戦略は、実装前に仮説、対象市場、entry/exit、risk、pass/fail 条件を文書化する。
- パラメータは評価前に固定し、結果を見た後の都合のよい調整は out-of-sample 合格扱いにしない。

これは execution platform の停止判断ではない。production compose、Pub/Sub、Supabase、
OMS Paper / Live の基盤は継続して使う。問題は「どのシグナルを BUY と見なすか」である。

## Current Diagnosis

2026-06-23 時点の観測では、既存 stack は以下の性質を持つと仮定する。

- RULE entry は current paper sample で負の期待値を示している。
- RSI / Bollinger 系の逆張り BUY は、下落中の銘柄を捕まえやすい。
- SMA / RSI / Bollinger を同じ RULE source に畳むと、戦略思想の違う signal が混ざる。
- Aggregator で source 単位に dominant pick すると、RULE 内 conflict が消えてしまう。
- AI は live entry の強い根拠としてまだ信頼できない。特に LLM の BUY は約定可能性、
  板、スリッページ、地合いまで含んだ期待値証明になっていない。
- stop、same-day re-entry block、market regime guard は損失を制限できるが、
  それ自体は alpha を作らない。

したがって、次の作業は「既存 BUY をどう救うか」ではなく、「どの局面だけ BUY するか」を
ゼロから定義する。

## First Hypothesis: Opening Range Breakout

最初の研究候補は opening range breakout とした。ただし 2026-06-23 の
end-to-end replay では、現時点の ORB は primary live candidate ではなく
棄却寄りの研究候補へ降格する。

仮説:

```text
寄り付き後 5-15 分で形成された range を、出来高拡大と VWAP 上維持を伴って
上抜ける銘柄は、短時間の long-side follow-through を持つ可能性がある。
```

この仮説は現行逆張り stack と思想が違う。安くなったから買うのではなく、
参加者が実際に上方向へ払っている局面だけを買う。

### Required Inputs

最低限必要な intraday inputs:

- symbol
- timestamp
- price
- VWAP
- volume ratio
- best bid / ask
- spread bps / ticks
- ask depth
- minutes from open
- opening range high / low
- price position versus opening range
- price position versus VWAP

現行 `ProcessedFeatures` には opening range high / low がない。実装時は
Feature Engine に追加するか、Strategy Rule 側で銘柄別 state として持つ。
contracts を変える場合は `contracts/` を Single Source of Truth として始める。

### Entry Rules

初期案:

- 対象は long-only day trade。
- `minutes_from_open >= 15` まで新規 BUY しない。
- opening range は 09:00-09:15 JST の high / low を使う。
- BUY 条件:
  - current price が opening range high を上抜ける。
  - price が VWAP 以上。
  - breakout 用に再定義した volume confirmation を満たす。
  - spread が許容範囲内。
  - ask depth が注文数量に対して十分。
  - market regime が `NORMAL` または明示的に許可された `CAUTION`。
- `RISK_OFF` / `CRASH` では新規 BUY しない。
- 高単価、高 ATR、低流動性、板薄銘柄は Gateway sizing で自然に 0 lot になることを許容する。

初期段階では、逆張り reclaim や VWAP mean reversion と混ぜない。

### Initial Parameter Set

最初の paper / replay 候補抽出では、以下を事前登録値として扱う。

| Parameter | Initial value | Note |
|---|---:|---|
| opening range | 09:00-09:15 JST | `minutes_from_open < 15` |
| first entry minute | 15 | 09:15 以降のみ |
| min minutes to close | 45 | 引け間際の新規を避ける |
| volume confirmation | disabled in first replay | 現行 `volume_ratio >= 2.0` は候補を全滅させたため再定義する |
| require VWAP alignment | true | `price >= vwap` |
| max stop risk | 300 bps | stop 距離が広すぎる候補を除外 |
| per-symbol max trades | 1 / day | ORB は同一銘柄の初回 breakout だけを評価する |

volume / spread / depth は archive の有無と定義に差があるため、最初の候補数診断では
任意 filter にする。live candidate 化する前には Gateway の execution gate で必須にする。

### Volume Confirmation Redesign

現行 `ProcessedFeatures.volume_ratio` は、直近 tick の `volume` を rolling average と比較する。
これは日足 / 汎用テクニカル指標としては使えるが、opening range breakout の
「breakout に参加が集まっているか」を直接表すものではない。

2026-06-23 に `ProcessedFeatures` へ以下の optional field を追加した。

- `cumulative_volume`: kabu `TradingVolume` をそのまま保持する日中累積出来高。
- `trade_volume_delta`: 同一 symbol の前回 tick からの出来高差分。初回 tick または
  累積出来高が巻き戻った場合は `None`。

これにより、今後の feature archive では opening range 中の累積出来高や breakout 前後の
出来高差分を再計算できる。過去 archive にはこの field がないため、初回診断では欠損扱い。

ORB 用の volume confirmation 候補:

- opening range 中の累積 volume が、同一銘柄の通常レンジより大きい。
- breakout 直前 N 分の累積 volume が、opening range 平均を上回る。
- breakout bar / tick cluster の volume が、直近 N サンプルより大きい。
- volume confirmation は単独 threshold ではなく、spread / ask depth と組み合わせる。

実装前に、Feature Engine が raw tick volume から intraday volume state を出せるか、
または Strategy Rule 側で銘柄別 state として集計するかを決める。
`cumulative_volume` / `trade_volume_delta` が archive に入るようになった後、まず診断スクリプトで
distribution を確認し、threshold を事前登録する。

### Exit Rules

初期案:

- stop は opening range low、VWAP、または ATR 由来のうち保守的な値を使う。
- target は risk distance の 1.0-1.5R を候補にする。
- 最大保有時間は 30-45 分を候補にする。
- 14:50 day closeout は既存 safety closeout として維持する。
- SELL / stop / closeout は opening BUY guard や entry gate で止めない。

任意の固定 `2%` stop だけに依存しない。銘柄価格と intraday range に対して
stop distance が大きすぎる場合は、ロットを 0 にして見送る。

## Secondary Hypotheses

Opening range breakout が最初の候補だが、並行して候補を整理しておく。
同時に実装・評価しない。

### VWAP Continuation

仮説:

```text
VWAP 上で押しを作り、再び VWAP から上方向に離れる銘柄は、
短時間の continuation を持つ可能性がある。
```

注意:

- VWAP mean reversion と混ぜない。
- entry は VWAP 上維持、volume expansion、range reclaim を要求する。
- 地合いが悪い日は continuation の失敗が増える前提で regime gate を必須にする。

2026-06-23 initial diagnostic:

`scripts/explore-vwap-continuation.py` を追加し、以下の代理条件で候補を数えた。

- 既に VWAP から `+50 bps` 以上上にいた銘柄。
- その後 VWAP 近辺 (`-10 bps` から `+35 bps`) まで押す。
- 再度 VWAP `+40 bps` 以上へ戻り、pullback high を上抜く。
- `max risk <= 200 bps`。
- 1 symbol / day 1 candidate。

2026-06-16 / 2026-06-18 / 2026-06-22 combined:

| Condition | Candidates | Avg 15m return | Avg 30m return | Positive 30m |
|---|---:|---:|---:|---:|
| no execution filter | 38 | `-16.926 bps` | `+12.731 bps` | `47.4%` |
| `spread_bps <= 30`, `spread_ticks <= 2`, `ask_depth_5 >= 1000` | 26 | `-33.984 bps` | `-33.108 bps` | `34.6%` |
| same execution filters + SMA uptrend | 25 | `-35.877 bps` | `-36.519 bps` | `32.0%` |

日別 execution filter 後:

| Date | Candidates | Avg 15m return | Avg 30m return | Positive 30m |
|---|---:|---:|---:|---:|
| 2026-06-16 | 0 | n/a | n/a | n/a |
| 2026-06-18 | 14 | `-94.955 bps` | `-61.489 bps` | `28.6%` |
| 2026-06-22 | 12 | `+37.149 bps` | `+0.003 bps` | `41.7%` |

解釈:

- 初期定義の VWAP continuation は、execution filter を入れると edge が消える。
- 6/22 だけに寄った可能性があり、6/18 では明確に悪い。
- 現時点では strategy plugin 化しない。

### VWAP Mean Reversion

仮説:

```text
過度に VWAP から下方乖離した銘柄のうち、板と約定が回復したものだけは
短時間の mean reversion を持つ可能性がある。
```

注意:

- 現行 RSI / Bollinger 逆張りの延長にしない。
- 下落中の安値更新を BUY しない。
- lower reclaim、VWAP 方向への回復、売り板消化、地合い正常を必須にする。
- adverse news / material move を検出できないうちは live 候補にしない。

### Relative Momentum

仮説:

```text
TOPIX、sector、watchlist peers に対して相対的に強く、出来高拡大と VWAP 上維持を
伴う銘柄は、同日 intraday continuation を持つ可能性がある。
```

注意:

- TOPIX / sector / peer basket の intraday baseline が必要。
- Universe Scanner の opportunity score とは別に intraday relative strength を測る。
- 既存 watchlist に乗っただけでは entry 条件にしない。

2026-06-23 initial diagnostic:

`scripts/explore-relative-momentum.py` を追加した。TOPIX / sector baseline がまだないため、
まず archived watchlist 内の peer universe を代理にした。

候補条件:

- return from open が `+100 bps` 以上。
- 同一 watchlist / minute の return from open peer percentile が `>= 0.80`。
- price が VWAP より `+20 bps` 以上上。
- intraday high を更新。
- 1 symbol / day 1 candidate。

2026-06-16 / 2026-06-18 / 2026-06-22 combined:

| Condition | Candidates | Avg 15m return | Avg 30m return | Positive 30m |
|---|---:|---:|---:|---:|
| no execution filter | 19 | `+81.097 bps` | `+95.909 bps` | `73.7%` |
| `spread_bps <= 30`, `spread_ticks <= 2`, `ask_depth_5 >= 1000` | 18 | `+60.253 bps` | `+74.961 bps` | `72.2%` |
| stricter: open return `>= 150 bps`, peer percentile `>= 0.90`, VWAP distance `>= 30 bps`, execution filters | 12 | `+85.946 bps` | `+89.168 bps` | `58.3%` |

日別 execution filter 後:

| Date | Candidates | Avg 15m return | Avg 30m return | Positive 30m |
|---|---:|---:|---:|---:|
| 2026-06-16 | 0 | n/a | n/a | n/a |
| 2026-06-18 | 8 | `+36.022 bps` | `+53.020 bps` | `50.0%` |
| 2026-06-22 | 10 | `+79.639 bps` | `+92.514 bps` | `90.0%` |

解釈:

- この時点では relative momentum が最も有望な次候補。
- ただし、これは forward return 診断であり、PnL backtest ではない。
- `StrategyEngine` の state は `(strategy name, symbol)` 単位なので、
  peer percentile のような cross-sectional feature は Strategy Rule plugin 内で自然に作れない。
- 2026-06-23 に `ProcessedFeatures` へ `return_from_open_bps` /
  `intraday_peer_percentile` / `intraday_high_price` を optional field として追加した。
- Feature Engine streaming state は、銘柄別始値、日中高値、watchlist 内の最新
  return-from-open 分布を使ってこれらを出す。
- Strategy Rule に `relative_momentum` plugin を追加した。過去 archive には新 field が
  無いため signal は出ない。次の feature archive 以降で end-to-end replay する。

2026-06-23 end-to-end replay:

過去 archive は momentum fields を持たないため、`scripts/enrich-relative-momentum-features.py`
で `return_from_open_bps` / `intraday_peer_percentile` / `intraday_high_price` を付与し、
`relative_momentum` plugin を実行した。

Base parameter set:

```text
STRATEGIES_ENABLED=relative_momentum
RELATIVE_MOMENTUM_MIN_RETURN_FROM_OPEN_BPS=100
RELATIVE_MOMENTUM_MIN_PEER_PERCENTILE=0.80
RELATIVE_MOMENTUM_MIN_VWAP_DISTANCE_BPS=20
ENTRY_MAX_SPREAD_BPS=30
ENTRY_MAX_SPREAD_TICKS=2
ENTRY_MIN_ASK_DEPTH_5=1000
BUY_LIMIT_OFFSET_TICKS=0
```

| Date | Signals | Closed trades | Total gross PnL | Total net PnL | Win rate | PF | No-fill rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-18 | 8 | 4 | `12,400` | `3,266.9304111` | `50.0%` | `1.177` | `33.3%` |
| 2026-06-22 | 10 | 1 | `10,500` | `4,336.61308575` | `100.0%` | n/a | `81.8%` |

`BUY_LIMIT_OFFSET_TICKS=1` の比較:

| Date | Signals | Closed trades | Total gross PnL | Total net PnL | Win rate | PF | No-fill rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-18 | 8 | 7 | `-9,800` | `-57,873.36` | `28.6%` | `0.443` | `6.7%` |
| 2026-06-22 | 10 | 6 | `34,700` | `3,521.89611505` | `66.7%` | `1.204` | `25.0%` |

解釈:

- 1 tick 上に出すと no-fill は改善するが、6/18 では悪い候補まで拾って大きく悪化した。
- momentum BUY は「置いていかれるなら見送る」ほうが現在のサンプルでは安全。
- offset は採用しない。約定率改善は、limit offset ではなく signal quality / timing 側で扱う。

Strict parameter set v1:

```text
RELATIVE_MOMENTUM_MIN_RETURN_FROM_OPEN_BPS=150
RELATIVE_MOMENTUM_MIN_PEER_PERCENTILE=0.90
RELATIVE_MOMENTUM_MIN_VWAP_DISTANCE_BPS=30
ENTRY_MAX_SPREAD_BPS=30
ENTRY_MAX_SPREAD_TICKS=2
ENTRY_MIN_ASK_DEPTH_5=1000
BUY_LIMIT_OFFSET_TICKS=0
```

| Date | Signals | Closed trades | Total gross PnL | Total net PnL | Win rate | PF | No-fill rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-18 | 5 | 3 | `31,200` | `19,676.9899758` | `66.7%` | `4.550` | `25.0%` |
| 2026-06-22 | 7 | 2 | `13,100` | `5,739.25714235` | `100.0%` | n/a | `55.6%` |
| 2026-06-23 | 3 | 2 | `-37,800` | `-57,611.636` | `0.0%` | `0` | `20.0%` |

v1 は 2026-06-18 / 2026-06-22 では通ったが、2026-06-23 の当日 archive を
production container から export して replay したところ明確に負けた。したがって
`150 bps` は live / paper route 候補から外す。6/23 の既存 paper 注文も同じ archive
replay で closed `12`、net `-94,693.541`、win rate `0%`、gate `FAIL` だったため、
当日地合いでは旧 stack と v1 の両方が不合格である。

Strict parameter set v2:

```text
RELATIVE_MOMENTUM_MIN_RETURN_FROM_OPEN_BPS=300
RELATIVE_MOMENTUM_MIN_PEER_PERCENTILE=0.90
RELATIVE_MOMENTUM_MIN_VWAP_DISTANCE_BPS=30
ENTRY_MAX_SPREAD_BPS=30
ENTRY_MAX_SPREAD_TICKS=2
ENTRY_MIN_ASK_DEPTH_5=1000
BUY_LIMIT_OFFSET_TICKS=0
```

| Date | Signals | Closed trades | Total gross PnL | Total net PnL | Win rate | PF | No-fill rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-18 | 5 | 3 | `31,200` | `19,676.9899758` | `66.7%` | `4.550` | `25.0%` |
| 2026-06-22 | 7 | 2 | `13,100` | `5,739.25714235` | `100.0%` | n/a | `55.6%` |
| 2026-06-23 | 1 | 0 | `0` | `0` | `0.0%` | n/a | `100.0%` |

v2 は 6/23 の負け trade を約定前に落とし、当日 net `0` まで止めた。これは
「本日のデータでは負けない」条件に近いが、1 件 no-fill で損益が 0 になっただけで、
positive edge の証明ではない。closed trade count は 3 日合計でも 5 件しかなく、
6/22 / 6/23 の no-fill rate が高い。現時点の扱いは first candidate ではなく
paper observation candidate であり、live-ready ではない。

`ENTRY_MIN_BOOK_IMBALANCE_5=-0.2` も試したが、6/23 は net `0` になった一方で
6/18 が net `-455.31` に落ちたため採用しない。book imbalance は単発 threshold ではなく、
急な売り板過多を見た直後の cooldown など、別実験として扱う。

Repro command:

```bash
bash scripts/run-relative-momentum-replay.sh \
  --archive-dir out/paper-archive-2026-06-22 \
  --date 2026-06-22
```

Use `--base` for the base threshold set or `--buy-limit-offset-ticks 1` for the
offset comparison. Default is strict / offset 0.

## Evaluation Contract

実装前に最低限この条件を固定する。

### In-Sample / Out-of-Sample

- In-sample は戦略仕様とパラメータ候補を作るためだけに使う。
- Out-of-sample 期間を先に固定する。
- OOS 開始後にパラメータを変更した結果は、同じ評価の合格扱いにしない。
- paper 実運用結果は OOS evidence として扱えるが、paper execution anomaly がある日は注記する。

### Metrics

最低合格条件:

- profit factor `> 1.2`
- max drawdown `< capital * 0.10`
- net PnL は手数料、税前、slippage / no-fill / partial-fill を含む評価でプラス
- closed trade 数が少なすぎない
- 1 銘柄または 1 日の大勝ちだけで PF が成立していない

補助指標:

- win rate
- expectancy per trade
- average win / average loss
- max adverse excursion
- hold time bucket 別 PnL
- entry time bucket 別 PnL
- symbol concentration
- no-fill rate
- average spread bps / ticks
- partial fill count

## First Archive Diagnostic

`scripts/explore-opening-range-breakout.py` を追加し、archived `ProcessedFeatures`
から opening range breakout 候補を数えられるようにした。これは PnL backtest ではなく、
候補数と短期 forward return を見るための診断ツールである。

2026-06-22 archive:

```bash
uv run python scripts/explore-opening-range-breakout.py \
  --features out/paper-archive-2026-06-22/features.jsonl \
  --output /tmp/opening-range-2026-06-22.csv
```

結果:

- strict initial condition (`volume_ratio >= 2.0`, VWAP required): candidates `0`
- stage diagnostics for 2026-06-22 strict + execution filters:
  - opening range crosses `107`
  - VWAP pass `79`
  - volume pass `0`
  - after VWAP, `volume_ratio` avg `1.0576`, median `1.0336`, max `1.3337`
  - after VWAP, `volume_ratio >= 1.2` は `5` 件、`>= 1.5` と `>= 2.0` は `0` 件
- no volume filter, VWAP required: candidates `38`, avg 15m return `+26.547 bps`,
  avg 30m return `+21.051 bps`
- no volume filter, VWAP required, `spread_bps <= 30`, `spread_ticks <= 2`,
  `ask_depth_5 >= 1000`: candidates `32`, avg 15m return `+28.574 bps`,
  avg 30m return `+28.148 bps`

2026-06-16 / 2026-06-18 / 2026-06-22 combined:

- strict initial condition: candidates `0`
- stage diagnostics for strict + execution filters:
  - opening range crosses `295`
  - VWAP pass `220`
  - volume pass `0`
  - after VWAP, `volume_ratio` avg `1.0407`, median `1.0183`, max `1.663`
  - after VWAP, `volume_ratio >= 1.2` は `10` 件、`>= 1.5` は `2` 件、
    `>= 2.0` は `0` 件
- no volume filter, VWAP required: candidates `89`, avg 15m return `+18.086 bps`,
  avg 30m return `+18.633 bps`
- no volume filter, VWAP required, `spread_bps <= 30`, `spread_ticks <= 2`,
  `ask_depth_5 >= 1000`: candidates `65`, avg 15m return `+19.338 bps`,
  avg 30m return `+19.359 bps`
- `volume_ratio >= 1.2` with the same execution filters: candidates `2`,
  avg 15m return `-3.588 bps`, avg 30m return `+2.074 bps`
- `volume_ratio >= 1.5` with the same execution filters: candidates `0`

初回診断の解釈:

- `volume_ratio >= 2.0` は現行 archive では機能しない。
- `volume_ratio >= 1.2` まで下げても、execution filter 後に 2 件しか残らない。
- 現行 `volume_ratio` は opening breakout 用の volume confirmation として使わない。
- VWAP alignment と基本的な execution filter を入れても候補は残る。
- forward return は正方向だが、約定、stop、target、no-fill、手数料、slippage を含んでいないため、
  live 候補の証拠ではない。
- 次は volume confirmation の定義を作り直す。候補は「直近 tick volume の rolling ratio」ではなく、
  opening range 中の累積出来高、直近 N 分出来高、または breakout bar / tick cluster の出来高を使う。
- 2026-06-23 以降の archive では `trade_volume_delta_after_vwap` の distribution も
  `scripts/explore-opening-range-breakout.py` で出力できる。

## ORB End-to-End Replay

2026-06-23 に `strategy-rule -> aggregator -> gateway -> oms-paper` の replay を接続し、
ORB の BUY signal を実際の limit order、no-fill、partial fill、day stop / target exit、
手数料、slippage 込みで評価した。

この過程で見つかった backtest plumbing の不足も修正した。

- Gateway backtest は `--entry-price` 省略時に `UnifiedTradeSignal.price` を使う。
- Gateway backtest の `OrderRequest.created_at` は、明示 `now` がない限り
  `signal.created_at` を使う。実行時刻を使うと過去 archive の板時刻とずれる。
- OMS Paper backtest は BUY order の `stop_loss_price` / `target_price` /
  `trailing_stop_pct` / `max_hold_days` を position へ引き継ぐ。
- OMS Paper backtest は book 更新時に DAY position の stop / target を評価し、
  自動 SELL を replay する。
- ORB plugin は同一 symbol / day で 1 回だけ signal を出す。

事前登録に近い初期条件:

```text
STRATEGIES_ENABLED=opening_range_breakout
ENTRY_MAX_SPREAD_BPS=30
ENTRY_MAX_SPREAD_TICKS=2
ENTRY_MIN_ASK_DEPTH_5=1000
ORB_MIN_BREAKOUT_VOLUME_DELTA=<disabled>
ORB_MIN_OPENING_RANGE_VOLUME=<disabled>
```

結果:

| Date | Signals | Approved BUY | Closed trades | Total gross PnL | Total net PnL | Win rate | PF | No-fill rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-16 | 0 | 0 | 0 | n/a | n/a | n/a | n/a | n/a |
| 2026-06-18 | 13 | 13 | 7 | `-21,400` | `-63,213.274` | `28.57%` | `0.205` | `30.0%` |
| 2026-06-22 | 18 | 18 | 7 | `-5,900` | `-61,937.261` | `28.57%` | `0.088` | `44.0%` |

比較として、同一 symbol / day の再 entry を許した旧 ORB 実装では以下だった。

| Date | Signals | Closed trades | Total gross PnL | Total net PnL | Win rate | PF | No-fill rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-18 | 33 | 12 | `18,700` | `-81,845.349` | `16.67%` | `0.602` | `40.0%` |
| 2026-06-22 | 32 | 15 | `7,300` | `-94,822.663` | `26.67%` | `0.166` | `36.17%` |

解釈:

- Forward return 診断では正方向に見えたが、実行可能な注文・exit・cost を入れると
  PF は合格条件から大きく外れた。
- 手数料と slippage が重く、gross PnL が小さい ORB では吸収できない。
- no-fill rate が高く、limit entry は良い候補ほど置いていかれる可能性がある。
- 同一銘柄の再 entry を抑えても、期待値は改善しなかった。
- 現時点の ORB は live / paper route 候補にしない。残す価値があるのは、
  将来 `trade_volume_delta` を含む新 archive で volume confirmation を再設計した場合だけ。

## Promotion Gates

新戦略を live candidate に戻す条件:

1. Plain-language strategy spec がある。
2. Parameters and costs が事前登録されている。
3. Archived / replay data で gate を通っている。
4. Production paper で少なくとも数営業日の観測がある。
5. Gateway reject reason と OMS Paper no-fill reason が説明可能である。
6. 現行 Project Kill Switch の条件を弱めていない。

live 反映は以下の順にする。

1. offline replay
2. strategy-rule output only, no route
3. aggregator / gateway log-only
4. paper route
5. live small size with existing hard safety controls

## Implementation Shape

既存アーキテクチャは維持する。

- Feature Engine:
  - opening range / relative strength / richer volume state を計算する候補。
  - contracts 追加が必要なら optional field で下流互換を保つ。
  - 2026-06-23 に ORB volume 用の `cumulative_volume` / `trade_volume_delta` と、
    relative momentum 用の `return_from_open_bps` / `intraday_peer_percentile` /
    `intraday_high_price` を追加済み。
- Strategy Rule:
  - `opening_range_breakout` を新しい plugin として追加済み。
  - 現行 `rsi_threshold`, `bollinger_breakout`, `sma_crossover` とは別 strategy name にする。
  - `STRATEGIES_ENABLED` で単独有効化できるようにする。
  - 初期実装は BUY only。`STRATEGIES_ENABLED=opening_range_breakout` で単独 backtest できる。
  - end-to-end replay の結果、現行 ORB は primary 候補から外す。
  - 2026-06-23 に `relative_momentum` plugin を追加済み。新 momentum feature が
    archive に存在する場合だけ signal を出す。
- Strategy AI:
  - 初期段階では BUY を出さない。
  - candidate signal の説明、除外理由、market context annotation に使う。
- Aggregator:
  - 新 strategy を RULE source に混ぜる場合、既存 RULE 内 conflict 問題を再発させない。
  - 将来的には strategy family を識別できる metadata が必要になる可能性がある。
- Gateway:
  - risk, liquidity, spread, depth, regime, sizing を最終 gate として維持する。
  - strategy が出した BUY を無条件に信用しない。

## Non-Goals

- 既存 RSI / SMA / Bollinger の threshold 調整で live 復帰する。
- 「悪い BUY signal の逆」をそのまま tradable signal として扱う。
- AI に単独 BUY 権限を戻す。
- paper 1 日の勝ちだけで live 復帰する。
- daily loss limit を緩める。
- Project Kill Switch の期限や条件を先送りする。

## Immediate Next Steps

1. ORB は現行条件では live / paper route に進めない。
2. 次の primary 研究候補は relative momentum に移す。VWAP continuation は初期診断では保留。
3. 次の feature archive で `return_from_open_bps` / `intraday_peer_percentile` /
   `intraday_high_price` が出ていることを確認する。
4. `STRATEGIES_ENABLED=relative_momentum` で strategy-rule -> aggregator -> gateway ->
   oms-paper の end-to-end replay を実行する。
5. 2026-06-23 以降の paper archive で `trade_volume_delta_after_vwap` の分布を確認し、
   volume confirmation が作れるかだけを別途検証する。
6. 新戦略は `strategy-rule` plugin として単独有効化し、既存 RULE strategies と混ぜない。
7. 評価は必ず `strategy-rule -> aggregator -> gateway -> oms-paper` の end-to-end replay で行う。

ORB plugin smoke:

```bash
STRATEGIES_ENABLED=opening_range_breakout \
  uv run python -m strategy_rule backtest \
  --input out/paper-archive-2026-06-22/features.jsonl \
  --output /tmp/orb-signals-2026-06-22.jsonl
```

2026-06-22 archive では、初期実装が `38` signals、execution filter 付きで `32` signals。
同一 symbol / day を 1 signal に絞った後は execution filter 付きで `18` signals。
