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

## Research Freeze Before v6

2026-06-25 時点で `daily_trend_pullback_v0`〜`v5` は paper/live candidate
ではなく、research-only frozen baseline として扱う。validation slice 上で良く見えた
selection や filter を、v6 としてそのまま昇格しない。

v6 filter を追加する前に必須の診断:

1. `daily_ohlcv` のローカル履歴を J-Quants の利用可能範囲で最大化する。
   現行 v2 契約では API 応答上 `2021-06-25` 以降が利用可能。2018〜2021前半は
   この契約では取得できない。
2. 同じ gate / cost / slippage / OOS block 設計で、true walk-forward harness を
   v0〜v5 へ再適用する。
3. random baseline を `signal_set_random`, `universe_date_matched_random`,
   `symbol_matched_random_date` の3種類に分ける。
4. entry alpha / selector alpha / exit alpha を分解する。
   - 全候補 forward return
   - selector の selected / unselected 比較
   - fixed 2d / 5d / 10d exit
   - target / stop / max_hold exit
5. `_entry_score` は fold 内 rank IC と tail loss で診断する。
   validation で見つけた `score_middle` をそのまま採用しない。
6. market regime を事前定義し、regime 別成績を出す。
   - `broad_uptrend`
   - `narrow_leadership`
   - `transition_chop`
   - `broad_downtrend`
7. 約定モデル stress を追加する。
   - `exit_before_entry_at_open`
   - `limit_down_unfillable`
   - `gap_stop_additional_slippage`
8. v6 は、上記診断で train / OOS の両方に安定して出た仮説だけを
   事前登録してから実装する。

### 2026-06-25 Extended Walk-forward Result

J-Quants 現行 v2 契約で取得可能な最大範囲として、
`2021-06-25`〜`2026-06-24` のローカル CSV
`data/reference/daily_ohlcv_20210625_20260624_bydate.csv` を作成した。
行数は `5,064,172`、営業日は `1,221`、銘柄数は `4,989`。OHLCV 欠損は 0。

`v0`〜`v5` に true walk-forward research harness を再適用した結果:

| Metric | Value |
|---|---:|
| OOS blocks | `16` |
| selected train pass | `0/16` |
| selected OOS pass | `1/16` |
| selected OOS trades | `473` |
| selected OOS net PnL | `+63,944.906` |
| selected OOS PF | `1.0367` |
| selected OOS max DD | `180,086.501` |
| selected OOS positive month ratio | `0.5208` |
| best random OOS net PnL | `+189,308.685` |
| research gate | `FAIL` |

主な gate failure:

- selected OOS PF が `1.2` 未満。
- selected OOS max DD が資本の 10% を超過。
- selected train pass が `0/16`。
- selected OOS pass が `1/16`。
- selected OOS net PnL が best random OOS を下回った。

Regime 別の selected OOS:

| Regime | Trades | Net PnL | Win rate |
|---|---:|---:|---:|
| broad uptrend | `74` | `+54,574.292` | `0.4730` |
| transition_chop | `293` | `+83,963.528` | `0.4403` |
| broad downtrend | `76` | `-41,450.995` | `0.3553` |
| narrow_leadership | `30` | `-33,141.920` | `0.3333` |

Regime 別成績は合計値だけでは安定仮説にならない。block ごとの符号は以下。

| Regime | Blocks with trades | Positive blocks | Negative blocks |
|---|---:|---:|---:|
| broad uptrend | `10` | `4` | `6` |
| transition_chop | `16` | `8` | `8` |
| broad downtrend | `9` | `4` | `5` |
| narrow_leadership | `9` | `3` | `6` |

したがって regime filter 単体も v6 へ事前登録しない。

Alpha decomposition:

- 全候補 forward return は平均では小さくプラスだが、selector の上乗せは安定しない。
- `_entry_score` の fold 内 rank IC は概ね 0 近辺:
  - `v0`: `-0.0025`
  - `v1`: `-0.0101`
  - `v2`: `+0.0066`
  - `v3`: `+0.0066`
  - `v4`: `+0.0038`
  - `v5`: `-0.0041`
- fixed 10d exit は多くの candidate / selection で target/stop/max_hold より良く見えるが、
  これは exit 仮説であり、entry/selector alpha の合格根拠ではない。
- `score_middle` は validation slice 由来の候補として扱い、v6 へそのまま採用しない。

結論: `v0`〜`v5` は research-only frozen baseline のまま維持する。
この結果から v6 に事前登録できる安定仮説はまだない。特に
`_entry_score` 由来の selector filter は採用しない。

