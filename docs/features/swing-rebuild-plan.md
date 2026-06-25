# Swing Strategy Rebuild Plan

作成日: 2026-06-24

このメモは ADR-0003 後の最初のスイング戦略開発メモです。目的は
「すぐ paper/live に戻すこと」ではなく、日足 OHLCV ベースで検証可能な
仮説、パラメータ、合格条件を事前登録することです。

## Context

既存 intraday RULE / AI judge stack は live 候補から外した。理由は
[ADR-0003](../adr/0003-strategy-layer-rebuild.md) と
[2026-06-24 relative momentum failure](../handoff/2026-06-24-relative-momentum-failure.md)
に記録済み。

再利用するもの:

- `daily_ohlcv` とローカル CSV archive
- Universe Scanner の日足スコアリング発想
- Gateway の資金管理、2% risk、per-order notional cap
- OMS Paper / Live、positions、kill switch、Dashboard
- replay gate / random baseline の考え方

再利用しないもの:

- 旧 RULE BUY
- 旧 AI judge を entry 根拠にする構成
- relative momentum / VWAP reclaim / oversold reclaim / RSI-VWAP recovery の
  intraday plugin
- 単日または特徴量 forward return だけで合格とする判断

## First Swing Hypothesis

初期仮説:

```text
高流動性で中期上昇トレンドにある銘柄が、20日移動平均付近への短期押しを作り、
終値で20日移動平均を回復した場合、その後 3-10 営業日の long-side follow-through
を持つ可能性がある。
```

これは逆張りの安値拾いではない。上昇トレンドが残っている銘柄だけを対象にし、
押し目からの回復を翌営業日寄りで買う。

## Pre-registered Candidate: `daily_trend_pullback_v0`

入力は日足 OHLCV のみ。初回は Supabase schema 変更なしで、既存
`daily_ohlcv(symbol,date,open,high,low,close,volume,turnover)` から検証する。

Entry はシグナル日終値で判定し、翌営業日寄りで約定したものとして評価する。
同じ銘柄は同時に 1 position だけ持つ。

初期パラメータ:

| Parameter | Value |
|---|---:|
| SMA short | 20 days |
| SMA long | 60 days |
| SMA long slope lookback | 20 days |
| ATR period | 14 days |
| Avg turnover period | 20 days |
| Min avg turnover | 200,000,000 JPY |
| Price range | 300-5,000 JPY |
| Min 20d return | +5% |
| Pullback lookback | 5 days |
| Pullback tolerance to SMA20 | +1% |
| Reclaim requirement | close >= SMA20 |
| Max distance above SMA20 at signal | +4% |
| ATR pct range | 1.5%-8.0% |
| Entry | next day open |
| Stop | entry - 1.5 ATR |
| Target | entry + 2.0R |
| Max hold | 10 trading days |
| Same-day stop/target collision | stop first |

Position sizing:

| Parameter | Value |
|---|---:|
| Starting capital | 1,000,000 JPY |
| Risk per trade | 1.0% of equity |
| Max notional per position | 20% of equity |
| Max concurrent positions | 5 |
| Lot size | 100 shares |
| Round trip commission | 9.9 bps each side |
| Slippage | 5 bps each side |

## Acceptance Gates

この候補は以下を満たすまで paper/live route に載せない。

- train / validation を時系列で分ける。
- train / validation の両方で、最低限の損益・PF・DD・月次安定性 gate を通す。
- validation profit factor `> 1.2`
- validation max drawdown `< capital * 0.10`
- validation closed trades `>= 30`
- validation total net PnL `> 0`
- validation positive month ratio `>= 0.55`
- validation worst month net PnL `>= -capital * 0.05`
- validation positive fold count が全 fold の概ね 2/3 以上
- random entry baseline を同じ保有日数・資金制約で上回る。
- パラメータ変更後は、変更後のパラメータを事前登録し直してから OOS 評価する。

## Initial Implementation Scope

最初の PR では production route は変更しない。

実装範囲:

- `scripts/backtest-swing-daily.py`
  - CSV / JSONL の `daily_ohlcv` archive を読む。
  - `daily_trend_pullback_v0` を固定パラメータで評価する。
  - trade list と summary JSON を出す。
  - acceptance gate を `PASS` / `FAIL` で明示する。
- focused unit tests
  - 日足指標計算
  - entry 条件
  - stop/target/max-hold exit
  - gate 判定

意図的にまだやらないこと:

- `strategy-rule` plugin 化
- `contracts/` schema 変更
- Dashboard 変更
- paper/live routing 有効化
- AI による entry

## Next Required Evidence

初回スクリプトが動いた後に必要な証拠:

1. `data/reference/daily_ohlcv_500bd_bydate.csv` で train / validation の実測。
2. validation が失格なら、原因を trade distribution と月次損益で確認する。
3. 採用候補が出ても、random baseline と別期間 OOS を通すまで paper 有効化しない。

## Initial Smoke Result

2026-06-24 に `scripts/backtest-swing-daily.py` を
`data/reference/daily_ohlcv_500bd_bydate.csv` で実行した。

Validation split は自動 70% / 30% split で、`validation_start=2025-10-29`。

結果:

| Split | Trades | Net PnL | PF | Max DD | Positive month ratio |
|---|---:|---:|---:|---:|---:|
| train | 170 | `+84,046.525` | `1.1503` | `110,977.777` | `0.5000` |
| validation | 106 | `+177,016.740` | `1.4231` | `120,285.828` | `0.6667` |

Gate 判定は `FAIL`。理由は validation max drawdown が `120,285.828` で、
許容値 `100,000` を超えたため。

この結果は「採用候補」ではなく「検証導線が動いた」証拠として扱う。
パラメータを緩めて合格扱いにしない。次は drawdown の発生月・銘柄集中・
gap stop 寄与を確認し、必要なら `daily_trend_pullback_v1` として別候補を
事前登録する。

### v0 Failure Anatomy

