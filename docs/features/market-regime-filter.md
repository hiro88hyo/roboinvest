# Market Regime Filter

作成日: 2026-06-08

急落・全面安寄りの相場で、個別銘柄シグナルだけを根拠に新規 BUY へ進むことを抑制するための実装方針。
2026-06-08 前場の risk-off paper 運用では、paper の mark-to-market が前場引け時点で約 `-84,500円` となり、live で動かさなかった判断は妥当だった。

## 背景

現行の rule 戦略は RSI / SMA / Bollinger などの個別銘柄指標を主に見る。
急落相場では「安いから買う」形の逆張りシグナルが捕まりやすく、同一銘柄の再エントリーで損失を重ねる余地がある。

必要なのは、銘柄単位の entry 判定より上位にある「今日は攻める日か、守る日か」の判定である。

## 目的

- 寄り前に market regime を判定し、通常運転 / 警戒 / risk-off / 停止を切り替える。
- 寄り後も実際の market data と paper/live PnL を見て、新規 BUY を抑制できるようにする。
- AI は総合判定の補助役として使い、単独でアクセルを踏ませない。
- 最終防衛線は gateway に置き、危険な BUY は fail-close で reject する。

## Regime 案

| Regime | 目的 | 代表動作 |
|---|---|---|
| `NORMAL` | 通常運転 | 既存 strategy / gateway rules |
| `CAUTION` | 弱い地合いで縮小運転 | size 縮小、entry 条件強化、再エントリー制限 |
| `RISK_OFF` | 急落・全面安で防御優先 | 新規 BUY 停止、paper only、watchlist 生成のみ |
| `CRASH` | 異常相場・運用停止 | trading disabled、手動確認待ち |

## 判定レイヤ

### 1. 寄り前: universe-scanner

`universe-scanner` が当日 watchlist 生成時に、定量特徴量と AI 総合判定を使って初期 `market_regime` を保存する。

入力候補:

- 日経平均 / TOPIX / グロース250 などの指数前日比と短期トレンド
- universe / watchlist 候補の上昇銘柄比率、下落銘柄比率
- gap down 候補の比率
- 25日線上 / 下の比率
- ATR / 出来高急増などのボラティリティ指標
- 前日までの paper/live PnL、連敗、ドローダウン
- 必要に応じたニュース要約

保存する構造の例:

```json
{
  "market_regime": "RISK_OFF",
  "confidence": 0.86,
  "buy_enabled": false,
  "position_size_multiplier": 0.25,
  "max_round_trips_per_symbol": 1,
  "daily_loss_limit_override": 30000,
  "rationale": [
    "broad index weakness",
    "high gap-down ratio",
    "watchlist breadth negative"
  ]
}
```

保存先は `system_status` 拡張か、新規 `market_regime` テーブルを候補とする。
履歴・説明可能性を重視するなら新規テーブルが望ましい。

### 2. 寄り後: strategy / gateway

寄り前判定だけでは、9:00 以降の急変に追随できない。
寄り後は `feature-engine` / `strategy-rule` / `gateway` 側で以下を使った日中ガードを追加する。

- watchlist の VWAP 下銘柄比率
- 始値から下落している銘柄比率
- 直近 5分 / 15分の breadth 悪化
- paper/live PnL の日中悪化
- 同一銘柄の損切り回数、連敗、再エントリー回数

責務分担:

- `strategy-rule`: `RISK_OFF` では逆張り RSI BUY を出さない。
- `strategy-ai`: regime と定量特徴量を input に含め、risk-off 理由を説明できる形でログに残す。
- `gateway`: regime を最終確認し、`RISK_OFF` / `CRASH` の BUY を reject する。
- `oms-paper`: paper 検証中も損失・再エントリー挙動を観測できるように約定結果を残す。

## AI の使い方

AI は market regime の総合判定に使う。
ただし、AI 判定は「強いブレーキ」には使ってよいが、単独で「アクセル」には使わない。

優先順位:

1. kill switch / trading disabled
2. hard quantitative rules
3. AI risk-off 判定
4. strategy signal

ルール:

- AI が `RISK_OFF` と判断したら、新規 BUY 停止または size 縮小の候補にする。
- AI が `NORMAL` と判断しても、定量ルールが危険なら止める。
- gateway の kill switch / daily loss limit は AI より優先する。
- AI の入力特徴量、出力 JSON、rationale、model、timestamp を保存する。

## 優先実装

1. `market_regime` の保存先を決める。
2. `universe-scanner` に定量 regime scorer を追加する。`2026-06-08` に PR #82 で純関数 scorer までは実装済み。
3. AI 判定を scorer のレビュー / 補正役として追加する。
4. `gateway` が `RISK_OFF` / `CRASH` の BUY を reject する。
5. `strategy-rule` が `RISK_OFF` で逆張り BUY を抑制する。
6. paper 日中損失リミット、同一銘柄 cooldown、`max_round_trips_per_symbol` を追加する。
7. Dashboard / logs で当日の regime、理由、適用されたガードを確認できるようにする。