旧 combined stress execution model でも extended walk-forward を実行した。条件は
`exit_before_entry_at_open=true`, `limit_down_unfillable=true`,
`gap_stop_additional_slippage_rate=0.01`。今後この結果名は `all-stress` ではなく、
`open_exit_then_entry+limit_down_unfillable+gap_stop_additional_slippage` として扱う。

| Metric | conservative_no_reuse | combined execution model |
|---|---:|---:|
| selected OOS trades | `473` | `563` |
| selected OOS net PnL | `+63,944.906` | `+10,023.210` |
| selected OOS PF | `1.0367` | `1.0044` |
| selected OOS max DD | `180,086.501` | `213,231.772` |
| selected OOS worst month | `-60,008.305` | `-129,207.396` |
| selected OOS pass | `1/16` | `3/16` |
| best random OOS net PnL | `+189,308.685` | `+130,908.680` |
| research gate | `FAIL` | `FAIL` |

Stress 後も selected OOS は best random OOS を下回り、PF / DD / worst month の
各 gate を満たさない。約定モデルを厳しくすると edge はほぼ消えるため、
`v0`〜`v5` から paper/live candidate を復活させない。

## Pre-registered Exit Hypothesis: `daily_trend_pullback_exit_fixed10_v0`

`v0`〜`v5` の alpha decomposition では、entry / selector alpha は不安定だった一方、
fixed 10d exit は複数 candidate / selection で configured
target/stop/max_hold より良く見えた。ただしこれは entry filter 仮説ではない。
そのため v6 filter ではなく、exit 仮説だけを分離した新 candidate として反証する。

事前登録内容:

- Entry family は `daily_trend_pullback_v3` 相当。
  - `min_entry_gap_pct=0.0`
  - `max_entry_gap_pct=0.01`
  - `max_new_positions_per_day=1`
- Exit は stop / target を使わず、entry から 10 営業日後の終値で固定決済する。
- Selection は `ranked` と `rank_2_3_first` のみ。
  `score_middle` は validation 由来の発見として採用しない。
- 同じ true walk-forward harness / cost / slippage / random baseline / OOS block /
  execution stress で評価する。
- 合格条件は v0〜v5 と同じ。best random OOS を上回れない場合は棄却する。

この candidate は paper/live candidate ではない。`v0`〜`v5` で見えた exit
仮説を独立に falsify するための research-only candidate とする。

### `daily_trend_pullback_exit_fixed10_v0` Result

`2021-06-25`〜`2026-06-24` の extended walk-forward で評価した。

| Metric | No stress | All stress |
|---|---:|---:|
| selected OOS trades | `388` | `434` |
| selected OOS net PnL | `+176,057.509` | `+315,160.166` |
| selected OOS PF | `1.1151` | `1.1747` |
| selected OOS max DD | `195,021.993` | `288,351.343` |
| selected OOS worst month | `-84,679.473` | `-77,735.590` |
| selected train pass | `0/16` | `0/16` |
| selected OOS pass | `1/16` | `1/16` |
| best random OOS net PnL | `+674,082.935` | `+596,534.688` |
| research gate | `FAIL` | `FAIL` |

Interpretation:

- fixed 10d exit は configured target/stop/max_hold より selected OOS net を改善した。
- しかし同じ fixed 10d exit を使う random baseline が selected を大きく上回った。
- stress 後も PF / DD / monthly loss / train pass / random comparison を満たさない。
- よって、fixed 10d は「exit tailwind」としては残るが、
  現 entry / selector には採用可能な alpha がない。

追加で、entry forward return を同日 tradable universe 平均に対する excess return として
分解した。No-stress extended walk-forward の主な結果:

| Candidate | Entry avg 5d | Entry excess 5d | Entry excess 10d |
|---|---:|---:|---:|
| `v0` | `+0.2764%` | `+0.2293%` | `+0.4126%` |
| `v1` | `+0.3222%` | `+0.2727%` | `+0.4631%` |
| `v2` | `+0.3956%` | `+0.3250%` | `+0.4485%` |
| `v3` | `+0.3956%` | `+0.3250%` | `+0.4485%` |
| `v4` | `+0.2845%` | `+0.2223%` | `+0.2604%` |
| `v5` | `+0.1182%` | `+0.1225%` | `+0.2675%` |
| `fixed10_v0` | `+0.3956%` | `+0.3250%` | `+0.4485%` |

