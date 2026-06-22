見ました。結論として、このリポジトリは**インフラと運用設計はかなりちゃんとしている**一方で、デイトレの収益源としてはまだ **「売買シグナル」「執行モデル」「流動性・地合いフィルター」** が弱いです。
言い方を変えると、**LLMが賢くないから負けるというより、RULE単独シグナルが素朴すぎるのと、MARKET注文・薄商い・地合い悪化・バックテスト約定モデルの扱いが一番危ない**です。

## 全体評価

このrepoは toy ではないです。README上は「国内現物株を auカブコム証券 API で自律売買する、ルールベース + AI/LLM のハイブリッド Trade AI Agent」で、サービスも `universe-scanner`, `feeder`, `feature-engine`, `strategy-rule`, `strategy-ai`, `aggregator`, `gateway`, `oms-live`, `oms-paper`, `dashboard` に分かれています。構成思想としては、Pub/Subで疎結合、Gatewayがリスク判定、OMS Live/Paperを分離、contractsを単一ソースにするという設計になっています。([GitHub][1])

さらに、2026年5月〜6月の運用メモまで残っていて、5月のLiveは `+46,766円`, 123 trades, win rate 50.41%, PF 1.34、Paperは `+68,100円`, 192 trades, PF 1.12 と記録されています。ただし、5月29日に `-45,540円` の大きな負けがあり、AIのmax token不足でAI戦略が実質沈黙していたため、3月・5月のLiveは実質RULE-onlyだった、という自己分析も書かれています。ここはかなり重要です。([GitHub][2])

私の評価はこうです。

| 項目      |        評価 | コメント                                         |
| ------- | --------: | -------------------------------------------- |
| アーキテクチャ |     かなり良い | サービス分離、Gateway中心のリスク管理、Paper/Live分離は良い       |
| 運用ログ    |        良い | 失敗事例と改善メモが残っている                              |
| ルール戦略   |        弱い | SMA/RSI/Bollinger中心で、地合い・板・流動性の扱いが薄い         |
| AI戦略    |     まだ補助役 | 現状はアルファ源というよりフィルター/ブレーキ向き                    |
| 執行モデル   |       要修正 | MARKET注文前提とPaper fillの単純さが危ない                |
| バックテスト  | 改善中だがまだ弱い | 日足OHLCVベースのPFは実運用デイトレの根拠として弱い                |
| リスク管理   |    方向性は良い | ただしstop-loss、thin name、RULE-onlyの遮断をもっと硬くすべき |

## 一番の問題は「アルファ」より「入口と執行」

現状の `ProcessedFeatures` は、`price`, `sma_short`, `sma_long`, `rsi`, `vwap`, `volume_ratio`, `bollinger_*`, `order_book` くらいの構成です。これは最低限のテクニカル特徴量としては悪くないですが、日本株デイトレで必要な `spread_ticks`, `spread_bps`, `板厚`, `book imbalance`, `時間帯`, `寄り後何分か`, `引け前か`, `日次流動性`, `ATR`, `gap`, `市場レジーム`, `セクター`, `tick size bucket` が明示的に入っていません。([GitHub][3])

ルール戦略も、確認できた範囲では `SMA crossover`, `RSI threshold`, `Bollinger mean reversion/breakout` が中心です。RSI戦略はRSIが買い閾値以下ならBUY、売り閾値以上ならSELL、オプションで出来高・VWAP上・SMA上向きを見る形です。Bollingerも価格が下限を割ったらBUY、上限を超えたらSELLという平均回帰系です。([GitHub][4])

ここが弱いです。
RSIやBollingerは単体だと、**下落中のナイフを拾う**シグナルになりやすいです。特に日本株の小型・中型・Standard/Growth系では、板が薄い、スプレッドが広い、寄り天、材料剥落、指数リスクオフなどで簡単に死にます。

`entry_filters.py` も、BUY側のフィルターとしては `volume_ratio`, `price >= vwap`, `sma_short >= sma_long` 程度です。これは良い第一歩ですが、デイトレ実運用の入口としてはまだ浅いです。少なくとも、スプレッド、板厚、約定予定数量に対する板の深さ、日次出来高参加率、時間帯、地合い、直近下落モメンタムをGateway側で必ず見たほうがいいです。([GitHub][5])

## 危ない箇所1: GatewayがMARKET注文を作っている

