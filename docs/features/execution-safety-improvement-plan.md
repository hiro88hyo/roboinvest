# Execution Safety Improvement Plan

作成日: 2026-06-16

ChatGPT 5.5 Pro feedback (`docs/handoff/2026-06-16-gpt55pro-feedback.md`) と
現行の運用ログ / backtest レポートを突き合わせた改善計画。

結論は、戦略パラメータや LLM prompt を先に触るより、まず live で壊れやすい
入口と執行を paper / backtest でも再現できるようにすることを優先する。

## 方針

- 新規 BUY を安易な MARKET 注文にしない。
- Paper / backtest が live より甘く約定しすぎる状態を減らす。
- Gateway に spread / depth / liquidity / regime / time / signal source を見る
  execution gate を置く。
- RULE-only BUY は地合いと損失状態に応じて縮小または停止する。
- stop-loss は StrategySignal 待ちではなく、安全装置として扱う。
- live 反映は log-only -> paper guard -> live guard の順で行う。

## Current State

すでに入っている下地:

- `market_regime` table / service-role grant / health check.
- Universe Scanner の daily OHLCV ベース market regime scorer.
- Gateway の market regime log-only / guard flag.
- Gateway の soft loss throttle log-only / guard flag.
- Gateway の daily liquidity sizing.
- Feature Engine stop-loss exit retry.
- Strategy Rule RSI BUY の VWAP / SMA uptrend confirmation.
- Feature / order / book archive と paper postmortem replay tooling.

計画開始時点で残っていた大きな穴:

- Gateway の通常 BUY は `OrderType.MARKET` 固定。
- OMS Paper は LIMIT を約定できず `limit_not_supported` にする。
- `ProcessedFeatures` に spread / depth / tick size / session phase がない。
- Execution quality gate は postmortem 指標としてあるが、Gateway の最終判定にはまだない。
- Universe Scanner score は opportunity 寄りで、risk penalty が弱い。

## PR Roadmap

### PR 1: Gateway BUY LIMIT + OMS Paper LIMIT fill

Status: Implemented on branch `codex/execution-safety-gates`.

目的: 新規 BUY を MARKET 固定から外し、paper/backtest に未約定を発生させる。

Scope:

- Gateway `order_builder`:
  - BUY は `OrderType.LIMIT` にする。
  - 初期実装では解決済み `entry_price` を `limit_price` に使う。
  - SELL は通常どおり `MARKET` を維持する。
  - closeout / emergency exit の MARKET は別経路として残す。
- OMS Paper `fill_simulator`:
  - BUY LIMIT は `ask.price <= limit_price` の板だけを食う。
  - SELL LIMIT は `bid.price >= limit_price` の板だけを食う。
  - 条件に合う板がなければ no-fill にする。
  - 条件に合う板が足りなければ partial にする。

完了条件:

- Gateway unit tests が BUY LIMIT を期待する。
- OMS Paper unit tests が LIMIT filled / partial / no-fill / missing limit price を確認する。
- 対象サービスの unit tests が通る。

### PR 2: Execution Features in Contracts / Feature Engine

Status: Core fields implemented on branch `codex/execution-safety-gates`.
`tick_size` / `spread_ticks` は通常呼値テーブルで算出済み。TOPIX500 の細かい
呼値は、信頼できる銘柄属性を Feature Engine へ渡せるようになってから有効化する。

目的: Gateway が執行可否を判断できる特徴量を `ProcessedFeatures` に載せる。

追加候補:

- `best_bid`, `best_ask`
- `spread_bps`
- `tick_size`
- `spread_ticks`
- `bid_depth_1`, `ask_depth_1`
- `bid_depth_5`, `ask_depth_5`
- `book_imbalance_5`
- `minutes_from_open`, `minutes_to_close`
- `session_phase`

注意:

- `tick_size` / 呼値は銘柄・価格帯で変わる。初期実装は通常呼値を使い、
  TOPIX500 判定は未適用にして保守的に扱う。
