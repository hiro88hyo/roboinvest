# 2026-05 Performance Review

作成日: 2026-05-29

2026年5月の live / paper trading 成績レビューと、次に修正すべき設定・ルールの要約。

## Summary

- Live Trading (`2026-05-21` から `2026-05-29`): `+46,766円`
- Live trades: `123`
- Live win rate: `50.41%`
- Live profit factor: `1.34`
- Live max drawdown: `69,230円`
- Paper Trading (`2026-05-19` から `2026-05-21`): `+68,100円`
- Paper trades: `192`
- Paper win rate: `36.98%`
- Paper profit factor: `1.12`

全体は利益着地したが、`2026-05-29` は朝一の連敗と持ち越し決済により `-45,540円` の大幅マイナス。

## Silent AI Bug

`2026-05-21` 以降の live trading では、AI/LLM 由来の取引が実質的に 0 件だった。

原因候補:

- 稼働中の `gemini-2.5-flash` などの思考型モデルが、JSON 出力前に thinking tokens を約 240-255 tokens 生成する。
- production の `AI_MAX_OUTPUT_TOKENS=256` では thinking tokens だけで出力枠を使い切る。
- その結果、JSON が出る前に `MAX_TOKENS` で打ち切られ、parser error になっていた。

検証済み対策:

- `AI_MAX_OUTPUT_TOKENS=2048` では JSON が正常に生成されることを確認済み。

次に確認すること:

- production env へ `AI_MAX_OUTPUT_TOKENS=2048` を反映する。
- `strategy-ai` が `StrategySignal` を publish することを production logs / Supabase `strategy_logs` で確認する。
- AI signal が aggregator / gateway まで到達することを確認する。

## Trading Rule Improvements

候補 1: 寄り付き制限

- `2026-05-29` の損失の大部分は 09:00-09:05 の急変動エントリーに集中。
- `gateway` で 09:00-09:15 の live/day 新規 BUY を reject する案が有力。

候補 2: 保有時間制限

- 15分以内の決済が利益の大半を稼いでいる。
- 60分を超えるポジションは勝率が `41.7%` まで低下。
- 45分前後での time-based market closeout を検討する。

候補 3: Carryover risk の追加対策

- `5031` の持ち越し決済では 2日保有後に `-17,600円` の損失。
- 大引け closeout の堅牢化、強いアラート、翌営業日 pre-open 手順が必要。
- 同一銘柄の当日再購入ロックと late live BUY guard は維持する。

## Universe Scanner Algorithm Analysis

### 現行アルゴリズム概要
- **第1段階（静的フィルタ）**: 東証プライム・スタンダード・グロース、株価300円〜20,000円、直近20日平均日次売買代金が1億円以上の流動性銘柄にスクリーニング。
- **第2段階（動的スコアリング）**: 過去60日の日次データから「ボラティリティ（20日リターン標準偏差）」、「モメンタム（20日騰落率）」、「出来高急増（5日平均/20日平均出来高）」の3因子を計算。各因子のZ-Score（標準化）を算出し、等価加重合計（デフォルト）でスコアを計算。上位30銘柄をWatchlistとして採用。

### 課題と改善プラン
- **モメンタム正の重みの罠（寄り天リスク）**: 直近急騰している銘柄がスコア上位に入りやすいため、寄り付き直後の急落（寄り天）の直撃を受けやすい。
  - **対策**: モメンタム評価スパンの短縮（例：3〜5日）や、RSI等のオシレーターを用いた過熱感（高値掴みリスク）の減点補正の導入。
- **相対スコア（Z-Score）の罠（地味株リスク）**: 相場全体が膠着している日でも、相対値で上位になった「実際にはほとんど動かない銘柄」がWatchlistの30枠を埋めてしまい、微小なスプレッド負けを引き起こす。
  - **対策**: ボラティリティの絶対値閾値（例: 20日ボラティリティが◯%以上）を設定し、ボラティリティの低い日はWatchlistの銘柄数を絞り込むように変更する。

## Rule-based Signal & Aggregation Analysis