Selector excess 5d は candidate によって符号が割れる。

| Candidate | Selection | Selected excess 5d | Unselected excess 5d | Delta |
|---|---|---:|---:|---:|
| `v0` | `ranked` | `+0.1950%` | `+0.2455%` | `-0.0505%` |
| `v1` | `ranked` | `+0.1753%` | `+0.3306%` | `-0.1553%` |
| `v3` | `ranked` | `+0.5181%` | `+0.2927%` | `+0.2254%` |
| `v3` | `rank_2_3_first` | `+0.3941%` | `+0.3134%` | `+0.0807%` |
| `v5` | `rank_2_3_first` | `+0.2778%` | `+0.0500%` | `+0.2278%` |
| `fixed10_v0` | `ranked` | `+0.5181%` | `+0.2927%` | `+0.2254%` |
| `fixed10_v0` | `rank_2_3_first` | `+0.3941%` | `+0.3134%` | `+0.0807%` |

Entry candidate pool には小さい正の同日 universe excess がある。しかし、
OOS の selected strategy は best random OOS を大きく下回り、selector excess も
v0/v1 ではマイナス、v3/fixed10 でも fold / random comparison を通らない。
したがって、この excess は paper/live に使える selection alpha として扱わない。

追加で `fixed10_v0` の `signal_set_random` を seed `1`〜`20` へ拡張して確認した。
candidate subset は `daily_trend_pullback_exit_fixed10_v0` のみ、random baseline kind は
`signal_set_random` のみ。

| Metric | Value |
|---|---:|
| random seeds | `20` |
| net PnL min / median / mean / max | `+40,129.505 / +260,804.272 / +303,499.867 / +674,082.935` |
| PF min / median / mean / max | `1.0257 / 1.1867 / 1.2253 / 1.5432` |
| max DD min / median / mean / max | `93,107.794 / 213,511.940 / 213,071.684 / 366,359.312` |
| worst month min / median / mean / max | `-119,345.705 / -84,621.440 / -82,733.914 / -45,824.817` |
| gate-like random passes | `0/20` |

この結果は、fixed10 の signal set に平均的な追い風がある可能性を示すが、
DD と月次損失が大きく、seed 間ばらつきも大きい。特に seed を選べば良く見えるため、
特定 seed を採用することはしない。次に扱うなら、random selection ではなく
事前登録した deterministic basket / risk-scaled basket 仮説として反証する。

結論: `daily_trend_pullback_exit_fixed10_v0` も research-only rejected candidate とする。
次に進むなら、exit ではなく entry family 自体を変える。

## Pre-registered Basket Hypothesis: `daily_trend_pullback_fixed10_hash_v0`

`fixed10_v0` の seed `1`〜`20` 診断では、signal set random の平均損益はプラスだった。
一方で、特定 seed の採用は後付けになるため禁止する。また DD と worst month は
gate を大きく超えている。

そこで、random seed を選ばず、銘柄・signal date の固定 hash で候補順を決める
deterministic basket を research-only candidate として反証する。これは selector alpha
仮説ではなく、「signal set 平均 edge が risk scaling 後に gate 内へ収まるか」の仮説。

事前登録内容:

| Parameter | Value |
|---|---:|
| Entry family | `daily_trend_pullback_v3` 相当 |
| Exit | fixed 10 trading days close |
| Selection | `stable_hash` |
| Hash salt | `fixed10_hash_v0` |
| Risk per trade | `0.35%` of equity |
| Max notional per position | `8%` of equity |
| Max new positions per day | `1` |
| Max concurrent positions | `5` |

合格条件は v0〜v5 と同じ。特に、同じ risk-scaled signal set random baseline を
上回れない場合は棄却する。

低頻度戦略用の `block_stability_gate` も、既存 `research_gate` とは別に事前登録する。
これは project kill switch や aggregate OOS gate を置き換えない。
1 block あたりの trade count が 30 未満になりやすい候補で、block stability を
分解して見るための research-only gate とする。block ごとの full `check_gate` は
低頻度 strategy では trade count 条件で構造的に FAIL しやすいため、正式な
block stability 判定には使わない。

`low_frequency_research_gate` / `block_stability_gate`:

| Requirement | Value |
|---|---:|
| Aggregate OOS gate | `PASS` |
| Train full-gate pass count | `>= 1/2` of blocks |
| Positive OOS block ratio | `>= 2/3` |
| Median OOS block trades | `>= 15` |
| Worst OOS block net PnL | `>= -50,000` |
| Random baseline count | `>= 100` |
| Selected net percentile vs random | `>= 0.75` |