## 残タスク構造

実装タスクは増やしすぎず、以下の 5 つをメインタスクとして扱う。

### 1. Regime を保存・観測できるようにする

目的: scorer の結果を後から検証できる状態にする。まだ Gateway / live には効かせない。

サブタスク:

- `contracts/sql/011_market_regime.sql` を追加する。
- `market_regime` テーブルには `valid_date`, `regime`, `confidence`, `buy_enabled`,
  `position_size_multiplier`, `metrics`, `rationale`, `source`, `created_at` を持たせる。
- `scripts/health-check.py` と `scripts/production-preopen-check.py` の Supabase table check に
  `market_regime` を追加する。
- `services/universe-scanner/src/universe_scanner/clients/supabase.py` 経由で upsert できるようにする。
- まず `MARKET_REGIME_WRITE_ENABLED=false` で log-only 運用する。

完了条件:

- migration と health check が通る。
- `universe-scanner` が `market_regime_candidate` をログに出せる。
- 書き込み flag を on にしたときだけ `market_regime` に upsert される。

### 2. Universe Scanner から scorer を呼ぶ

目的: PR #82 で追加した純関数 scorer を実際の日次 pipeline に接続する。

サブタスク:

- `ScannerSettings` に `market_regime_enabled` と `market_regime_write_enabled` を追加する。
- `pipeline.py` で watchlist 候補または scored watchlist の symbol を scorer に渡す。
- `daily_ohlcv` だけの判定であることをログに明示する。
- `NORMAL` / `CAUTION` / `RISK_OFF` / `CRASH`、metrics、rationale を構造化ログに出す。
- DB 書き込みは flag で分ける。

完了条件:

- production paper day で DB 書き込みなしに regime ログを観測できる。
- 同日再実行しても watchlist 挙動は変わらない。

### 3. `daily_ohlcv` 以外の入力を追加する

目的: 2026-06-08 のような寄り前 risk-off を捕まえる。
`daily_ohlcv` 最新日が前営業日までだと、米国市場下落・日経先物・寄り前気配を検出できない。

サブタスク:

- 入力元を決める。優先候補は日経平均 / TOPIX / グロース250、日経先物、米国指数。
- 寄り前に取得できる値と、9:00 後にしか取得できない値を分ける。
- 9:00 直後の watchlist breadth を feature-engine または別集計で計算する案を検討する。
- AI ニュース要約は補助入力に留める。AI が `NORMAL` と言っても定量ルールが危険なら止める。

完了条件:

- 6/8 型の急落を `CAUTION` 以上にできる入力がある。
- 入力欠損時は `NORMAL` へ安易に倒さず、欠損理由をログに残す。

### 4. Gateway / Strategy に log-only で反映する

目的: 実際に止める前に「止めていたらどうだったか」を観測する。

サブタスク:

- Gateway が当日の `market_regime` を読む。
- `MARKET_REGIME_GATEWAY_GUARD_ENABLED=false` のときは reject せず log-only にする。
- log-only では BUY signal ごとに `would_reject_reason=market_regime_risk_off` を出す。
- Strategy Rule はまず read-only または未接続でよい。Gateway の観測を先に進める。

完了条件:

- `RISK_OFF` 判定日に BUY が何件止まるはずだったか追える。
- `NORMAL` 日に過剰停止しそうな signal がないか確認できる。

### 5. Paper guard から Live guard へ段階適用する

目的: 実資金へ反映する前に paper で誤検知・機会損失を確認する。

サブタスク:

- paper mode だけ `RISK_OFF` / `CRASH` の day BUY を reject する。
- SELL / closeout は常に許可する。
- reject reason は `market_regime_risk_off` にする。
- `strategy-rule` では `RISK_OFF` 時に RSI / Bollinger 系の逆張り BUY を生成しない。
- 数営業日、損失回避・機会損失・誤検知を記録する。
- live 適用は最後に行う。

完了条件:

- paper で `RISK_OFF` guard が期待どおり BUY のみ止める。
- closeout / SELL が妨げられない。
- live 適用前に Cloud Logging / Dashboard で regime と reject reason が追える。

## 実装ロードマップ

あとで実装するときは、live へ直接効かせず、純関数 scorer → 保存 → log-only → paper guard → live guard の順で小さく分ける。

### PR 1: universe-scanner の純関数 scorer

Status: Done in PR #82 (`services/universe-scanner/src/universe_scanner/regime.py`,
`services/universe-scanner/tests/unit/test_regime.py`).

目的: DB 書き込みや Gateway 連携なしで、判定ロジックだけをテスト可能にする。

追加候補:

- `services/universe-scanner/src/universe_scanner/regime.py`
- `services/universe-scanner/tests/unit/test_regime.py`

入力:

