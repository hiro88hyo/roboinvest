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
2. `universe-scanner` に定量 regime scorer を追加する。
3. AI 判定を scorer のレビュー / 補正役として追加する。
4. `gateway` が `RISK_OFF` / `CRASH` の BUY を reject する。
5. `strategy-rule` が `RISK_OFF` で逆張り BUY を抑制する。
6. paper 日中損失リミット、同一銘柄 cooldown、`max_round_trips_per_symbol` を追加する。
7. Dashboard / logs で当日の regime、理由、適用されたガードを確認できるようにする。

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