この補助 gate を通っても paper/live candidate にはしない。次に必要なのは、
同じ事前登録条件で別 stress / 別 OOS 設計に耐えるかの確認。

### `daily_trend_pullback_fixed10_hash_v0` Result

`2021-06-25`〜`2026-06-24` の extended walk-forward で、candidate subset を
`daily_trend_pullback_fixed10_hash_v0` のみにして評価した。

| Metric | conservative_no_reuse | open_exit_then_entry |
|---|---:|---:|
| selected OOS trades | `293` | `324` |
| selected OOS net PnL | `+109,938.707` | `+257,750.440` |
| selected OOS PF | `1.2264` | `1.5589` |
| selected OOS max DD | `98,959.395` | `67,697.220` |
| selected OOS positive month ratio | `0.5319` | `0.6170` |
| selected OOS worst month | `-39,709.841` | `-31,447.945` |
| selected train pass | `8/16` | `10/16` |
| selected OOS pass | `0/16` | `0/16` |
| selected positive OOS blocks | `9/16` | `11/16` |
| selected positive OOS block ratio | `0.562` | `0.688` |
| selected OOS min block trades | `10` | `13` |
| selected OOS median block trades | `19.0` | `21.5` |
| selected OOS worst block net PnL | `-44,779.993` | `-48,757.261` |
| random baselines | `60` | `60` |
| selected rank by net vs random | `24/61` | `6/61` |
| selected net percentile vs random | `0.607` | `0.902` |
| random gate-like pass count | `19/60` | `30/60` |
| best random OOS net PnL | `+258,284.201` | `+374,166.996` |
| research gate | `FAIL` | `FAIL` |
| low-frequency research gate | `FAIL` | `PASS` |

`conservative_no_reuse` は PF / DD / worst month は改善したが、positive month ratio と
random comparison を満たさない。`open_exit_then_entry` は aggregate OOS では PF / DD /
positive month / worst month を満たし、random20 分布でも net 上位 `6/61` まで
改善した。しかし best random OOS は `+374,166.996` で、selected の
`+257,750.440` を上回った。それでも research gate は
`selected_train_pass_count`、`selected_oos_pass_count`、random comparison で FAIL。

事前登録した `low_frequency_research_gate` では、No-stress は
`positive_month_ratio`, `positive_block_ratio`, `selected_net_percentile` で FAIL。
`open_exit_then_entry` は当時の暫定 random60 gate では PASS。ただしこれは既存
`research_gate` を置き換えるものではなく、
低頻度候補を次段階の研究対象に残すための補助 gate である。

Execution stress sensitivity:

- `fixed_hold` exit では gap stop / limit-down stress は実質的に効かない。
- 過去に `All-stress` と呼んだ改善は、`exit_before_entry_at_open=true` による
  同日入れ替え許可で説明できる。
- `open_exit_then_entry` 単独と旧 `all-stress` の結果は一致した。
- したがって旧 `all-stress` PASS は「厳しい約定 stress に耐えた」という意味ではなく、
  「同日入れ替えを許す運用仮定では補助 gate を通る」という意味に限定する。
- 今後の結果名は以下に分解する:
  `conservative_no_reuse`, `open_exit_then_entry`, `limit_down_unfillable`,
  `gap_stop_additional_slippage`。

Trade-level delta between no-stress and `exit_before_entry_at_open=true`:

| Metric | Value |
|---|---:|
| Common OOS trades | `181` |
| Added OOS trades | `143` |
| Removed OOS trades | `112` |
| Common net PnL | `+122,228.708` |
| Added net PnL | `+135,521.732` |
| Removed net PnL | `-12,290.001` |
| Added PF | `1.7308` |
| Removed PF | `0.9415` |

改善は単なる trade count 増加ではなく、同日 exit で枠・資金が空くことにより、
候補選択が入れ替わる効果が大きい。このため、この candidate の次段階評価では
`exit_before_entry_at_open=true` を運用可能な前提として別途検証する必要がある。

Operational feasibility check:

- `oms-paper` / `oms-live` の streaming runner は、1 `run_once` 内では
  raw market data を先に取り込み、stop / target / `max_hold_days` exit を評価してから
  `paper-orders` / `live-orders` を処理する。この局所順序だけを見ると exit 優先に近い。
  `oms-paper` については、swing `max_hold_days` exit の position delete が同じ cycle の
  BUY position insert より先に書かれることを unit test で固定した。