診断付き summary では validation の最大 DD は以下だった。

| Metric | Value |
|---|---:|
| Max DD amount | `120,285.828` |
| Peak date | `2026-02-25` |
| Trough date | `2026-03-23` |
| Peak equity | `212,374.234` |
| Trough equity | `92,088.406` |

Validation 月次:

| Month | Trades | Net PnL | Win rate |
|---|---:|---:|---:|
| 2025-10 | 3 | `-7,735.170` | `0.3333` |
| 2025-11 | 10 | `+27,589.355` | `0.6000` |
| 2025-12 | 13 | `+74,738.486` | `0.8462` |
| 2026-01 | 12 | `+47,159.502` | `0.7500` |
| 2026-02 | 13 | `+52,345.899` | `0.6154` |
| 2026-03 | 17 | `-86,032.843` | `0.1765` |
| 2026-04 | 13 | `+10,442.411` | `0.4615` |
| 2026-05 | 14 | `+89,592.272` | `0.5714` |
| 2026-06 | 11 | `-31,083.172` | `0.3636` |

Validation exit reason:

| Exit reason | Trades | Net PnL |
|---|---:|---:|
| gap_stop | 10 | `-112,224.782` |
| stop | 31 | `-285,780.537` |
| target | 26 | `+435,487.198` |
| max_hold | 39 | `+139,534.862` |

Interpretation:

- v0 has real right-tail capture: target exits and max-hold exits are strongly positive.
- The acceptance failure is drawdown concentration, not lack of gross upside.
- March 2026 and June 2026 are the first failure regimes to study.
- Gap stops are material. v1 must reduce exposure before adverse regimes or reduce per-trade
  risk; merely widening stops would likely increase tail loss.

## Candidate Direction: `daily_trend_pullback_v1`

`daily_trend_pullback_v1` is not yet accepted. It is a separate candidate to test,
not a retroactive change to v0.

Initial v1 hypothesis:

```text
The same daily trend-pullback entry has positive convexity, but v0 oversizes
positions for the March/June adverse regimes. A risk-throttled variant may keep
the target/max-hold upside while keeping OOS drawdown under 10%.
```

Pre-registered v1 changes to test next:

| Parameter | v0 | v1 |
|---|---:|---:|
| Risk per trade | 1.0% | 0.75% |
| Max concurrent positions | 5 | 4 |
| Max notional per position | 20% | 15% |
| Other entry/exit rules | unchanged | unchanged |

v1 cannot be accepted on the same validation slice alone because it is informed
by v0 diagnostics. It must pass:

- the same validation gate on the current archive,
- a random baseline comparison,
- and a later OOS slice or paper observation before any route enablement.

### v1 Diagnostic Result

2026-06-24 に `daily_trend_pullback_v1` を同じ archive で診断実行した。

| Split | Trades | Net PnL | PF | Max DD | Positive month ratio |
|---|---:|---:|---:|---:|---:|
| validation | 86 | `+3,061.558` | `1.0109` | `100,532.218` | `0.4444` |

Gate 判定は `FAIL`。

失敗理由:

- PF が `1.2` を大きく下回った。
- Max DD は `100,532.218` で、まだ `100,000` を超えた。
- Positive month ratio が `0.4444` に悪化した。

Interpretation:

- 単純な risk throttle / notional cap / max position 削減は、DD を十分に下げる前に
  勝ちトレードの捕捉も削ってしまう。
- v0 の問題は sizing だけではない。負け局面の entry regime を見分ける必要がある。

### Breadth Regime Diagnostic

v0 trade を、entry 前営業日の daily universe breadth で後処理フィルタする荒い診断も実施した。
ここでの breadth は `close > SMA20` および `SMA20 > SMA60 and close > SMA60` の比率。
これは正式候補ではなく、regime filter が検討に値するかの確認。

Validation の trend breadth threshold 診断:

| Min trend breadth | Trades | Net PnL | PF | Max DD | Gate |
|---|---:|---:|---:|---:|---|
| 0.35 | 68 | `+122,427.733` | `1.5187` | `120,285.828` | FAIL DD |
| 0.40 | 53 | `+24,654.210` | `1.1183` | `120,285.828` | FAIL PF/DD/month |
| 0.45 | 38 | `+28,948.556` | `1.1940` | `79,305.329` | FAIL PF/month |
| 0.50 | 31 | `+9,397.023` | `1.0699` | `78,789.151` | FAIL PF |
| 0.55 | 28 | `+1,502.787` | `1.0118` | `72,102.917` | FAIL PF/trades |
| 0.60 | 24 | `+6,912.310` | `1.0593` | `64,292.344` | FAIL PF/trades/month |

Interpretation:

- Breadth threshold can reduce DD, but it also removes too much edge.
- A single broad-market breadth cutoff is not sufficient.
- Next candidate should focus on entry quality / volatility regime / gap risk,
  not only total exposure reduction.

## Gap And Cluster Diagnostics

2026-06-24 追加診断で、Trade に以下の entry context を出力するようにした。

- `signal_close`
- `signal_sma_short`
- `signal_atr_pct`
- `signal_return_20d`
- `signal_avg_turnover`
- `entry_gap_pct`

v0 validation では `entry_gap_pct 0%..1%` の後処理フィルタだけを見ると
非常に良く見えた。

| Post-filter | Trades | Net PnL | PF | Max DD | Gate |
|---|---:|---:|---:|---:|---|
| `0 <= entry_gap < 1%` | 41 | `+205,983.473` | `2.9179` | `19,114.597` | PASS |
| above + `return_20d < 25%` | 36 | `+162,437.741` | `2.5341` | `19,114.597` | PASS |

ただしこれは v0 の成立済み trade を後から抜き出しただけで、除外された trade の代わりに
別 candidate が入る効果を含まない。正式シミュレーションでは失格した。

追加候補:

| Candidate | Change from v0 | Trades | Net PnL | PF | Max DD | Positive month ratio | Gate |
|---|---|---:|---:|---:|---:|---:|---|
| v2 | `0 <= entry_gap < 1%` | 119 | `+75,392.092` | `1.1521` | `205,843.264` | `0.4444` | FAIL |
| v3 | v2 + max 1 new position/day | 105 | `+122,162.345` | `1.3044` | `142,862.140` | `0.4444` | FAIL |
| v4 | v3 + `return_20d >= 8%` + `avg_turnover < 3B` | 90 | `+148,759.480` | `1.4340` | `120,128.695` | `0.4444` | FAIL |

Interpretation:

- Gap-confirmed entry is promising as a feature but not sufficient as a strategy.
- The post-filter PASS was misleading because it ignored replacement trades after slots opened.
- Max 1 new position/day reduces cluster exposure but still does not fix March/April drawdown.
- v4 recovers PF but still fails DD and positive month ratio.
- Do not promote v2/v3/v4 to paper. They are evidence that gap risk matters, not accepted
  strategies.

Next direction:

- Add random baseline for the same daily swing constraints before adding more filters.
- Add walk-forward / monthly fold gates so a candidate cannot pass by one strong rebound month.
- Investigate whether March/April failures are regime-specific enough to define a pre-registered
  external market filter. Do not use month labels or known bad dates as a filter.

## Random Baseline And Monthly Gate

2026-06-24 に `scripts/backtest-swing-daily.py` へ以下を追加した。

- `--selection ranked|random`
- `--random-seed`
- `--random-baseline-seeds`
- validation worst month gate: `worst_month_net_pnl >= -capital * 0.05`
- random baseline comparison: candidate net PnL must exceed best random baseline net PnL

この比較では、entry/exit/risk constraints は同じまま、同一日の eligible candidates の
選択順だけを seed 固定 random に変える。

### v0 With Random Baseline

`daily_trend_pullback_v0`, random seeds `1,2,3,4,5`:

| Selection | Seed | Trades | Net PnL | PF | Max DD | Positive month ratio | Worst month |
|---|---:|---:|---:|---:|---:|---:|---:|
| ranked | - | 106 | `+177,016.740` | `1.4231` | `120,285.828` | `0.6667` | `-86,032.843` |
| random | 1 | 112 | `+75,314.107` | `1.1964` | `116,784.077` | `0.7778` | `-92,459.922` |
| random | 2 | 115 | `+117,527.632` | `1.2488` | `111,044.515` | `0.7778` | `-78,476.903` |
| random | 3 | 117 | `+203,801.356` | `1.4457` | `98,926.766` | `0.5556` | `-65,614.223` |
| random | 4 | 107 | `+98,090.716` | `1.2373` | `93,217.828` | `0.4444` | `-41,931.873` |
| random | 5 | 110 | `+210,892.971` | `1.5720` | `122,734.500` | `0.6667` | `-73,321.810` |

v0 は best random baseline `+210,892.971` を下回り、DD と worst month でも FAIL。

### v4 With Random Baseline

`daily_trend_pullback_v4`, random seeds `1,2,3,4,5`:

| Selection | Seed | Trades | Net PnL | PF | Max DD | Positive month ratio | Worst month |
|---|---:|---:|---:|---:|---:|---:|---:|
| ranked | - | 90 | `+148,759.480` | `1.4340` | `120,128.695` | `0.4444` | `-63,673.313` |
| random | 1 | 92 | `+265,485.594` | `1.9400` | `113,339.384` | `0.6667` | `-49,074.456` |
| random | 2 | 91 | `+122,969.877` | `1.3208` | `116,116.949` | `0.7778` | `-85,905.955` |
| random | 3 | 88 | `+299,008.725` | `2.0638` | `77,275.469` | `0.7778` | `-60,805.249` |
| random | 4 | 96 | `+262,884.957` | `1.7147` | `130,725.837` | `0.6667` | `-105,596.249` |
| random | 5 | 89 | `+133,846.078` | `1.4502` | `80,595.471` | `0.5556` | `-58,890.399` |

v4 は best random baseline `+299,008.725` を大きく下回った。特に random seed 3 は
net/PF/DD が ranked candidate より良い。

Interpretation:

- 現在の `_entry_score` は alpha を持つ ranking ではない。
- 候補集合には右尾があるが、ranked selection がそれを選べていない。
- v0-v4 を paper/live に進めない。
- 次は candidate filter 追加ではなく、selection model を作り直すか、
  random baseline を上回る ranking feature を事前登録して検証する。

## Ranking Failure Anatomy

2026-06-24 に Trade 出力へ以下を追加した。

- `entry_score`
- `ranked_position`
- `candidate_count`

`daily_trend_pullback_v4` の ranked selection と random seed 3 を比較した。

| Selection | Trades | Net PnL | PF | Max DD | Positive month ratio | Worst month |
|---|---:|---:|---:|---:|---:|---:|
| ranked | 90 | `+148,759.480` | `1.4340` | `120,128.695` | `0.4444` | `-63,673.313` |
| random seed 3 | 88 | `+299,008.725` | `2.0638` | `77,275.469` | `0.7778` | `-60,805.249` |

Ranked position contribution:

| Selection | Rank bin | Trades | Net PnL | Win rate |
|---|---|---:|---:|---:|
| ranked | 1 | 90 | `+148,759.480` | `0.5000` |
| random seed 3 | 1 | 49 | `+209,839.495` | `0.6122` |
| random seed 3 | 2..3 | 27 | `+36,545.208` | `0.5556` |
| random seed 3 | 4..5 | 7 | `+33,053.328` | `0.5714` |
| random seed 3 | 6..inf | 5 | `+19,570.694` | `0.6000` |

Entry score contribution:

| Selection | Score bin | Trades | Net PnL | Win rate |
|---|---|---:|---:|---:|
| ranked | `<0.10` | 11 | `+40,759.977` | `0.6364` |
| ranked | `0.10..0.15` | 20 | `+51,271.815` | `0.6000` |
| ranked | `0.15..0.20` | 27 | `-21,953.752` | `0.3333` |
| ranked | `0.20..0.30` | 26 | `+52,524.070` | `0.5000` |
| ranked | `>=0.30` | 6 | `+26,157.371` | `0.6667` |
| random seed 3 | `<0.10` | 17 | `+84,265.757` | `0.7059` |
| random seed 3 | `0.10..0.15` | 35 | `+79,696.547` | `0.5429` |
| random seed 3 | `0.15..0.20` | 22 | `+108,954.387` | `0.5909` |
| random seed 3 | `0.20..0.30` | 11 | `+24,977.428` | `0.5455` |
| random seed 3 | `>=0.30` | 3 | `+1,114.606` | `0.6667` |

Monthly comparison:

| Month | ranked net | random seed 3 net |
|---|---:|---:|
| 2025-10 | `-27,954.578` | `-22,055.365` |
| 2025-11 | `+37,167.270` | `+53,002.013` |
| 2025-12 | `-1,494.533` | `+3,711.836` |
| 2026-01 | `+43,459.979` | `+50,718.613` |
| 2026-02 | `+131,698.309` | `+105,608.554` |
| 2026-03 | `-63,673.313` | `-60,805.249` |
| 2026-04 | `-49,942.224` | `+16,033.457` |
| 2026-05 | `+117,255.835` | `+147,183.295` |
| 2026-06 | `-37,757.264` | `+5,611.570` |

Interpretation:

- ranked selection always takes rank 1, but rank 1 is not reliably best.
- random seed 3 improved results mainly by mixing lower-ranked candidates, not by changing entry/exit.
- The current `_entry_score` overfits simple momentum/turnover shape and fails as a selector.
- The largest practical improvement is April/June damage reduction, not only higher upside capture.

Next pre-registered selection candidates should be evaluated before any paper route:

- `selection_equal_weight_random`: random selection as a baseline only, not tradable without a robust seed-free rule.
- `selection_score_band`: prefer score bands that survive OOS, not highest score.
- `selection_diversified`: limit same-day concentration while sampling across rank buckets.
- `selection_liquidity_middle`: avoid both very low and very high turnover if confirmed out-of-sample.

None of these are accepted yet. The next implementation should add a walk-forward runner that can
evaluate selection rules by fold without choosing rules from the same validation slice.

## Walk-forward Fold Diagnostics

2026-06-24 に `scripts/backtest-swing-daily.py` へ validation fold summary を追加した。

- `--walk-forward-folds N`
- default は `3`
- validation exit dates を時系列に等分割する。
- gate は positive fold count が全 fold の概ね 2/3 以上であることを要求する。

`daily_trend_pullback_v4`, 6 validation folds:

| Selection | Positive folds | Net PnL | PF | Max DD | Worst month |
|---|---:|---:|---:|---:|---:|
| ranked | `4/6` | `+148,759.480` | `1.4340` | `120,128.695` | `-63,673.313` |
| random seed 3 | `5/6` | `+299,008.725` | `2.0638` | `77,275.469` | `-60,805.249` |

Ranked fold detail:

| Fold | Period | Trades | Net PnL | PF | Max DD |
|---:|---|---:|---:|---:|---:|
| 1 | 2025-10-30..2025-12-02 | 15 | `+7,113.882` | `1.1131` | `39,214.756` |
| 2 | 2025-12-05..2026-01-07 | 12 | `+22,774.794` | `1.9398` | `10,094.761` |
| 3 | 2026-01-15..2026-02-13 | 16 | `+107,162.487` | `3.9635` | `16,823.798` |
| 4 | 2026-02-17..2026-03-23 | 14 | `-23,392.081` | `0.7043` | `69,217.365` |
| 5 | 2026-03-25..2026-04-30 | 12 | `-44,398.173` | `0.3402` | `55,153.783` |
| 6 | 2026-05-07..2026-06-12 | 21 | `+79,498.571` | `2.0877` | `58,601.172` |

Random seed 3 fold detail:

| Fold | Period | Trades | Net PnL | PF | Max DD |
|---:|---|---:|---:|---:|---:|
| 1 | 2025-10-30..2025-12-02 | 13 | `+22,615.356` | `1.4103` | `35,155.176` |
| 2 | 2025-12-08..2026-01-15 | 14 | `+62,331.085` | `3.4187` | `18,105.291` |
| 3 | 2026-01-21..2026-02-24 | 17 | `+103,391.918` | `3.8648` | `19,788.180` |
| 4 | 2026-02-25..2026-03-26 | 14 | `-62,097.364` | `0.2301` | `77,275.469` |
| 5 | 2026-03-27..2026-04-30 | 11 | `+19,972.864` | `1.4349` | `27,933.403` |
| 6 | 2026-05-07..2026-06-12 | 19 | `+152,794.865` | `5.0736` | `22,329.271` |

Interpretation:

- 3 folds is too coarse; it hides the March/April instability.
- 6 folds exposes that ranked selection loses in two adjacent folds.
- random seed 3 still has one severe fold and worst month below the risk gate, so it is not
  tradable either.
- Walk-forward fold stability is now a required diagnostic before any paper route.

## Deterministic Selection Diagnostics

2026-06-25 に seed 依存しない selection diagnostics を追加した。

- `selection=score_ascending`: low `_entry_score` first.
- `selection=score_middle`: closest to `_entry_score = 0.15` first.

`daily_trend_pullback_v4`, 6 validation folds:

| Selection | Trades | Net PnL | PF | Max DD | Positive months | Worst month | Positive folds | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| ranked | 90 | `+148,759.480` | `1.4340` | `120,128.695` | `0.4444` | `-63,673.313` | `4/6` | FAIL |
| random seed 3 | 88 | `+299,008.725` | `2.0638` | `77,275.469` | `0.7778` | `-60,805.249` | `5/6` | FAIL |
| score_ascending | 88 | `+202,187.179` | `1.7093` | `83,004.438` | `0.6667` | `-67,418.040` | `4/6` | FAIL |
| score_middle | 87 | `+333,338.477` | `2.2304` | `85,774.313` | `0.7778` | `-57,004.013` | `4/6` | FAIL |

