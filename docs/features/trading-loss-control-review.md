# Trading Loss Control Review

作成日: 2026-06-10

2026-06-10 の live 運用は `realized_daily_pnl=-40,310円`、`38` 約定、残 live position なしで終了した。
14:50 closeout と kabu / Supabase position 整合は正常だったが、損失の出方から、単発の時間帯ガード追加ではなく、銘柄選定・戦略前提・ロット・日中リスク縮小をまとめて見直す必要がある。

## 目的

- 場当たり的な「今日は負けたから閾値を上げる」ではなく、負けやすい市場状態と銘柄状態を事前に避ける。
- 1 回の失敗が日次損益を壊す銘柄・サイズ構造を減らす。
- live 適用前に paper / log-only で、損失回避と機会損失を検証できる形にする。

## 2026-06-10 の観測

確定値:

- live trades: `38` (`buy=19`, `sell=19`)
- realized PnL: `-40,310円`
- open live positions: `0`
- weekly PnL: `-30,380円`
- monthly PnL: `-36,330円`
- all live trades had `signal_source=RULE`

主な負け:

| Symbol | PnL | Entry | Exit | コメント |
|---|---:|---:|---:|---|
| `4378` | `-11,900円` | 09:17 `1443.0` | 09:25 `1324.0` | 寄り後ガード通過直後に急逆行 |
| `4047` | `-10,950円` | 12:31 `4046.5` | 13:08 `3937.0` | 高単価で 100 株固定の損失が大きい |
| `9256` | `-9,000円` | 14:28 `3345.0` | 14:33 `3255.0` | late BUY cutoff 直前の高単価損失 |
| `6327` | `-8,050円` | 09:27 `3189.5` | 09:44 `3109.0` | 高単価で短時間逆行 |

勝ちもあったが、少数の大きな負けで全体が崩れた。

## 根本論点

### 1. 銘柄選定が「利益候補」寄りで「損失を作りやすい銘柄」を落としきれていない

Universe Scanner / watchlist は出来高、ボラティリティ、モメンタムなどの opportunity を拾うが、risk penalty が弱い。
高ボラ銘柄は利益機会でもあるが、現行の実行・損切り・100 株固定サイズでは、逆行時の損失が大きすぎる。

見直し候補:

- 高単価銘柄へのペナルティまたは別ロット設計。
- ATR / intraday range / gap の大きい銘柄へのペナルティ。
- 前日大陰線、連続陰線、直近急落、寄り後急落のペナルティ。
- watchlist 内で同一セクター・同一テーマに偏りすぎる場合の集中リスク制限。
- 板が薄い、スプレッドが広い、成行の滑りが大きい銘柄の除外。
- 前日までの出来高急増を「良い材料」とだけ見ず、売り圧力・需給悪化としても評価する。

方向性:

```text
watchlist_score = opportunity_score - risk_penalty
```

`risk_penalty` は少なくとも価格帯、ATR、gap、下落トレンド、板薄、セクター集中を含める。

### 2. Market Regime が戦略の前提条件になっていない

個別銘柄の RSI / SMA / Bollinger 条件だけでは、全面安や小型株逆風の日に逆張り BUY が捕まりやすい。
`docs/features/market-regime-filter.md` の通り、上位の地合い判定を first-class input にする必要がある。

見直し候補:

- `NORMAL`, `CAUTION`, `RISK_OFF`, `CRASH` を日次と日中で判定する。
- `RISK_OFF` では RULE 単独 BUY を止めるか、少なくとも小ロット / 高 confidence のみ許可する。
- `CRASH` では新規 BUY を停止し、SELL / closeout だけ許可する。
- 寄り前に指数・先物・米国市場・watchlist 候補の breadth を使う。
- 寄り後に watchlist の VWAP 下比率、始値割れ比率、5分/15分 breadth を使って regime を更新する。

### 3. RULE 単独の権限が強すぎる

2026-06-10 の live 約定はすべて `signal_source=RULE` だった。
AI が `HOLD` / no signal を返していても、RULE 単独が十分な confidence で通ると live BUY が成立する。

見直し候補:

- RULE 単独 BUY は `NORMAL` のみ許可する。
- `CAUTION` では RULE 単独 BUY の threshold を上げ、ロットを下げる。
- `RISK_OFF` では RULE 単独 BUY を禁止する。
- AI は単独アクセルではなく、否定・HOLD をブレーキとして使う。
- RULE + AI consensus は通常ロット、RULE 単独は縮小ロットのように扱う。

### 4. 100 株固定が損失を均していない

日本株は 100 株単位制約があるが、現行は高単価・高ボラ銘柄でも 100 株で入る。
そのため `4047`, `9256`, `6327` のような銘柄では 1 回の逆行が日次損益に大きく効く。

見直し候補:

```text
risk_budget_per_trade = account_risk_budget * regime_multiplier
estimated_stop_distance = max(ATR_based_stop, spread/slippage_buffer, strategy_stop)
allowed_qty = floor_to_lot(risk_budget_per_trade / estimated_stop_distance)
```

100 株未満になるなら、その銘柄は見送る。
高単価銘柄を取引したい場合は、strategy 側でより厳しい entry 条件を要求する。