`gateway/order_builder.py` は、現状 `OrderRequest` を `order_type=MARKET` 固定で作っています。これはデイトレの新規エントリーとしてはかなり危ないです。([GitHub][6])

`oms-paper/fill_simulator.py` も、MARKET注文だけをサポートしていて、BUYならask板を食う、SELLならbid板を食う擬似約定です。LIMIT注文は `limit_not_supported` になります。さらにVWAPを1円単位で丸めています。([GitHub][7])

これは日本株ではまずいです。東証は銘柄群や価格帯によって呼値が違います。TOPIX500系では0.1円、0.5円、1円などの細かい呼値があり、その他銘柄では1円、5円、10円などの粗い呼値になります。つまり「1円丸め」「MARKETで板を食う」だけだと、TOPIX500大型株と小型株で執行コストの意味が全然変わります。([東京証券取引所][8])

**修正方針は明確です。**

新規BUYは原則として `MARKET` 禁止。
使うなら、以下のどちらかです。

```text
1. passive limit
   best_bid に指値を置く
   一定秒数で未約定ならキャンセル

2. marketable limit
   BUYなら best_ask か best_ask + 許容tick
   SELLなら best_bid か best_bid - 許容tick
   それ以上滑るなら約定させない
```

MARKETを許していいのは、基本的に以下だけです。

```text
緊急損切り
大引け/日次クローズアウト
通信断・リスク停止時のポジション縮小
```

ただし、薄い銘柄で緊急MARKETを打つとそれも滑るので、本当は `marketable limit + retry` のほうが安全です。

## 危ない箇所2: Paperが「実運用に勝ちすぎる」可能性

`oms-paper/backtest/runner.py` は、注文と板スナップショットを時刻順にマージして、最新の板で `simulate_fill` しています。約定品質としてspread bps、fill ratio、depth、book imbalanceなどを出す方向に進化しているのは良いです。ただし、これはまだ「最新板を食うシミュレーター」であり、指値のキュー、部分約定、未約定、キャンセル、板寄せ、特別気配、注文応答遅延までは十分に再現していません。([GitHub][9])

つまり、現状のPaper/Backtestで勝っても、**Liveで同じように勝つとは限らない**です。特にこのrepoが狙っている日本株デイトレでは、負けパターンはだいたいこの3つです。

```text
1. シグナルは出たが、実際の板は薄い
2. MARKETで入って想定より高く買う
3. 損切りシグナル/SELLが遅れて、2%想定より大きく負ける
```

実際、2026年6月15日の運用ログでは、4346 NEXYZ.Groupで、09:01に600株を2回BUYし、その後803円で1200株SELL、実現損失 `-32,400円` という事例が記録されています。ログでは、この銘柄が低流動性で、Paperが1200株を抱えたこと、stop-loss条件を割っていたのにクローズが遅れたことが問題として整理されています。([GitHub][10])

ここはかなり刺さっているはずです。
あなたのアルゴが「イケてない」と感じる主因は、たぶん予測モデルよりも **薄い銘柄でのサイズ・MARKETエントリー・stop-loss実行遅延** です。

## 危ない箇所3: RULE-onlyがまだ強すぎる

Aggregator側は、以前よりかなり良くなっています。`consensus.py` では同一ソース内でBUY/SELLが混在したら支配シグナルなしにする、RULEとAIが揃えば加重平均、衝突時はデフォルトskip、source別のしきい値を見る、という形になっています。これは良い修正です。([GitHub][11])

ただ、運用ログ側を見ると、2026年6月10日のLive損失 `-40,310円` のレビューで「全Live trades were RULE」と書かれています。また、改善案として、RULE-only BUYはNORMAL地合いでのみ許可、CAUTIONではしきい値上げ/サイズ低下、RISK_OFFでは禁止、という提案が明記されています。([GitHub][12])

これはその通りです。
このrepoではAIを「アクセル」にするより、まず **ブレーキ** にしたほうがいいです。

具体的にはこうです。

```text
NORMAL:
  RULE-only BUY 可
  ただし spread/liquidity/depth 条件を満たす場合のみ

CAUTION:
  RULE-only BUY 原則不可
  AI同意 or confidence高 + size半減なら可

RISK_OFF:
  BUY全面禁止
  SELL/closeout/stop-lossのみ

CRASH:
  新規禁止
  既存ポジション縮小のみ
```