`score_middle` fold detail:

| Fold | Period | Trades | Net PnL | PF | Max DD | Worst month |
|---:|---|---:|---:|---:|---:|---:|
| 1 | 2025-10-30..2025-12-01 | 13 | `+58,113.483` | `2.5343` | `22,055.365` | `-6,366.675` |
| 2 | 2025-12-02..2025-12-30 | 12 | `+28,014.214` | `2.3147` | `10,874.554` | `+28,014.214` |
| 3 | 2026-01-07..2026-02-12 | 13 | `+117,339.498` | `6.9430` | `12,425.893` | `+57,214.473` |
| 4 | 2026-02-13..2026-03-23 | 17 | `-35,510.066` | `0.6692` | `81,525.269` | `-62,548.064` |
| 5 | 2026-03-25..2026-05-07 | 14 | `-593.792` | `0.9898` | `37,580.724` | `-6,513.157` |
| 6 | 2026-05-08..2026-06-12 | 18 | `+165,975.140` | `7.2948` | `18,244.593` | `+35,538.306` |

Interpretation:

- `score_middle` is the best deterministic selector so far by net PnL and PF.
- It still fails because the Feb-Mar regime loss remains above the monthly loss gate.
- The current score is not monotonic. Highest score is not best; mid-score selection is better
  on this validation slice.
- This is not accepted because it was discovered from the same validation slice.

Next work:

- Add a fold-aware selection evaluation table that reports candidate selectors over train and
  validation consistently.
- If `score_middle` is promoted to a new pre-registered candidate, it must be evaluated on a
  later OOS slice and random baseline set before paper observation.

## Selection Comparison Report

2026-06-25 に `scripts/backtest-swing-daily.py --compare-selections` を追加した。
同じ candidate / same params / same folds で、複数 selection を一括比較する。

Example:

```bash
uv run python scripts/backtest-swing-daily.py \
  --candidate daily_trend_pullback_v4 \
  --input data/reference/daily_ohlcv_500bd_bydate.csv \
  --output-summary out/swing-daily/v4-selection-comparison-folds6.json \
  --walk-forward-folds 6 \
  --random-baseline-seeds 1,2,3,4,5 \
  --compare-selections
```

Result:

| Selection | Trades | Net PnL | PF | Max DD | Positive month ratio | Worst month | Positive folds | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| score_middle | 87 | `+333,338.477` | `2.2304` | `85,774.313` | `0.7778` | `-57,004.013` | `4/6` | FAIL |
| random seed 3 | 88 | `+299,008.725` | `2.0638` | `77,275.469` | `0.7778` | `-60,805.249` | `5/6` | FAIL |
| random seed 1 | 92 | `+265,485.594` | `1.9400` | `113,339.384` | `0.6667` | `-49,074.456` | `5/6` | FAIL |
| random seed 4 | 96 | `+262,884.957` | `1.7147` | `130,725.837` | `0.6667` | `-105,596.249` | `4/6` | FAIL |
| score_ascending | 88 | `+202,187.179` | `1.7093` | `83,004.438` | `0.6667` | `-67,418.040` | `4/6` | FAIL |
| ranked | 90 | `+148,759.480` | `1.4340` | `120,128.695` | `0.4444` | `-63,673.313` | `4/6` | FAIL |
| random seed 5 | 89 | `+133,846.078` | `1.4502` | `80,595.471` | `0.5556` | `-58,890.399` | `4/6` | FAIL |
| random seed 2 | 91 | `+122,969.877` | `1.3208` | `116,116.949` | `0.7778` | `-85,905.955` | `4/6` | FAIL |

Interpretation:

- Every selector still fails at least one risk gate.
- `score_middle` is best by net/PF among deterministic selectors and random seeds 1-5,
  but fails monthly loss gate by about `7,004` yen.
- `random seed 1` passes monthly loss but fails max DD.
- The bottleneck is now loss clustering in adverse regimes, not average expectancy.
- Do not relax the monthly loss gate. It exists specifically to prevent accepting this
  kind of lumpy strategy.

Next pre-registered candidate direction:

- `daily_trend_pullback_v5_score_middle_regime_guard`
- base candidate: v4
- selection: `score_middle`
- add only a pre-declared, date-agnostic regime guard that can be computed before entry.
- required evidence: must beat the full selection comparison table and pass worst-month /
  DD / fold gates on a later OOS slice.

## Train Gate And Multi-split Check

2026-06-25 に gate 判定を修正し、single split / selection comparison ともに
train gate と validation gate を分けて出したうえで、combined gate は両方を要求する
形にした。理由は、`score_middle` が validation では強いが、train 側の PF と月次安定性が
弱く、validation だけで候補昇格すると overfit を許すため。

`daily_trend_pullback_v4`, `score_middle`, default split
(`validation_start=2025-10-29`) の train / validation:

| Split | Trades | Net PnL | PF | Max DD | Positive month ratio | Worst month | Gate |
|---|---:|---:|---:|---:|---:|---:|---|
| train | 138 | `+27,410.713` | `1.0576` | `87,963.375` | `0.5000` | `-29,544.627` | FAIL |
| validation | 87 | `+333,338.477` | `2.2304` | `85,774.313` | `0.7778` | `-57,004.013` | FAIL |

Interpretation:

- `score_middle` は validation だけなら「惜しい」ように見える。
- しかし train PF が `1.2` を大きく下回り、positive month ratio も `0.55` 未満。
- validation 側も worst month が `-57,004.013` で `-50,000` の月次損失 gate を超える。
- よって `score_middle` を v5 として昇格させるのはまだ早い。

同日に `--comparison-validation-starts` を追加し、複数の validation start で同じ
selection comparison を集計できるようにした。