- フィールドは optional で追加し、下流互換性を保つ。

### PR 3: Gateway Execution Gate Log-Only

Status: Initial spread/depth gate implemented on branch `codex/execution-safety-gates`.
Default は log-only。`EXECUTION_GATE_GUARD_ENABLED=true` で実 reject できる。
Spread は bps と ticks の両方で観測する。

目的: 実際に止める前に、止めていたらどうだったかを観測する。

Gate 候補:

- spread が広すぎる BUY を would-reject。
- ask depth が注文数量に対して薄すぎる BUY を would-reject。
- daily volume / turnover が薄い銘柄を shrink / reject。
- `RISK_OFF` / `CRASH` の BUY を would-reject。
- `CAUTION` の RULE-only BUY を would-reject。
- `soft_loss_limit` 到達後の RULE-only BUY を would-reject。

完了条件:

- `execution_gate_would_reject` の構造化ログが出る。
- paper/live どちらの mode でも log-only 観測できる。
- SELL / closeout は gate で止めない。

### PR 4: Paper Guard + Replay Gate

Status: Paper-only market regime guard switch and replay gate metrics implemented
on branch `codex/execution-safety-gates`.
1営業日以上の archive 評価は未実施。

目的: paper で実際に BUY を止め、postmortem replay で機会損失と損失回避を比較する。

Scope:

- paper mode で `RISK_OFF` / `CRASH` BUY guard を有効化。
  - `MARKET_REGIME_PAPER_GUARD_ENABLED=true` で paper BUY のみ reject。
  - live は `MARKET_REGIME_GATEWAY_GUARD_ENABLED=true` を明示するまで reject しない。
- LIMIT no-fill / partial-fill を `backtest_report.json` の採用判定に含める。
  - `no_fill_count`, `no_fill_rate`, `limit_no_fill_count`
  - `average_spread_ticks`, `max_spread_ticks`
  - `average_fill_ratio`, `partial_fill_count` は既存指標として継続
- `ENTRY_VOLUME_RATIO_MIN=2.0` などの候補は paper-only のまま replay で比較する。

完了条件:

- 1営業日以上の paper archive で replay summary を残す。
- 止めた BUY の損益影響と逃した利益を比較できる。

### PR 5: Strategy Rule Reclaim Filters

目的: RSI / Bollinger の「落ちるナイフ」BUY を減らす。

Scope:

- Bollinger BUY の lower-band reclaim を paper-only で有効化する。
- 共通 entry filter に VWAP / SMA / volume / spread / depth を追加する。
- RISK_OFF 時の逆張り BUY 抑制は Gateway の観測後に Strategy 側へ広げる。

### PR 6: Universe Scanner Risk Penalty

目的: watchlist が高ボラ / 薄商い / 過熱銘柄を拾いすぎる問題を減らす。

初期 risk penalty:

- 高価格帯。
- ATR / range 過大。
- gap 過大。
- 低 volume / turnover。
- momentum 過熱後失速。
- 下落トレンド。
- セクター / テーマ集中。

最初は `selected_reasons` に risk metrics を入れる log-only から始める。

## Live Guard Promotion Criteria

live に guard を効かせる条件:

- Paper で少なくとも数営業日観測している。
- LIMIT / no-fill / partial-fill 込みの replay gate が通っている。
- RULE-only BUY の損益を単独で確認している。
- `RISK_OFF` / `CRASH` guard が BUY だけを止め、SELL / closeout を妨げていない。
- Cloud Logging または Dashboard で reject reason と regime が追える。
- live 資金増額や kill-switch 条件変更は `AGENTS.md` の Project Kill Switch を弱めない。

## Non-Goals

- RSI / Bollinger 閾値だけを微調整して終わらせる。
- LLM prompt を複雑にして BUY 権限を強める。
- 日足 OHLCV backtest の PF だけで live 採用する。
- AI 単独 BUY をアクセルとして使う。
- live 資金でいきなり guard を有効化する。