- `oms-paper.swing_monitor.find_max_hold_due_swing_positions` を追加し、market data なしで
  寄り時点の fixed-hold 期限到達 swing position を抽出できるようにした。
- `oms-paper` に paper-only の `run_opening_swing_max_hold_exits()` を追加した。
  これは CLI / scheduler へは未接続で、cached bid がある期限到達 swing position だけを
  明示的に SELL し、trade insert と position delete を返す検証用 entry point である。
- 手動 CLI `oms-paper opening-swing-exits` を追加した。デフォルトでは
  raw-market-data を 1 batch pull して book cache を温めてから
  `run_opening_swing_max_hold_exits()` を実行する。`--book-warmup-batches 0` なら
  Pub/Sub warmup なしで実行する。
- ただし exit 判定は板更新が届いた symbol のみで発火する。日足 fixed 10d exit を
  寄り前または寄り直後に全対象へ自動発火する scheduler は現時点ではない。
- Gateway の同日再エントリ禁止は `holding_type=day` の BUY のみで、swing BUY には
  直接かからない。一方、資金枠は `positions` 由来の capital-in-use を読むため、
  SELL 約定と position delete が BUY 判定より先に確定している必要がある。
- よって `exit_before_entry_at_open=true` は現行 stack が保証する自然な挙動ではない。
  paper-only で検証するには、寄り exit バッチ、SELL 約定確認、positions 更新、
  その後の BUY signal 発行という順序を明示的に実装・観測する必要がある。
- この運用仮定が paper で再現できない場合、`open_exit_then_entry` の改善は採用根拠から外し、
  no-stress 側の `low_frequency_research_gate=FAIL` を主判定にする。

OOS block length sensitivity:

`exit_before_entry_at_open=true`、candidate subset は
`daily_trend_pullback_fixed10_hash_v0`、random baseline は各 block length で
`10 seeds * 3 kinds` とした。60営業日 block だけは random20 診断も別途実行済み。

| OOS block days | Blocks | Net PnL | PF | Max DD | Positive blocks | Median block trades | Selected rank vs random | Low-frequency gate | Research gate |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `40` | `24` | `+174,718.368` | `1.4046` | `53,095.617` | `14/24` | `13.0` | `2/31` | `FAIL` | `FAIL` |
| `60` | `16` | `+257,750.440` | `1.5589` | `67,697.220` | `11/16` | `21.5` | `6/61` | `PASS` | `FAIL` |
| `80` | `12` | `+219,511.597` | `1.4437` | `58,666.535` | `10/12` | `28.5` | `6/31` | `PASS` | `FAIL` |
| `120` | `8` | `+262,104.272` | `1.5087` | `64,077.569` | `7/8` | `46.5` | `1/31` | `PASS` | `FAIL` |

40営業日 block は aggregate は悪くないが、block が短すぎて median trades が `15`
未満、positive block ratio も `2/3` 未満になった。60/80/120営業日では
low-frequency gate を通る。ただし全て既存 research gate は FAIL。

重要な評価基準上の発見:

- 現在の `selected_oos_pass_count` は各 60営業日 block に `check_gate` をそのまま
  適用している。
- `check_gate` は `trade_count >= 30` を要求するため、この candidate のように
  1 block あたり概ね `10`〜`22` trades の低頻度 strategy では、block OOS pass が
  構造的に 0 になりやすい。
- これは project kill switch の `OOS PF > 1.2` / `max DD < 10% capital` を
  弱める話ではない。block stability の測り方として、full gate pass count ではなく
  positive block ratio、block drawdown、aggregate OOS gate を分けて評価する必要がある。

結論: `daily_trend_pullback_fixed10_hash_v0` は paper/live rejected のまま固定する。
ただし、ここまでの research-only 候補の中では初めて `open_exit_then_entry`
aggregate OOS が PF / DD / positive month / worst month を満たし、random20 分布の
上位に入った。
次の作業はパラメータ探索ではなく、
評価 harness の block stability gate を妥当な低頻度用指標へ分解したうえで、
operational-consistent な continuation candidate として再評価すること。

### Research-continuation Candidate: `daily_trend_pullback_fixed10_hash_v1_operational`

`daily_trend_pullback_fixed10_hash_v1_operational` を research-continuation candidate
として再登録する。paper/live candidate ではない。