Example:

```bash
uv run python scripts/backtest-swing-daily.py \
  --candidate daily_trend_pullback_v4 \
  --input data/reference/daily_ohlcv_500bd_bydate.csv \
  --output-summary out/swing-daily/v4-selection-comparison-multisplit.json \
  --walk-forward-folds 6 \
  --random-baseline-seeds 1,2,3,4,5 \
  --compare-selections \
  --comparison-validation-starts 2025-04-01,2025-07-01,2025-10-29
```

Multi-split result:

| Selection | Pass splits | Validation net sum | Worst validation DD | Worst month | Min positive month ratio | Min positive fold ratio |
|---|---:|---:|---:|---:|---:|---:|
| score_middle | `0/3` | `+1,062,202.614` | `85,774.313` | `-57,004.013` | `0.6667` | `0.6667` |
| random seed 3 | `0/3` | `+979,844.963` | `77,275.469` | `-60,805.249` | `0.6667` | `0.8333` |
| random seed 4 | `0/3` | `+856,248.427` | `130,725.837` | `-105,596.249` | `0.6667` | `0.6667` |
| random seed 1 | `0/3` | `+801,244.253` | `113,339.384` | `-49,074.456` | `0.4667` | `0.6667` |
| score_ascending | `0/3` | `+643,694.483` | `83,004.438` | `-67,418.040` | `0.6667` | `0.6667` |
| random seed 2 | `0/3` | `+583,638.990` | `116,116.949` | `-85,905.955` | `0.7333` | `0.6667` |
| ranked | `0/3` | `+492,336.535` | `120,128.695` | `-63,673.313` | `0.4000` | `0.5000` |
| random seed 5 | `0/3` | `+378,838.522` | `80,595.471` | `-58,890.399` | `0.4667` | `0.5000` |

`score_middle` failed all three splits:

| Validation start | Main failures |
|---|---|
| 2025-04-01 | train PF `1.0878`; validation worst month `-57,004.013` |
| 2025-07-01 | train net `-26,182.102`; train PF `0.9160`; train positive month ratio `0.5000`; validation worst month `-57,004.013` |
| 2025-10-29 | train PF `1.0576`; train positive month ratio `0.5000`; validation worst month `-57,004.013` |

Conclusion:

- 現在の評価基準は妥当。むしろ validation only では甘かったため、train gate を combined gate
  に含める修正は必要だった。
- v4 / `score_middle` は期待値の右尾を持つが、train robustness と月次損失耐性が不足している。
- 次の作業は gate 緩和ではなく、entry 以前に観測可能な regime / candidate quality feature を
  追加し、multi-split で train と validation の両方が改善するかを見ること。
- `daily_trend_pullback_v5_score_middle_regime_guard` は、この multi-split 結果を上回る
  pre-registered feature が見つかるまで未登録のままにする。

## Market Context Diagnostics

2026-06-25 に Trade 診断へ signal 日時点の market context を追加した。これは entry 前に
観測可能なユニバース全体の状態であり、まだ売買ルールではない。

追加した列:

- `market_close_above_sma20_ratio`
- `market_trend_breadth_ratio`
- `market_positive_return_20d_ratio`
- `market_avg_return_20d`

`daily_trend_pullback_v4`, `score_middle`, default split の validation gate は引き続き
FAIL:

| Split | Trades | Net PnL | PF | Max DD | Positive month ratio | Worst month | Gate |
|---|---:|---:|---:|---:|---:|---:|---|
| train | 138 | `+27,410.713` | `1.0576` | `87,963.375` | `0.5000` | `-29,544.627` | FAIL |
| validation | 87 | `+333,338.477` | `2.2304` | `85,774.313` | `0.7778` | `-57,004.013` | FAIL |

Validation `market_trend_breadth_ratio`:

| Bucket | Trades | Net PnL | Win rate |
|---|---:|---:|---:|
| `<25%` | 10 | `+35,931.691` | `0.5000` |
| `25%..35%` | 20 | `+132,930.125` | `0.7000` |
| `35%..45%` | 22 | `+96,591.359` | `0.6818` |
| `45%..55%` | 12 | `-10,200.052` | `0.4167` |
| `>=55%` | 23 | `+78,085.353` | `0.5652` |

Train `market_trend_breadth_ratio`:

| Bucket | Trades | Net PnL | Win rate |
|---|---:|---:|---:|
| `<25%` | 4 | `-2,421.502` | `0.2500` |
| `25%..35%` | 21 | `-29,613.896` | `0.2857` |
| `35%..45%` | 31 | `+7,705.504` | `0.3548` |
| `45%..55%` | 22 | `+51,548.329` | `0.5455` |
| `>=55%` | 60 | `+192.278` | `0.4333` |

Interpretation:

- validation だけなら `market_trend_breadth_ratio 45%..55%` を避けたくなる。
- しかし train では同じ bucket が最も良く、ここを除外する guard は後付け overfit。
- 単純な trend breadth guard は v5 の根拠にならない。

Train `market_positive_return_20d_ratio 45%..55%` は `-50,027.757` と弱く、
validation でも `+4,485.781` と相対的に弱い。ただしこの bucket も、この時点では
候補生成のヒントに留める。次に使うなら、以下の条件を満たす別 candidate として
事前登録する。

- guard は entry 前に計算できる market context のみを使う。
- default split だけでなく multi-split の train / validation gate を両方改善する。
- random baseline と deterministic selectors の比較表を上回る。
- monthly loss gate は緩めない。

## Candidate: `daily_trend_pullback_v5`

2026-06-25 に exploratory candidate として `daily_trend_pullback_v5` を追加した。
v5 は v4 の entry / exit / sizing を変えず、entry 前に観測できる market context guard
だけを加える。

Change from v4:

| Parameter | v4 | v5 |
|---|---:|---:|
| `blocked_market_positive_return_20d_min` | - | `0.45` |
| `blocked_market_positive_return_20d_max` | - | `0.55` |