`market-regime-filter.md` にも、2026年6月8日のrisk-off相場でPaperが昼までに約 `-84,500円` の含み損、Liveが見送りで良かった、という記録があり、NORMAL/CAUTION/RISK_OFF/CRASH のレジームフィルターをGateway最終チェックに入れる案が書かれています。これは優先度を最上位にすべきです。([GitHub][13])

## 危ない箇所4: Universe Scannerが「上がりすぎ銘柄」を拾いやすい

Universe Scannerは、日次でJ-Quantsを使い、流動性・価格・市場などで絞った後、ボラティリティ、テクニカル、出来高急増、セクターモメンタムでスコアリングし、20〜50銘柄程度のwatchlistにする設計です。([GitHub][14])

ただ、運用レビューでは、Universe Scannerの問題として「momentum positive weight が寄り天の過熱銘柄を拾いやすい」「relative z-scoreが低ボラ環境で鈍い銘柄を拾う可能性」が挙げられています。これは非常に重要です。([GitHub][2])

今のUniverse Scannerは、おそらく **opportunity score** が強く、**risk penalty** が弱いです。
修正するなら、スコアをこう変えるべきです。

```text
final_score = opportunity_score - risk_penalty
```

risk_penaltyには最低限これを入れたいです。

```text
高価格帯ペナルティ
ATR過大ペナルティ
前日/当日gap過大ペナルティ
薄商いペナルティ
低売買代金ペナルティ
スプレッド広いペナルティ
板厚不足ペナルティ
寄り直後急騰後の失速ペナルティ
下落トレンドペナルティ
セクター集中ペナルティ
直近大陰線/出来高急増後ペナルティ
```

さらに、watchlistは必ず30銘柄埋める必要はないです。

```text
良い銘柄が7銘柄しかない日は7銘柄で止める
地合いが悪い日は0銘柄も許す
```

デイトレで一番やってはいけないのは、**毎日必ず何かを買うこと**です。

## バックテスト結果は「良さそう」だが、まだLive投入根拠には弱い

`docs/reports/backtest-2026-06.md` では、短期サンプルでは検証PFが高く見えるものの、日足OHLCV、実ロット、板流動性、税、約定失敗などが十分ではないと明記されています。さらに500営業日程度の長期検証では、train PF >= 1.2、validation PF >= 1.2、worst fold PF >= 1.0 を同時に満たす設定は `0/48` でした。これはかなり正直な結果です。([GitHub][15])

なので、今やるべきことはRSIやBollingerのパラメータ最適化ではないです。

やるべき順番はこれです。

```text
1. BacktestのPFを上げる
   ではなく

2. Liveで死ぬ条件をPaper/Backtestでも死ぬようにする
   その後で

3. 生き残った戦略だけパラメータ調整する
```

現状のバックテストでPFが良くても、MARKET注文、板スナップショット消費、日足OHLCV、薄商いサイズ過大が残っているなら、Liveでは簡単に崩れます。

## このrepoに対する最優先修正

### P0: 新規BUYをMARKET禁止にする

`gateway/order_builder.py` を直して、新規BUYは原則 `LIMIT` または `MARKETABLE_LIMIT` にするべきです。([GitHub][6])

例えば設計はこうです。

```python
# BUY entry
if signal.action == "BUY":
    if not execution_gate.allow_entry(features, account_state):
        reject()

    price = min(
        best_ask + max_slippage_ticks * tick_size,
        theoretical_entry_price + max_slippage_bps_limit
    )

    order_type = "LIMIT"
    limit_price = round_to_tick(price, tick_size)
```

SELL/損切りも、本当はこうしたいです。

```text
通常利確:
  passive/marketable limit

通常損切り:
  marketable limit + retry

緊急損切り:
  MARKET許可
  ただし薄板なら数量分割
```

Paper側の `fill_simulator.py` もLIMIT対応が必要です。

```text
BUY limit:
  limit_price >= askならaskを食う
  limit_price < askなら未約定
  best_bidに置くpassive orderは、次のtrade/book更新でqueue判定

SELL limit:
  limit_price <= bidならbidを食う
  limit_price > bidなら未約定
```

最初はキューを完全再現しなくてもいいですが、少なくとも **未約定** を発生させるべきです。今のようにMARKETだけだと、「エントリーできない」という現実がシミュレーションに出ません。

### P0: `ProcessedFeatures` を拡張する

今の特徴量だけだと、Gatewayが良い判断をできません。([GitHub][3])

最低限、これを追加したいです。