### 現行ロジックの動作
- **搭載戦略**: `sma_crossover` (トレンド順張り), `rsi_threshold` (逆張り平均回帰), `bollinger_breakout` (逆張り平均回帰)。
- **集約処理**: アグリゲーターの [consensus.py](file:///home/hiroyuki/workspaces/roboinvest/services/aggregator/src/aggregator/consensus.py) において、同一ソース（RULE）から複数の異なるシグナルが出た場合、[_pick_dominant](file:///home/hiroyuki/workspaces/roboinvest/services/aggregator/src/aggregator/consensus.py#L30) 関数により **「最も自信度（confidence）が高いもの1つ」を無条件で選択し、他を破棄** する。

### 課題と改善プラン
- **ルール間の対立（コンフリクト）の黙殺**: 
  - 例えば、トレンド指標（SMA crossover）が強い下落トレンドを検知して `SELL` を出している最中であっても、オシレーター（RSI）が売られすぎを検知して高い自信度で `BUY` を出すと、アグリゲーターは「対立」として処理せず、自信度の高い `BUY` のみを通して発注してしまいます（逆行トレンドへの買い向かい事故）。
  - **対策**: 同一ソース（RULE）内であっても、`BUY` と `SELL` の相反するシグナルが同時に出ている場合は、アグリゲーターレベルでコンフリクトとして相殺（skip）するロジック、あるいは「順張りトレンドに逆らう逆張りシグナルの自信度を引き下げる（gating）」仕組みを導入する。
- **AIフィルタ不在時の高感度化**:
  - 本来であれば「AIが同意しないシグナルはskipする（デフォルト設定）」という CONSENSUS 制御が防波堤となる設計でしたが、AIが沈黙していたため、アグリゲーターは単一のルールベースシグナル（自信度 $\ge 0.3$）をすべてスルーパスしていました。
  - AI復旧後は機能する見込みですが、ルール単体でもノイズを減らすため、自信度閾値（`min_confidence`）を現在の `0.3` から `0.5`〜`0.6` に引き上げることを検討すべきです。

## Additional Review: 修正点と修正方針

上記レビューは概ね妥当。追加で、損失抑制を目的にするなら「良い銘柄を選ぶ」より先に、Gateway / Aggregator に置ける横断的な fail-close guard を優先する。

### P0: AI 戦略の復旧

修正点:

- production の `AI_MAX_OUTPUT_TOKENS=256` は、`gemini-2.5-flash` の thinking tokens だけで出力枠を使い切る可能性が高い。
- 2026-05 の live 成績は、実質的に RULE 単独運用の結果として扱うべき。

修正方針:

- `infra/env.production` / `infra/env.production.tpl` の `AI_MAX_OUTPUT_TOKENS` を `2048` に上げる。
- production 再起動後、`strategy-ai` が `StrategySignal` を publish し、`strategy_logs` / `aggregator_logs` / `gateway` まで到達することを確認する。
- AI 復旧前に threshold や universe selection を大きく変えると、評価対象が変わりすぎるため避ける。

検証観点:

- `strategy_logs.source = AI` の行数が市場時間中に増える。
- `aggregator_logs.signal_source = CONSENSUS` が発生する。
- parser error / `MAX_TOKENS` 打ち切りが warning の主因でなくなる。

### P1: 寄り付き live/day BUY guard

修正点:

- 2026-05-29 の大きな損失は 09:00-09:05 の急変動エントリーに集中している。
- 現行 Gateway には `stale_signal`、`market_closed`、`late_live_buy`、`same_day_reentry_after_sell` はあるが、寄り付き直後の live/day 新規 BUY を抑制する guard はない。

修正方針:

- `gateway` に `LIVE_DAY_NEW_BUY_START_TIME` または `LIVE_DAY_OPEN_BUY_BLOCK_UNTIL` を追加する。
- 初期値は `09:05` まで完全 reject。より保守的に始めるなら `09:15` まで reject。
- 将来的には `09:05-09:15` を完全 reject ではなく、板スプレッド、気配安定、出来高、AI 同意が揃う場合のみ許可する段階制御にする。

検証観点:

- 09:00-09:05 JST の live/day BUY が `opening_live_buy` などの明示 reason で reject される。
- SELL は既存ポジション決済なので block しない。
- paper mode には原則影響させず、live mode のみを保守側に倒す。

### P1: RULE 内コンフリクトの skip

修正点:

- `aggregator.consensus._pick_dominant()` は、同一 source 内に `BUY` と `SELL` が混在しても最高 confidence の 1 件だけを採用する。
- `sma_crossover` の順張り SELL と `rsi_threshold` / `bollinger_breakout` の逆張り BUY が同時発生した場合、トレンド逆行の BUY が通りうる。

修正方針:

- まずは同一 source 内で `BUY` と `SELL` が混在した場合、その source の候補を `None` として扱う。
- その結果、RULE だけが対立している場合は unified signal を出さない。
- AI と RULE が両方ある場合も、RULE 内対立は RULE 無効扱いにして、AI 単独の threshold を満たす場合のみ通す。
- いきなり複雑な indicator weighting は入れず、skip から始める。

検証観点:

- `RULE: BUY + RULE: SELL` の同時入力で `aggregate()` が `None` を返す。
- `RULE` 内対立 + `AI BUY` の場合、AI confidence が threshold 未満なら skip、以上なら AI 単独で通る。
- 既存の RULE 同方向複数シグナルは従来通り dominant pick される。

### P1: 実損切り exit の追加

修正点:

- Gateway の 2% ルールは、BUY 数量計算には効くが、保有後の損切りを自動執行するものではない。
- `positions.stop_loss_price` は存在するが、live position の current price を監視して自動 SELL を出す責務が明確に実装されていない。
- 5/29 のような急落では、time-based closeout より先に price-based stop が必要。

修正方針:

- Phase 1 は固定幅 stop でよい。BUY 約定時に `stop_loss_price = fill_price * (1 - DEFAULT_STOP_LOSS_SPREAD_PCT)` を保存する。
- `feature-engine` が `positions.current_price` / `unrealized_pnl` を更新するだけでなく、別サービスまたは OMS Live の scheduler が live position を監視し、`current_price <= stop_loss_price` で market SELL を publish / execute する。
- リスク判断の責務を Gateway に寄せる原則を守るなら、stop trigger も `trade-signals` 相当の内部 signal として Gateway に通す設計にする。
- ただし実装を急ぐ場合は、OMS Live 側の closeout 系処理として「live position safety close」を最小実装し、後で Gateway 経由に整理する。

検証観点:

- BUY 約定後に `positions.stop_loss_price` が保存される。
- current price が stop を下回った時、live SELL が一度だけ発行される。
- stop SELL は opening BUY guard / late BUY guard の影響を受けない。
- stop SELL 後に `positions(live)` が削除され、`trades_live` と `system_status.daily_pnl` が更新される。

### P2: 保有時間 closeout

修正点:

- 15分以内の決済が利益の大半を稼ぎ、60分超の勝率が落ちている。
- 現行 closeout は大引け中心で、ポジションごとの最大保有時間を見ていない。

修正方針:

- `positions.opened_at` を使い、live/day position が一定時間を超えたら market SELL を出す。
- 初期値は `45` 分を候補にするが、いきなり固定 closeout せず、まずは `MAX_HOLD_MINUTES` を env 化して paper / dry-run で発火件数を確認する。
- 15分以降は exit signal を優先、45分で強制 closeout、含み損 stop は時間に関係なく即 exit、という優先順位にする。

検証観点:

- opened_at から `MAX_HOLD_MINUTES` 超過した live/day position が closeout 対象になる。
- swing position には適用しない。
- 既存の 14:50 day closeout と二重発注しない。

### P2: Universe Scanner の過熱・低ボラ対策

修正点:

- 現行 scoring は `volatility`、`volume_surge`、`momentum` の Z-score 合算で、相対上位を必ず `top_n` 件採用する。
- 市場全体が低ボラの日でも、実際には動かない銘柄が Watchlist を埋める。
- 直近急騰銘柄が momentum で上位に入り、寄り天リスクを増やす。

修正方針:

- `min_volatility` の絶対閾値を追加し、閾値未満は Watchlist から除外する。
- `momentum_window` を 20 日固定から 3-5 日候補へ短縮できるよう env 化する。
- 直近日次リターンが過大な銘柄は score 減点、または `opening_buy_blocked=true` 相当の watchlist reason を付け、寄り付き直後だけ Gateway で BUY を抑制する。
- `top_n` を必ず埋めるのではなく、条件未達の日は Watchlist 件数を減らすことを許容する。

検証観点:

- 低ボラ日に Watchlist 件数が 30 未満になりうる。
- `selected_reasons` に absolute volatility / momentum penalty の値が残る。
- 変更前後の watchlist と実取引候補を日次で比較できる。

### P2: source 別 confidence threshold

修正点:

- `CONSENSUS_MIN_CONFIDENCE` を一律に `0.5`-`0.6` へ上げると、AI 復旧後の `RULE + AI` 一致シグナルまで落とす可能性がある。
- 問題は主に AI 沈黙時の RULE 単独通過なので、一律 threshold より source 別 threshold の方が制御しやすい。

修正方針:

- `CONSENSUS_MIN_CONFIDENCE` は維持または小幅調整に留める。
- 追加で `MIN_CONFIDENCE_RULE_ONLY`、`MIN_CONFIDENCE_AI_ONLY`、`MIN_CONFIDENCE_CONSENSUS` を導入する。
- 初期案は `RULE_ONLY=0.5`、`AI_ONLY=0.5`、`CONSENSUS=0.3`。

検証観点:

- RULE 単独の低 confidence signal は落ちる。
- RULE + AI 一致は blended confidence が `CONSENSUS` threshold を満たせば通る。
- `aggregator_logs` に skip reason を残すか、少なくともログで threshold reason を追える。

## 推奨実装順

1. `AI_MAX_OUTPUT_TOKENS=2048` 反映と AI signal 復旧確認。
2. `gateway` の寄り付き live/day BUY guard。
3. `aggregator` の RULE 内コンフリクト skip。
4. live position の price-based stop-loss exit。
5. `MAX_HOLD_MINUTES` による time-based closeout。
6. Universe Scanner の `min_volatility` / momentum penalty。
7. source 別 confidence threshold。

損失抑制の観点では、Universe Scanner の改善よりも Gateway / Aggregator の fail-close guard を先に入れる。銘柄選定は入口の質を上げる施策だが、5/29 型の損失には「いつ入らないか」「いつ逃げるか」の制御の方が直接効く。

## 実験方針: 同時に両方を変えない

Universe Scanner と Gateway / Aggregator は、同じタイミングで大きく変更しない。

理由:

- Universe Scanner を変更すると watchlist 自体が変わるため、損益変化の原因が「銘柄選定」なのか「売買ガード」なのか切り分けにくい。
- Gateway / Aggregator の変更は、同じ watchlist に対して「どの signal を通すか / 拒否するか」を変えるため、reject reason、trade 数、時間帯別損益で効果を比較しやすい。
- 5/29 型の損失は寄り付きエントリー、RULE 単独通過、保有後 exit の問題が強いため、まず売買ガードを固める方が直接効く。

推奨順:

1. 先に Gateway / Aggregator 側を修正する。
   - `AI_MAX_OUTPUT_TOKENS=2048`
   - 寄り付き live/day BUY guard
   - RULE 内 BUY/SELL conflict skip
   - 必要に応じて RULE 単独 threshold 調整
2. 数営業日、同じ Universe Scanner 方針のまま効果を見る。
   - trade 数
   - reject reason 分布
   - 09:00-09:15 の損益
   - missed profit が増えすぎていないか
   - `RULE` / `AI` / `CONSENSUS` の比率
3. その後に Universe Scanner 側を修正する。
   - `min_volatility`
   - momentum penalty
   - `top_n` を必ず埋めない
   - 寄り天リスク銘柄の減点

この順序なら、最初の実験では watchlist を固定したまま「売買判断の品質」を測れる。その後、Universe Scanner を変えた実験では「入口の品質改善」を別に評価できる。

## Source

詳細分析は元アーティファクト `brain/f2b491b0-d9b8-43c8-bf17-0873c80e7b52/investment_review_may_2026.md` に記録されている。