### 5. 日中の戦略ヘルス監視が弱い

日次損失上限 `100,000円` は最終停止線としてはよいが、戦略がその日の市場に合っていないことを検知するには遠い。
複数銘柄で同時に短時間逆行している場合、個別 trade の損切りではなく、戦略全体を縮小する必要がある。

見直し候補:

- realized PnL が `-20,000円` などの段階に達したら新規 BUY を縮小。
- 連続負け数、直近 N trade の勝率、平均損益で throttle。
- 同一時間帯に複数銘柄が損切りされたら `CAUTION` / `RISK_OFF` へ昇格。
- watchlist 全体の breadth が悪化したら RULE BUY を停止。
- daily loss limit とは別に `soft_loss_limit` / `hard_loss_limit` を持つ。

例:

| 状態 | 動作 |
|---|---|
| `soft_loss_limit` 到達 | RULE 単独 BUY 禁止、consensus のみ |
| 直近 5 trade 中 4 敗 | 新規 BUY cooldown |
| watchlist breadth 悪化 | regime を `CAUTION` 以上へ |
| `hard_loss_limit` 到達 | 新規 BUY 停止、SELL / closeout のみ |

実装状況:

- branch `codex/market-regime-gateway-log-only` で Gateway の soft loss throttle を追加済み。
- 既定値は `SOFT_LOSS_LIMIT_JPY=20000`、`SOFT_LOSS_THROTTLE_LOG_ONLY_ENABLED=true`,
  `SOFT_LOSS_THROTTLE_GUARD_ENABLED=false`。
- `system_status.daily_pnl <= -SOFT_LOSS_LIMIT_JPY` かつ `signal_source=RULE` の BUY で、
  既定では `soft_loss_throttle_would_reject` を構造化ログ出力する。
- guard 有効化時は `soft_loss_rule_only_buy` で RULE 単独 BUY を reject する。
- CONSENSUS BUY と SELL は soft throttle では止めない。

### 6. Broker 余力エラーは損失原因ではないが、リスク制御の品質問題

2026-06-10 は `Code 21: 可能額が不足しております` が `6779`, `3905`, `6997` などで繰り返し出た。
これは直接損失を作ったというより、Gateway の資金見積もりと broker 実余力がズレている症状である。

見直し候補:

- Gateway が live BUY 前に broker cash / available buying power を考慮する。
- `Code 21` が連続した symbol は同日 cooldown する。
- `Code 21` が一定回数を超えたら、新規 BUY 全体を一時停止する。
- Gateway の budget model と kabu の実拘束資金、未約定注文、SOR 条件の差を整理する。

### 7. 重複 SELL / 決済指定エラーは防御層として潰す

`Code 8: 決済指定内容に誤りがあります` は、すでに決済済みの銘柄へ追加 SELL が出た可能性がある。
position 整合は最終的に正常だったが、不要な broker error は減らすべき。

見直し候補:

- OMS Live で SELL 直前に broker / Supabase position を再確認する。
- SELL order pending 中は同一 symbol の追加 SELL を抑制する。
- SELL fill 後の短時間 duplicate closeout / strategy SELL を idempotent に落とす。
- `Code 8` は想定済み no-op と本当の異常を分けてログ分類する。

## 実装順序

live に直接効かせず、観測から始める。

1. 2026-06-10 のような負け日を再集計できる分析 script を作る。
   - symbol PnL
   - hold time
   - entry time bucket
   - signal source
   - regime / watchlist score / risk penalty
2. Universe Scanner の watchlist score に risk penalty を log-only で追加する。
3. `market_regime` を保存し、Gateway で log-only の `would_reject` を出す。
4. dynamic risk throttle を log-only で出す。
5. paper mode で `RISK_OFF` / `soft_loss_limit` / risk penalty guard を有効化する。
6. live は数営業日の paper / log-only 検証後に、限定的に有効化する。

## 検証観点

単に損失が減るかだけでなく、以下を比較する。

- 止めていたら避けられた損失。
- 止めたことで逃した利益。
- 高単価銘柄の除外・縮小による PnL 変化。
- RULE 単独禁止による約定数、勝率、平均損益の変化。
- `RISK_OFF` 誤判定による機会損失。
- broker error (`Code 21`, `Code 8`) の減少。

## 非方針

- 2026-06-10 の負けだけを根拠に、寄り後開始時刻を機械的に 09:30 へ変更しない。
- RSI / SMA / Bollinger の閾値を単独で微調整して終わらせない。
- daily loss limit だけを下げて、戦略が市場に合っていない問題を隠さない。
- AI に単独で BUY 権限を持たせない。

## 直近の意思決定候補

1. Universe Scanner の risk penalty に含める最小特徴量を決める。
2. 高単価銘柄を除外するのか、ロット計算で自然に落とすのかを決める。
3. RULE 単独 BUY を `NORMAL` のみに制限する方針でよいか決める。
4. `soft_loss_limit` の初期値を決める。候補は `-20,000円` または日次上限の 25%。
5. 2026-06-10 を分析 fixture として保存し、今後の guard 評価に使う。