| Parameter | Value |
|---|---:|
| Based on | `daily_trend_pullback_fixed10_hash_v0` |
| Stable hash salt | `fixed10_hash_v0` unchanged |
| Risk params | unchanged initially |
| Exit | fixed 10 TSE business sessions |
| Scheduled exit | `scheduled_exit_date = entry_date + 10 TSE business sessions` |
| `open_exit_then_entry` exit price | open/bid/slippage |
| `close_exit` capital rule | same-day BUY cannot reuse capital from same-day close exits |

この v1 は、旧 `all-stress` の良化を「厳しい stress 耐性」として扱わず、
`open_exit_then_entry` が paper で観測可能な運用順序かどうかを分離して検証するための
継続研究対象である。

### `daily_trend_pullback_fixed10_hash_v1_operational` Result

`open_exit_then_entry`, OOS block 60 trading sessions, random seeds `1..100`,
random baseline 3種類で再評価した。結果ファイル:
`out/swing-daily/walk-forward-research-20210625-20260624-fixed10-hash-v1-operational-open-exit-random100-capital-sensitivity.json`。

| Metric | Value |
|---|---:|
| selected OOS trades | `324` |
| selected OOS net PnL | `+257,750.440` |
| selected OOS PF | `1.5589` |
| selected OOS max DD | `67,697.220` |
| selected OOS positive month ratio | `0.6170` |
| selected OOS worst month | `-31,447.945` |
| selected train pass | `10/16` |
| selected OOS pass | `0/16` |
| random baselines | `300` |
| selected rank by net vs random | `13/301` |
| selected net percentile vs random | `0.957` |
| research gate | `FAIL` |
| low-frequency block stability gate | `PASS` |

Random baseline kind breakdown:

| Baseline kind | Runs | Selected rank | Percentile | Best random net | Gate-like random passes |
|---|---:|---:|---:|---:|---:|
| `signal_set_random` | `100` | `8/101` | `0.921` | `+312,989.756` | `67` |
| `universe_date_matched_random` | `100` | `3/101` | `0.970` | `+374,166.996` | `19` |
| `symbol_matched_random_date` | `100` | `4/101` | `0.960` | `+303,738.959` | `44` |

Capital sensitivity:

| Capital | Trades | Net PnL | PF | Max DD | Random percentile | Block stability gate |
|---:|---:|---:|---:|---:|---:|---|
| `1,000,000` | `324` | `+257,750.440` | `1.5589` | `67,697.220` | `0.957` | `PASS` |
| `2,000,000` | `377` | `+518,048.033` | `1.4876` | `210,660.526` | `0.963` | `FAIL` |
| `5,000,000` | `394` | `+754,284.021` | `1.2770` | `427,441.676` | `0.831` | `FAIL` |

Execution model decomposition:

| Execution model | Trades | Net PnL | PF | Max DD | Positive month | Worst month | Random rank | Random percentile | Research gate | Block stability gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `conservative_no_reuse` | `293` | `+109,938.707` | `1.2264` | `98,959.395` | `0.5319` | `-39,709.841` | `110/301` | `0.635` | `FAIL` | `FAIL` |
| `limit_down_unfillable` | `293` | `+109,938.707` | `1.2264` | `98,959.395` | `0.5319` | `-39,709.841` | `110/301` | `0.635` | `FAIL` | `FAIL` |
| `gap_stop_additional_slippage` | `293` | `+109,938.707` | `1.2264` | `98,959.395` | `0.5319` | `-39,709.841` | `110/301` | `0.635` | `FAIL` | `FAIL` |
| `open_exit_then_entry` | `324` | `+257,750.440` | `1.5589` | `67,697.220` | `0.6170` | `-31,447.945` | `13/301` | `0.957` | `FAIL` | `PASS` |

Result files:

- `out/swing-daily/walk-forward-research-20210625-20260624-fixed10-hash-v1-operational-conservative-random100.json`
- `out/swing-daily/walk-forward-research-20210625-20260624-fixed10-hash-v1-operational-limit-down-random100.json`
- `out/swing-daily/walk-forward-research-20210625-20260624-fixed10-hash-v1-operational-gap-stop-slippage-random100.json`
- `out/swing-daily/walk-forward-research-20210625-20260624-fixed10-hash-v1-operational-open-exit-random100-capital-sensitivity.json`

Interpretation:

- 1M では formal low-frequency block stability gate を通ったが、既存 `research_gate` は
  train pass / per-block OOS pass / best random comparison で FAIL のまま。
- 2M / 5M は 100株単元と max_notional cap の影響で trade count と PnL は増えるが、
  drawdown と worst block が悪化し、block stability gate を通らない。