意味:

```text
signal 日時点で、ユニバース全体の 20日リターンが正の銘柄比率が 45% 以上 55% 未満なら、
market direction が中途半端な局面として新規 entry を見送る。
```

この候補は、validation diagnostics から着想しているため accepted candidate ではない。
multi-split で train / validation の両方を通せなければ paper route に載せない。

### v5 Default Split Result

`daily_trend_pullback_v5`, `selection=score_middle`, random seeds `1..5`,
`validation_start=2025-10-29`:

| Split | Trades | Net PnL | PF | Max DD | Positive month ratio | Worst month | Gate |
|---|---:|---:|---:|---:|---:|---:|---|
| train | 116 | `+33,956.788` | `1.0886` | `98,965.223` | `0.4615` | `-28,984.861` | FAIL |
| validation | 79 | `+319,259.862` | `2.2543` | `84,137.713` | `0.7778` | `-33,465.951` | PASS |

Combined gate は `FAIL`。

Failures:

- train PF `1.0886 <= 1.2`
- train positive month ratio `0.4615 < 0.55`

Interpretation:

- v5 は v4 の validation worst month `-57,004.013` を `-33,465.951` へ改善した。
- validation net は `+333,338.477` から `+319,259.862` に少し落ちたが、PF と月次損失は改善。
- ただし train robustness はまだ不足。validation だけの改善で採用しない。

### v5 Multi-split Selection Comparison

`validation_start=2025-04-01,2025-07-01,2025-10-29`:

| Selection | Pass splits | Validation pass splits | Train pass splits | Validation net sum | Worst validation DD | Worst month | Min positive month ratio | Min positive fold ratio |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| score_middle | `0/3` | `3/3` | `0/3` | `+1,055,478.605` | `85,036.957` | `-34,606.442` | `0.6667` | `0.6667` |
| random seed 3 | `0/3` | `0/3` | `0/3` | `+970,569.340` | `104,986.015` | `-72,859.409` | `0.6000` | `0.6667` |
| ranked | `0/3` | `0/3` | `0/3` | `+769,200.904` | `93,145.234` | `-55,019.128` | `0.4444` | `0.6667` |
| random seed 4 | `0/3` | `0/3` | `0/3` | `+736,570.158` | `131,807.564` | `-68,652.822` | `0.5333` | `0.6667` |
| random seed 5 | `0/3` | `2/3` | `0/3` | `+637,293.211` | `71,907.200` | `-47,549.854` | `0.5333` | `0.5000` |
| random seed 1 | `0/3` | `1/3` | `0/3` | `+556,491.646` | `65,339.879` | `-39,418.566` | `0.4667` | `0.8333` |
| random seed 2 | `0/3` | `0/3` | `0/3` | `+454,161.474` | `126,150.151` | `-84,327.914` | `0.5556` | `0.6667` |
| score_ascending | `0/3` | `0/3` | `1/3` | `+394,342.286` | `80,349.371` | `-68,702.380` | `0.6667` | `0.5000` |

`score_middle` failed splits:

| Validation start | Main failures |
|---|---|
| 2025-04-01 | train PF `1.0243`; train positive month ratio `0.5000` |
| 2025-07-01 | train net `-33,615.089`; train PF `0.8642`; train positive month ratio `0.4444` |
| 2025-10-29 | train PF `1.0886`; train positive month ratio `0.4615` |

Conclusion:

- v5 は validation risk gate を明確に改善し、`score_middle` は validation gate を `3/3`
  通した。
- しかし train gate が `0/3` なので、まだ live/paper 候補ではない。
- 次のボトルネックは validation 損失ではなく、前半期間の低 PF / 低 positive month ratio。
- v6 を作るなら、validation の損失削減ではなく、train 側の低期待値局面を説明できる
  feature が必要。

## Extended Market Context And Bucket Stability

2026-06-25 に market context diagnostics を拡張した。

追加した context:

- `market_positive_return_5d_ratio`
- `market_avg_return_5d`
- `market_positive_return_60d_ratio`
- `market_avg_return_60d`

さらに summary へ `diagnostics.bucket_stability` を追加した。これは train / validation の
同じ bucket を照合し、expectancy の符号が一致するか、combined PnL がどうかを並べる。
目的は、validation だけ悪い bucket を後付け filter にしないこと。

`daily_trend_pullback_v5`, `score_middle`, default split の結果:

- 十分な件数 (`train >= 10`, `validation >= 10`) で、train / validation 両方が
  negative expectancy の bucket は `0` 件。
- 少数件数では `market_avg_return_5d < -3%` が両方 negative だったが、
  train 3 trades / validation 1 trade で候補化不可。
- `signal_atr_pct < 2%` も両方 negative だったが、train 10 trades / validation 6 trades で、
  combined loss は `-1,408.276` と小さい。

Interpretation:

- v6 にそのまま使える単純な market context filter はまだ見つかっていない。
- train 低 PF は、単一 bucket の悪化というより、期間ごとの edge の弱さとして出ている。
- この段階で market context filter を増やすと overfit になる可能性が高い。

### Exploratory Selector: `rank_2_3_first`

Bucket stability では `ranked_position 2..3` が train / validation ともに positive だった。
これを受けて exploratory selector として `rank_2_3_first` を追加した。

Selector rule:

```text
same-day candidates の元 ranking で rank 2-3 を先に選び、
次に rank 1、次に rank 4-5、最後に rank 6 以上を選ぶ。
```

これは accepted selector ではない。`ranked_position 2..3` の後処理診断から着想したため、
multi-split で落とす目的の exploratory check として扱う。

Default split:

| Selection | Train net | Train PF | Train positive month ratio | Validation net | Validation PF | Validation DD | Validation worst month | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| score_middle | `+33,956.788` | `1.0886` | `0.4615` | `+319,259.862` | `2.2543` | `84,137.713` | `-33,465.951` | FAIL |
| rank_2_3_first | `+47,405.738` | `1.1313` | `0.4615` | `+222,445.558` | `1.6942` | `130,996.378` | `-71,917.270` | FAIL |