- watchlist 候補または universe 候補の直近日次 OHLCV
- 1日リターン
- 下落銘柄比率
- 3%以上下落銘柄比率
- 25日線下銘柄比率
- 出来高急増比率
- 欠損データ比率

出力例:

```json
{
  "market_regime": "NORMAL",
  "confidence": 0.62,
  "buy_enabled": true,
  "position_size_multiplier": 1.0,
  "rationale": ["breadth not weak enough for caution"],
  "metrics": {
    "avg_return_1d": 0.0148,
    "down_ratio": 0.467,
    "big_down_ratio": 0.333,
    "below_ma25_ratio": 0.0
  }
}
```

注意: 2026-06-08 の dry-run では、`daily_ohlcv` 最新日が `2026-06-05` だったため、
この入力だけでは寄り前の risk-off を検出できなかった。`daily_ohlcv` ベースの scorer は
「前日までの地合い」を見るものとし、寄り前先物・外部指数・寄り後 breadth なしで過信しない。

### PR 2: 保存先と writer

履歴・説明可能性を優先し、新規 `market_regime` テーブルを第一候補にする。
`system_status` に直書きすると、後から「なぜその regime になったか」を追いにくい。

migration 候補:

```sql
create table if not exists market_regime (
    valid_date date primary key,
    regime text not null check (regime in ('NORMAL', 'CAUTION', 'RISK_OFF', 'CRASH')),
    confidence numeric not null,
    buy_enabled boolean not null,
    position_size_multiplier numeric not null,
    metrics jsonb not null default '{}'::jsonb,
    rationale jsonb not null default '[]'::jsonb,
    source text not null default 'universe_scanner',
    created_at timestamptz not null default now()
);
```

あわせて `scripts/health-check.py` / `scripts/production-preopen-check.py` の Supabase table list に
`market_regime` を追加する。

### PR 3: universe-scanner dry-run / log-only

`universe-scanner` 実行時に regime を計算し、まず DB 書き込みなしでログ出力する。

env 候補:

```text
MARKET_REGIME_ENABLED=false
MARKET_REGIME_WRITE_ENABLED=false
```

初期運用:

- `MARKET_REGIME_ENABLED=true`
- `MARKET_REGIME_WRITE_ENABLED=false`
- production paper day で `market_regime_candidate` ログだけ観測する

### PR 4: 寄り前外部入力

6/8 型の急落を捕まえるには、`daily_ohlcv` 以外の入力が必要。
優先順は以下。

1. 日経平均 / TOPIX / グロース250 の前日終値と当日寄り前先物
2. 9:00 直後の watchlist breadth
3. ニュース / 市況 summary の AI 判定

AI は理由付け・補正役に留める。AI が `NORMAL` と言っても、定量ルールが危険なら止める。

### PR 5: Gateway log-only guard

Gateway が `market_regime` を読む処理を追加するが、最初は reject せず log のみ。

env 候補:

```text
MARKET_REGIME_GATEWAY_GUARD_ENABLED=false
```

確認すること:

- `RISK_OFF` 判定日に、実際の BUY がどれだけ出ていたか
- 止めていれば損失回避になったか
- `NORMAL` 日に過剰停止していないか

### PR 6: paper guard

paper mode だけ Gateway guard を有効化する。

期待動作:

- `RISK_OFF` / `CRASH` の day BUY は reject
- SELL / closeout は常に許可
- reject reason は `market_regime_risk_off`
- `CAUTION` の size multiplier は最初は実装せず log-only にする

### PR 7: strategy-rule の逆張り抑制

Gateway だけで止めると reject が増えるだけなので、上流でも BUY 生成を抑制する。

最初に止める対象:

- `rsi_threshold` の BUY
- Bollinger 系の逆張り BUY

`RISK_OFF` では BUY を生成しない。SELL は維持する。

### PR 8: live guard

paper で数営業日観測してから live に適用する。

live 有効化条件:

- `RISK_OFF` 判定が少なくとも 2-3 回、実損回避に寄与している
- `NORMAL` 日に過剰停止していない
- closeout / SELL が regime に妨げられない
- Dashboard / Cloud Logging で regime と reject reason が追える

## 初期ガード候補

- `RISK_OFF`: 新規 BUY 停止、SELL / closeout は許可。
- `CAUTION`: `position_size_multiplier=0.25-0.5`、entry confidence 閾値引き上げ。
- 損切り後の同一銘柄 cooldown。
- 同一銘柄の round trip 上限。
- paper/live 日中損失が閾値を超えたら新規停止。

## 非スコープ

- AI 単独の銘柄選定。
- AI が直接 order を作る構成。
- live 資金での即時適用。
- ニュース解析を必須依存にすること。

## 未決事項

- `market_regime` を `system_status` に持たせるか、履歴テーブルとして分離するか。
- 指数データの取得元と更新時刻。
- 定量 scorer の初期閾値。
- AI provider / model / prompt の運用コスト。
- paper 検証期間と live 適用条件。