```python
spread_bps: Decimal | None
spread_ticks: Decimal | None

best_bid: Decimal | None
best_ask: Decimal | None
bid_depth_1: int | None
ask_depth_1: int | None
bid_depth_5: int | None
ask_depth_5: int | None
book_imbalance_5: Decimal | None

tick_size: Decimal | None
liquidity_tier: str | None
avg_daily_volume_20d: int | None
avg_daily_turnover_20d: Decimal | None

minutes_from_open: int | None
minutes_to_close: int | None
session_phase: str | None

atr_pct: Decimal | None
gap_pct: Decimal | None
market_regime: str | None
sector_regime: str | None
```

特に `spread_ticks` と `tick_size` は必須です。東証の呼値は銘柄・価格帯で違うので、1円幅が狭いのか広いのかは銘柄によって違います。([東京証券取引所][8])

### P0: GatewayにExecution Gateを置く

今のGatewayにはkill switch、lot calculator、validator、routerがあります。構造としては良いです。([GitHub][16])

ただ、新規エントリー前のGateをもっと硬くしたほうがいいです。

```python
def allow_new_buy(signal, features, account_state):
    if account_state.kill_switch_on:
        return False, "kill_switch"

    if features.market_regime in {"RISK_OFF", "CRASH"}:
        return False, "bad_market_regime"

    if signal.source == "RULE" and features.market_regime != "NORMAL":
        return False, "rule_only_not_allowed"

    if features.spread_ticks is None or features.spread_ticks > 2:
        return False, "spread_too_wide"

    if features.ask_depth_5 is None or features.ask_depth_5 < required_depth:
        return False, "insufficient_depth"

    if features.avg_daily_turnover_20d < 100_000_000:
        return False, "low_turnover"

    if order_qty > features.avg_daily_volume_20d * 0.01:
        return False, "participation_too_high"

    if features.minutes_from_open < 15:
        return False, "opening_guard"

    if account_state.daily_pnl <= -20_000 and signal.source == "RULE":
        return False, "soft_loss_rule_only_block"

    return True, "ok"
```

このrepoのdocsでも、2026年6月10日の損失レビューで、RULE-only BUYの制限、soft/hard loss throttle、100株固定サイズ問題、watchlist risk penalty、市場レジーム導入が挙げられています。これはそのままP0で実装していい内容です。([GitHub][12])

### P0: stop-lossを「戦略シグナル」ではなく「安全装置」にする

stop-lossはStrategySignalのSELL待ちにしないほうがいいです。
Gateway/OMS側で、ポジションごとに常時監視する安全装置にしたほうがいいです。

ログ上でも、4346の事例で、stop-loss条件を割っていたのにすぐ閉じられず、後からstop-loss retryが実装されたと書かれています。([GitHub][10])

設計としてはこうです。

```text
Strategy:
  エントリー理由と初期stopを提案

Gateway:
  stop価格を正式登録
  stop到達時はStrategyを待たずにSELL発注

OMS:
  SELL失敗時は同じposition_idでidempotent retry
  約定確認できるまで新規BUY禁止
```

これをやらないと、「2%ルールで入ったはずなのに、実損は4〜6%」が起きます。

## アルゴ側はこう変えるのがよい

今のRSI/Bollingerを完全に捨てる必要はありません。
ただし、単体でBUYさせるのはやめて、**Liquidity-Aware VWAP Reclaim** に変えるのが良いです。

現状のRSI戦略には、`require_price_above_vwap` や `require_sma_uptrend` のようなオプションがあります。これは良い方向です。([GitHub][17])

叩き台はこうです。

```text
BUY条件:
  RSIが売られすぎから回復
  price >= VWAP
  sma_short >= sma_long
  volume_ratio >= 2.0
  spread_ticks <= 2
  ask_depth_5 が注文数量の数倍以上
  book_imbalance_5 が悪化していない
  market_regime == NORMAL
  直近1〜3分の下落モメンタムが止まっている
  寄り後15分以降
```

つまり、RSIの「安いから買う」ではなく、**売られすぎた後にVWAPを奪回し、板と出来高が戻ったから買う**に変えます。

Bollingerも同じです。

```text
悪い:
  lower bandを割ったから買う

良い:
  lower bandを割った後、
  VWAP/短期MAを回復し、
  出来高が入り、
  spreadが狭く、
  板が戻り、
  地合いがNORMALなら買う
```

この変更だけで、落ちるナイフ系のエントリーはかなり減るはずです。