- `universe_date_matched_random` の best random が selected を上回るため、
  selector / basket alpha はまだ paper/live 根拠として不十分。
- `limit_down_unfillable` と `gap_stop_additional_slippage` は fixed-hold exit では
  結果を悪化させていないが、これは stop/target に依存しない exit 設計のためであり、
  厳しい約定 stress への一般的な耐性とは解釈しない。
- 数字の大きな改善は `open_exit_then_entry` の資金再利用順序に集中している。
  したがって paper-only sequence log で SELL fill / position delete-update /
  capital recalculation / BUY publish の順序を観測できることが前提になる。
- `open_exit_then_entry` でも research gate は FAIL のままなので、
  `daily_trend_pullback_fixed10_hash_v1_operational` は paper/live candidate ではない。

Additional random300 / block length sensitivity:

`open_exit_then_entry`, random seeds `1..300`, random baseline 3種類
(`900` random runs per block length) で再評価した。

| OOS block | Trades | Net PnL | PF | Max DD | Random rank | Random percentile | Best random net | Research gate | Block stability gate |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `40` | `296` | `+174,718.368` | `1.4046` | `53,095.617` | `50/901` | `0.945` | `+471,816.447` | `FAIL` | `FAIL` |
| `60` | `324` | `+257,750.440` | `1.5589` | `67,697.220` | `24/901` | `0.973` | `+401,983.858` | `FAIL` | `PASS` |
| `80` | `342` | `+219,511.597` | `1.4437` | `58,666.535` | `84/901` | `0.907` | `+507,768.493` | `FAIL` | `PASS` |
| `120` | `362` | `+262,104.272` | `1.5087` | `64,077.569` | `31/901` | `0.966` | `+500,415.676` | `FAIL` | `PASS` |

Result files:

- `out/swing-daily/walk-forward-research-20210625-20260624-fixed10-hash-v1-operational-open-exit-random300.json`
- `out/swing-daily/walk-forward-research-20210625-20260624-fixed10-hash-v1-operational-open-exit-40d-random300.json`
- `out/swing-daily/walk-forward-research-20210625-20260624-fixed10-hash-v1-operational-open-exit-80d-random300.json`
- `out/swing-daily/walk-forward-research-20210625-20260624-fixed10-hash-v1-operational-open-exit-120d-random300.json`

Interpretation:

- 300 seeds でも selected は random 分布の上位に残るが、全 block length で
  best random net を下回った。
- `40` trading day block では low-frequency block stability gate も FAIL した。
- `80` trading day block では selected percentile が `0.907` まで低下し、
  block 設計に対する感度が大きい。
- best random は主に `universe_date_matched_random` から出ており、date / universe
  exposure だけでも selected を上回る basket が多数存在する。
- したがって `daily_trend_pullback_fixed10_hash_v1_operational` は
  research-continuation candidate のままだが、paper/live candidate へは昇格しない。
- `daily_trend_pullback_fixed10_hash_v1_operational` の追加パラメータ探索は一旦停止する。
  理由は `300 seeds x 3 baseline` と OOS block `40/60/80/120` のすべてで
  best random net を下回り、selector / basket alpha が不十分だったため。
- 次に進むなら、この family の微修正ではなく、entry / selector alpha を
  新しい独立仮説として事前登録してから検証する。

Required gates before any paper route:

1. 数値 gate:
   - `exit_before_entry_at_open=true` だけでなく、no-stress / block length 40/60/80/120 /
     random baseline 3種類で結果を再確認する。
   - random baseline は `signal_set_random`, `universe_date_matched_random`,
     `symbol_matched_random_date` の各 100〜300 seeds へ増やし、percentile を出す。
   - `low_frequency_research_gate` は formal block stability gate として扱うが、
     aggregate OOS gate と既存 `research_gate` の FAIL を隠さない。
   - 1M / 2M / 5M capital sensitivity を出し、100株単元と max_notional cap の影響を確認する。
2. 運用 gate:
   - paper-only で寄り exit バッチを作り、SELL 約定確認と `positions` 更新完了後にのみ
     BUY signal を通す。
   - 同一営業日の sequence をログで観測し、BUY 時点の `capital_in_use` から exit 済み
     position が除外されていることを確認する。
   - 手順は [`swing-paper-opening-exit-gate`](../runbook/swing-paper-opening-exit-gate.md)
     に固定する。
   - この gate が通るまで、`open_exit_then_entry` の `low_frequency_research_gate=PASS` は
     paper/live 採用理由に使わない。