`rank_2_3_first` は train PF を少し改善したが、validation DD と worst month を壊した。

Multi-split comparison:

| Selection | Pass splits | Train pass splits | Validation pass splits | Validation net sum | Worst validation DD | Worst month | Min positive month ratio |
|---|---:|---:|---:|---:|---:|---:|---:|
| score_middle | `0/3` | `0/3` | `3/3` | `+1,055,478.605` | `85,036.957` | `-34,606.442` | `0.6667` |
| rank_2_3_first | `0/3` | `2/3` | `0/3` | `+685,677.594` | `130,996.378` | `-71,917.270` | `0.5333` |

Conclusion:

- `rank_2_3_first` は train 側を改善するが、validation risk を破壊する。
- 採用不可。v6 の方向としても弱い。
- 現時点で最も情報量がある事実は、`score_middle + v5 guard` が validation では安定するが、
  train の低 PF を説明できていないこと。
- 次は単純 filter / selector 追加ではなく、時系列 regime の違いを説明する特徴量、または
  strategy hypothesis 自体の見直しが必要。

## Independent Review And True Walk-forward Direction

2026-06-25 に gpt-5.5 へ独立レビューを依頼した。

Review conclusion:

- `v5 + score_middle` は paper/live-ready ではない。
- validation の強さは right-tail capture の証拠ではあるが、deployment evidence ではない。
- 最大の方法論上の問題は、500営業日サンプル上で validation diagnostics を再利用しすぎていること。
- `score_middle` と v5 guard は validation を見た後に発見され、重複する validation window で
  再評価されている。したがって validation net sum は独立 OOS の証拠として過大評価される。
- bucket stability で十分な件数の一貫した悪化 bucket が見つからないため、問題は単純な bad
  bucket ではなく time-varying edge の可能性が高い。

Review recommended next steps:

1. 非重複 OOS block の true walk-forward research harness を作る。
2. 500営業日より長い daily OHLCV history を集める。履歴不足なら paper/live ではなく証拠不足。
3. entry 時点で観測可能な market-wide regime taxonomy を事前登録し、conditional performance を見る。

Do not:

- PF / monthly loss / DD / train gate を緩める。
- validation-only success を昇格させる。
- diagnostics から手作業 bucket filter を追加し続ける。
- 既知の悪い月や日付を避ける。
- random seed の勝ちを deterministic rule 化せずに戦略扱いする。

## True Walk-forward Research Harness

2026-06-25 に `--walk-forward-research` を追加した。

Design:

- OOS block は非重複。
- 各 block の開始時点で、それ以前の train data だけを使って deterministic candidate /
  selector を 1 つ選ぶ。
- train gate を通る候補がある場合は、その中で train net / PF / DD の順に選ぶ。
- train gate を通る候補がない場合は `forced_best_train_no_gate_pass` と明示して、研究上の
  失敗として扱う。
- random seed は選択対象にせず、baseline として別集計する。
- 不完全な最終 OOS block は除外する。

追加した research gate:

- aggregate selected OOS gate が PASS。
- 各 block の selected train gate が全 block で PASS。
- selected OOS pass count が全 block の概ね 2/3 以上。
- aggregate selected OOS net が best random OOS baseline を上回る。

Example:

```bash
uv run python scripts/backtest-swing-daily.py \
  --input data/reference/daily_ohlcv_500bd_bydate.csv \
  --output-summary out/swing-daily/walk-forward-research-250train-60oos-fullblocks.json \
  --walk-forward-research \
  --min-train-days 250 \
  --oos-block-days 60 \
  --walk-forward-folds 6 \
  --random-baseline-seeds 1,2,3
```

Result:

| Metric | Value |
|---|---:|
| Full OOS blocks | `4` |
| Selected train pass count | `1/4` |
| Selected OOS pass count | `1/4` |
| Aggregate selected OOS trades | `114` |
| Aggregate selected OOS net | `+203,110.632` |
| Aggregate selected OOS PF | `1.4991` |
| Aggregate selected OOS max DD | `89,701.480` |
| Aggregate selected OOS worst month | `-42,345.294` |
| Aggregate selected OOS gate | PASS |
| Research gate | FAIL |

Research gate failures:

- `selected_train_pass_count 1 < 4`
- `selected_oos_pass_count 1 < 3`
- `selected_oos_total_net_pnl 203110.632 <= best_random_oos 333118.130`

Block detail:

| Block | OOS period | Selected | Selection reason | Train gate | OOS gate | OOS net | Best random OOS net |
|---:|---|---|---|---|---|---:|---:|
| 1 | 2025-06-04..2025-08-28 | v5 / score_ascending | forced best | FAIL | FAIL | `+7,327.103` | `+131,355.825` |
| 2 | 2025-08-29..2025-11-27 | v5 / score_middle | forced best | FAIL | FAIL | `-4,399.703` | `+61,676.155` |
| 3 | 2025-11-28..2026-02-27 | v4 / rank_2_3_first | forced best | FAIL | PASS | `+108,954.427` | `+191,458.233` |
| 4 | 2026-03-02..2026-05-29 | v4 / score_middle | train pass | PASS | FAIL | `+91,228.806` | `+103,154.552` |

Interpretation:

- Aggregate selected OOS gate だけを見ると PASS だが、これは採用根拠にしない。
- 4 block 中 3 block で train gate を通る候補がなく、forced selection になっている。
- OOS block gate を通ったのは 1/4 だけ。
- selected aggregate OOS は best random OOS baseline を下回る。
- true walk-forward では、現候補群はまだ研究 gate FAIL。

Next required work:

- これ以上 v0-v5 の bucket filter を足さない。
- daily OHLCV history を拡張し、同じ harness を長期履歴で再評価する。
- それまでは paper/live route へ載せない。