## `volume_ratio_min=2.0` はPaper候補、Live直行はまだ早い

バックテストレポートでは、`volume_ratio_min=2.0` が取引数とDDを減らし、Paper候補とされています。ただし、500営業日検証ではworst fold PFが弱く、Live readyではないという扱いです。([GitHub][15])

これは妥当です。
`volume_ratio_min=2.0` は、過疎銘柄を避ける方向には効きますが、出来高急増は「上昇開始」だけでなく「投げ売り」「材料剥落」「寄り天」でも発生します。

なので、出来高条件はこう使うべきです。

```text
volume_ratio 高い
かつ
VWAP回復
かつ
板改善
かつ
地合いNORMAL
かつ
spread狭い
```

出来高だけで買うと危ないです。

## 大引けまわりも見直し対象

このrepoのOMS Liveには「14:50 day closeout」とあります。([GitHub][14])
一方で、東証は2024年11月5日にarrowhead 4.0へ移行し、現物市場の終了時刻を15:00から15:30に延長し、クロージング・オークションを導入しています。([東京証券取引所][18])

14:50 closeout自体は、安全運用としては悪くないです。
ただし、現在の市場構造では、これは「大引け前に逃げる安全設計」であって、「引け前アルファを取りに行く設計」ではありません。

方針を分けたほうがいいです。

```text
安全運用モード:
  14:50以降は新規BUY禁止
  既存ポジションを縮小
  引け板寄せは触らない

引け戦略モード:
  15:00〜15:25を別モデル化
  クロージング・オークションを別シミュレーター化
  通常ザラバ戦略と混ぜない
```

現段階では、安全運用モードでいいと思います。まずは朝の損失と薄商い事故を潰すほうが先です。

## 最初に切るべきPRは3本

### PR 1: Execution Gate + MARKET BUY禁止

対象は `gateway` と `oms-paper` です。

やることはこれです。

```text
gateway:
  新規BUYのMARKET禁止
  spread/depth/liquidity/regime/time gate追加
  marketable limit価格を作る

oms-paper:
  LIMIT注文対応
  未約定/部分約定を発生させる
  tick_sizeに応じた丸め
  fill失敗をmetricsに出す
```

このPRが一番重要です。
ここを直さないまま戦略パラメータをいじっても、Liveで滑ります。

### PR 2: Market Regime GuardをGateway最終判定に入れる

docsにある `NORMAL`, `CAUTION`, `RISK_OFF`, `CRASH` を、StrategyではなくGatewayの最終判定に入れるべきです。`market-regime-filter.md` でも、AIはアクセルではなくブレーキとして使う案が書かれています。([GitHub][13])

ルールはまず単純でいいです。

```text
NORMAL:
  通常

CAUTION:
  RULE-only BUY禁止
  AI同意ありのみ
  size 50%

RISK_OFF:
  BUY禁止

CRASH:
  BUY禁止
  closeout優先
```

地合い入力は、最初は雑でもいいです。

```text
TOPIX/Nikkei/Growth指数のgap
前場開始後の騰落銘柄数
watchlist内の上昇/下落比率
先物の方向
自分の当日PnL/DD
```

日経225やTOPIX先物は現物寄り前の8:45から日中取引が始まるので、朝の地合い判断に使いやすいです。([東京証券取引所][19])

### PR 3: RSI/BollingerをVWAP Reclaim型に変える

対象は `strategy-rule` と `feature-engine` です。

現状のRSI/Bollingerは、BUY条件をかなり絞ったほうがいいです。

```text
RSI oversold
  + price above VWAP
  + SMA uptrend
  + volume_ratio >= 2.0
  + spread_ticks <= 2
  + depth enough
  + market_regime NORMAL
```

`entry_filters.py` にすでに `volume_ratio`, `price >= vwap`, `sma_short >= sma_long` の入口があるので、そこを拡張するのが最短です。([GitHub][5])

## やらないほうがいいこと

今すぐやらないほうがいいのはこれです。

```text
RSI閾値を30から25に変えるだけ
Bollingerのsigmaをチューニングするだけ
LLMプロンプトを複雑にするだけ
AIのconfidenceを信じてsizeを上げる
日足OHLCVバックテストのPFだけでLive投入する
薄い銘柄で100株/数百株を固定的に買う
寄り直後にRULE-onlyで連打する
```

このrepoの現在の状態だと、戦略の賢さよりも、**入ってはいけない局面で入らないこと**のほうが効きます。