3. Project kill switch gate:
   - 最終判定は OOS aggregate の `profit_factor > 1.2` と
     `max_drawdown < capital * 0.10` を維持する。
   - 低頻度用の block stability 指標は、この条件を弱める例外ではなく、
     追加の研究診断として扱う。

## Pre-registered Entry Family: `daily_breakout_continuation_v0`

`v0`〜`v5` と `fixed10_v0` は、いずれも trend pullback family から採用可能な
paper/live candidate を出せなかった。次は filter 追加ではなく、別の entry family を
事前登録して同じ harness で反証する。

仮説:

```text
高流動性で中期上昇トレンドにある銘柄が、過去60日高値を終値で更新し、
かつ出来高代金の増加を伴う場合、その後 3-10 営業日の continuation を持つ
可能性がある。
```

これは押し目回復ではない。新高値 breakout を翌営業日寄りで買う momentum
continuation 仮説として扱う。

事前登録内容:

| Parameter | Value |
|---|---:|
| Entry family | `breakout_continuation` |
| SMA short / long | `20 / 60` |
| SMA long slope lookback | `20` |
| Min avg turnover | `200,000,000 JPY` |
| Price range | `300-5,000 JPY` |
| Min 20d return | `+8%` |
| Max 20d return | `+35%` |
| Min 60d return | `+10%` |
| Breakout | close >= prior 60d high |
| Min turnover multiple | current turnover >= `1.20 * 20d avg turnover` |
| Max prior 20d range | `28%` of signal close |
| Max distance above SMA20 | `18%` |
| ATR pct range | `1.5%-8.0%` |
| Entry | next day open |
| Entry gap | `0% <= gap < 3%` |
| Stop / target / max hold | `1.5 ATR / 2.0R / 10 trading days` |
| Max new positions per day | `1` |

合格条件:

- v0〜v5 と同じ true walk-forward harness / gate / cost / slippage / OOS block。
- random baseline は `signal_set_random`, `universe_date_matched_random`,
  `symbol_matched_random_date` の3種類。
- stress 条件も同じ。
- train / OOS の両方で安定し、best random OOS を上回るまで paper/live candidate にしない。

この candidate も research-only とする。結果を見てからパラメータを都合よく
修正した場合は、別 candidate として事前登録し直す。

### `daily_breakout_continuation_v0` Result

`2021-06-25`〜`2026-06-24` の extended walk-forward に追加した。
全体の selected strategy は前回と同じく `fixed10_v0` / `v1` / `v3` だけを選び、
`daily_breakout_continuation_v0` は train selection で一度も選ばれなかった。

全体 selected OOS は変わらず FAIL:

| Metric | Value |
|---|---:|
| selected OOS trades | `388` |
| selected OOS net PnL | `+176,057.509` |
| selected OOS PF | `1.1151` |
| selected OOS max DD | `195,021.993` |
| selected train pass | `0/16` |
| selected OOS pass | `1/16` |
| best random OOS net PnL | `+674,082.935` |
| research gate | `FAIL` |

Breakout の alpha diagnostics:

| Metric | Value |
|---|---:|
| candidate count | `2,576` |
| entry avg 5d return | `-0.2033%` |
| entry avg 10d return | `-0.2130%` |
| entry excess 5d vs tradable universe | `-0.1310%` |
| entry excess 10d vs tradable universe | `-0.0361%` |
| score fold avg rank IC 5d | `-0.0383` |

Selector / configured exit も全てマイナス:

| Selection | Selected excess 5d | Configured net PnL | PF | Max DD |
|---|---:|---:|---:|---:|
| `ranked` | `-0.2517%` | `-281,843.163` | `0.8201` | `390,197.913` |
| `score_ascending` | `-0.3940%` | `-266,998.372` | `0.8098` | `378,580.245` |
| `score_middle` | `-0.4665%` | `-289,349.113` | `0.7874` | `414,348.216` |
| `rank_2_3_first` | `-0.2792%` | `-311,667.548` | `0.7968` | `403,344.349` |

Breakout random baseline では `symbol_matched_random_date` が一部プラスだったが、
これは breakout entry set 自体より同一銘柄の別日 random のほうが良いという結果であり、
entry alpha の支持材料ではない。

結論: `daily_breakout_continuation_v0` は research-only rejected candidate とする。
この候補から v6 / paper / live に進めない。`breakout_continuation_v0` の
パラメータ探索はここで停止し、継続する場合は別 entry family として事前登録する。

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