## 私ならこう運用条件を置く

Live再開・拡大の条件は、かなり硬くします。

```text
1. Paperで少なくとも10営業日
2. 新fill simulatorで未約定/部分約定込み
3. RULE-only BUYの損益を単独で確認
4. 銘柄別・時間帯別・流動性別PnLを見る
5. 09:00〜09:15の新規BUYは原則なし
6. RISK_OFF日はBUYなし
7. 1銘柄の想定損失が日次許容損失の20%を超えるならskip
8. daily loss -20,000円でRULE-only停止
9. daily loss -40,000円で新規停止
10. stop-loss未約定/retry中は新規BUY禁止
```

docsには、プロジェクト全体のkill switchとして「2026-09-30までにout-of-sample PF > 1.2、最大DD < 資本の10%を満たせなければ、Live戦略開発を停止または転用」という基準もあります。この基準は良いです。むしろ今は、それに加えて **執行品質込みのPaper基準** を追加するべきです。([GitHub][20])

## まとめ

このrepoは、**システム屋としてはかなり良いところまで来ています**。
でも、デイトレアルゴとしてはまだ次の状態です。

```text
インフラ: 良い
監視・ログ: 良い
リスク設計: 方向性は良い
実運用の反省: 良い

シグナル: まだ素朴
執行: MARKET依存が危険
Paper: Liveより甘い可能性
地合いフィルター: 最優先で強化すべき
流動性制御: もっと硬くすべき
```

一番効く改善は、**AIを賢くすることではなく、Gatewayで「買ってはいけないBUY」を落とすこと**です。
次に、MARKET新規BUYをやめて、LIMIT/marketable limitと未約定をPaperに入れる。
その後で、RSI/Bollingerを「VWAP reclaim + 板 + 出来高 + 地合い」型に変える。

この順番で直すのが、このリポジトリには一番合っています。

[1]: https://github.com/hiro88hyo/roboinvest/ "GitHub - hiro88hyo/roboinvest · GitHub"
[2]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/docs/handoff/2026-05-performance-review.md "raw.githubusercontent.com"
[3]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/contracts/python/trade_contracts/features.py "raw.githubusercontent.com"
[4]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/services/strategy-rule/src/strategy_rule/strategies/sma_crossover.py "raw.githubusercontent.com"
[5]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/services/strategy-rule/src/strategy_rule/strategies/entry_filters.py "raw.githubusercontent.com"
[6]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/services/gateway/src/gateway/order_builder.py "raw.githubusercontent.com"
[7]: https://github.com/hiro88hyo/roboinvest/raw/refs/heads/main/services/oms-paper/src/oms_paper/fill_simulator.py "raw.githubusercontent.com"
[8]: https://www.jpx.co.jp/english/equities/trading/domestic/07.html?utm_source=chatgpt.com "Tick Size | Trading Rules of Domestic Stocks | Japan Exchange Group"
[9]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/services/oms-paper/src/oms_paper/backtest/runner.py "raw.githubusercontent.com"
[10]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/docs/handoff/2026-06-operations-log.md "raw.githubusercontent.com"
[11]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/services/aggregator/src/aggregator/consensus.py "raw.githubusercontent.com"
[12]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/docs/features/trading-loss-control-review.md "raw.githubusercontent.com"
[13]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/docs/features/market-regime-filter.md "raw.githubusercontent.com"
[14]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/main/CLAUDE.md "raw.githubusercontent.com"
[15]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/docs/reports/backtest-2026-06.md "raw.githubusercontent.com"
[16]: https://github.com/hiro88hyo/roboinvest/tree/main/services/gateway/src/gateway "roboinvest/services/gateway/src/gateway at main · hiro88hyo/roboinvest · GitHub"
[17]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/refs/heads/main/services/strategy-rule/src/strategy_rule/strategies/rsi_threshold.py "raw.githubusercontent.com"
[18]: https://www.jpx.co.jp/english/corporate/news/monthly-headline/202411.html?utm_source=chatgpt.com "JPX Monthly Headlines | Japan Exchange Group"
[19]: https://www.jpx.co.jp/english/derivatives/rules/trading-hours/index.html?utm_source=chatgpt.com "Trading Hours | Derivatives | Japan Exchange Group"
[20]: https://raw.githubusercontent.com/hiro88hyo/roboinvest/main/AGENTS.md "raw.githubusercontent.com"
