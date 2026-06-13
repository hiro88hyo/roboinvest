# roboinvest 酷評 × ドチャクソ儲けるための改善プロンプトセット

> リポジトリ: https://github.com/hiro88hyo/roboinvest
> 分析日: 2026-06-12
> 分析対象: 自動株式トレードシステム(日本国内現物株・auカブコム証券)

---

## 第1部: 酷評 — 「儲ける機械」ではなく「儲かってるか測れない機械」

### 総評

**538ファイル・9マイクロサービス・Pub/Sub・OTel・Supabase Realtime を積み上げた先にあるアルファは、RSI 25/75・SMAクロス・ボリンジャー逸脱という、教科書の最初の3ページである。** インフラは7割本番品質、戦略は0割。これは「トレードシステム」ではなく「トレードシステムを動かすための配管工事の見本市」だ。

### 致命傷リスト

#### 1. アルファが存在しない、しかも存在しないことを測る手段もない

- ルール戦略は3個だけ。パラメータ(RSI 25/75、SMAギャップ0.5%、ボリンジャーtolerance 0.15)は全部ハードコードで、**検証された形跡がゼロ**(`services/strategy-rule/src/strategy_rule/config.py:38-42`)。市場参加者全員が見ている指標を素朴な閾値で叩いて超過リターンが出るなら誰も苦労しない。

- 「バックテスト」と称するものの出力は **`feature_count` と `signal_count`、つまりシグナルの個数を数えるだけ**(`services/strategy-rule/src/strategy_rule/backtest/runner.py`)。PnL・勝率・シャープ・最大DD・期待値の計算コードはリポジトリ全体で **0行**。`grep "sharpe|drawdown|win_rate"` → ヒットなし。パラメータスイープもウォークフォワードもなし。**儲かるかどうかを一度も数値で問うていないシステムが、儲かるわけがない。**

#### 2. 目玉の「AIハイブリッド」が本番9営業日間、無言で死んでいた

- 2026-05-21〜29のライブ運用123約定のうち **AI由来は0件**。gemini-2.5-flash の thinking tokens が `AI_MAX_OUTPUT_TOKENS=256` を食い潰してJSONが途切れ、パース失敗→`return None`→**誰も気づかない**(`services/strategy-ai/src/strategy_ai/strategy.py`、`docs/handoff/2026-05-performance-review.md`)。

- LLM失敗・パース失敗・HOLD・confidence欠落(→自動0.0)は全部サイレントに握り潰される設計。**「AIハイブリッド戦略」のAIが沈黙しても外形上何も変わらないなら、そのAIの寄与は定義上ゼロ**。

- そもそも温度0のflashモデルに板3段とRSIを見せて5分に1回「BUY/SELL/HOLD」を聞く設計に、統計的根拠の引用は一切ない。

#### 3. 合議制(Aggregator)が数学的に出鱈目

- RULE:AI の重みは **1.0:1.0、根拠なし**(`services/aggregator/src/aggregator/config.py:33-38`)。検証済みでもないルールと、9日間沈黙していたLLMを同格に扱う。

- 単独シグナルの採用閾値は confidence 0.5、なのに**両者一致時は 0.3 に下がる**。「二つの不確かな情報源が一致したら個別より低い確信度で通す」— 合議の意味が逆転している。

- そして confidence 自体が各戦略で「閾値からの距離を線形スケール」しただけの**作り物の数字**。作り物同士を加重平均しても作り物である。

#### 4. リスク管理は「書いてあるが守れない」

- **総資金100万円がハードコード**(`services/gateway/src/gateway/config.py:38`)。実際 2026-06-01 に kabu 側で「可能額が不足」(Code 21)が発生済み — 2%ルールの分母が現実とズレている動かぬ証拠。

- キルスイッチに **TOCTOU レース**: 複数シグナルが同時に daily_pnl を読むと損失上限をオーバーシュートする。DB側の原子性保証なし。

- **部分約定の残数量は黙って消える**。トランザクションなしの3段書き込み(trades→positions→pnl)はクラッシュで必ずずれる。リコンサイルは手動フラグ実行のみ。

- 実績: 9営業日で **+46,766円、うち1日で-45,540円**。日次損失上限は「大損した後に振り返りで気づく」形でしか機能していない。この成績は統計的にコイン投げと区別がつかない。

#### 5. バックテストと本番が別世界

- バックテスト入力は**日足**(シグナルは1日1回、15:00固定)、本番は**tick+板**。検証している市場と戦っている市場が違う。

- Universe Scanner は 8:00 JST 起動なのに当日終値を参照しうる**ルックアヘッド**、上場廃止銘柄の**サバイバーシップバイアス**未対応、手数料(約0.099%)・スリッページ・税金(20.315%)**全部ゼロ**。Paper約定は板を直線消費するだけのスリッページゼロ仮定。

- バックテスト結果のレポート・ノートブックは**リポジトリに1つも存在しない**。

#### 6. 運用は「次セッションで検討」の墓場

- アラートなし(closeout失敗・broker error・AI沈黙、全部人間の目視待ち)。単一マシンSPOF、warm/coldデータはバックアップゼロ。CIは `-m "not integration"` で統合テストをスキップし、docker build すら検証しない。HANDOFF.md には「次セッション以降」が並ぶ。

### 結論

**配管(インフラ)は上等、水(アルファ)は一滴も流れていない。そして水道メーター(収益計測)が付いていないので、流れていないことに本人が気づけない構造になっている。**

---

## 第2部: 「ドチャクソ儲けたい！」の分解

### 儲けの方程式

```
儲け = エッジ(期待値) × 回転数 × サイズ − コスト
ただし破産しないこと
```

### 現状マッピング

| 構成要素 | 現状 | ボトルネック度 |
|---|---|---|
| エッジ(期待値>0の戦略) | **未検証(おそらく無い)** | ★★★ 最大 |
| 計測(エッジの有無を知る手段) | **存在しない** | ★★★ 全ての前提 |
| コスト(手数料・スリッページ・税) | **モデル化ゼロ** | ★★ 偽エッジ製造機 |
| サイズ(資金管理) | 分母ハードコード・レース有 | ★★ 破産防止に直結 |
| 生存(監視・冪等性・整合性) | 半完成 | ★ |
| インフラ | 過剰なほど完成 | — もう触るな |

### 最短経路

**「ドチャクソ儲ける」ための最短経路は、新機能でもLLM強化でもなく以下の順:**

1. **測れるようにする** → バックテストに PnL・勝率・シャープレシオを追加
2. **コスト込みで負ける戦略を殺す** → パラメータスイープして PF < 1.0 を除外
3. **勝つパラメータだけ残す** → 検証期間で profit factor > 1.2 の組み合わせのみ運用開始
4. **勝ってる時だけサイズを上げる** → daily_pnl > 0 の日だけ翌営業日の定量サイズを1.5倍に
5. **計測ループを回す** → 毎月末に sweep 再実行、負け筋を即座に切る

### 正直な注記

どんなプログラムも正のエッジを保証はできない。プログラムにできるのは：

- 期待値を最大化し
- 負けを早く検出し
- 破産を防ぐ

ことまで。以下のプロンプトはそれを機械的に回すためのもの。

---

## 第3部: 低インテリジェンスモデル向け改善プロンプトセット

### 設計方針

- ①判断を求めない(全部チェックリストとpass/fail条件)
- ②出力を固定JSONに縛る
- ③1チケット=1修正の最小スコープ
- ④資金に触る箇所は必ず人間ゲート

オーケストレータは「AUDIT→findingsを優先度順にFIXへ1件ずつ渡す→FIX後に再AUDIT→HITLで人間承認→次へ」という状態機械を回すだけ。

### プロンプト0: オーケストレータ(ループ制御)

```text
あなたはオーケストレータです。判断はしません。以下の手順を順番に実行するだけです。

状態ファイル: docs/improvement/state.json(なければ {"phase":"AUDIT","findings":[],"done":[]} で作成)

手順:
1. state.json を読む。
2. phase が "AUDIT" なら → プロンプトA を実行し、出力JSONの findings を state.json に保存し、phase を "FIX" にする。
3. phase が "FIX" なら → findings の中で status="open" かつ priority が最小の数字のものを1件選び、プロンプトB にその finding を渡して実行する。完了したら status を "fixed" にし、phase を "HITL" にする。
4. phase が "HITL" なら → プロンプトC を実行し、人間の回答を待つ。
   - 人間が "approve" → finding を done に移し、phase を "AUDIT" に戻る(再監査)。
   - 人間が "reject" → finding の status を "open" に戻し、人間のコメントを finding.note に追記し、phase を "FIX" に戻る。
5. findings に status="open" が無くなったら「全件完了」と報告して停止する。

禁止事項:
- finding に書かれていないファイルを変更しない。
- services/oms-live/ と services/gateway/ の変更は、HITLで人間の "approve" を得るまで main にマージしない。
- 本番デプロイ(deploy-production)は絶対に自分で実行しない。人間だけが実行する。
```

### プロンプトA: AUDIT(監査)

```text
あなたは監査担当です。コードは修正しません。以下のチェックを上から順に全部実行し、
失敗した項目だけを findings として JSON で出力します。

各チェックの実行方法と合格条件は固定です。曖昧な判断はせず、条件に合うか合わないかだけ見ます。

[CHK-01] 収益計測の存在(priority=1)
  方法: リポジトリ全体で grep -rE "sharpe|max_drawdown|win_rate|profit_factor|expectancy" を実行。
  合格: services/ 配下のバックテストコードに上記指標の計算と出力が存在する。
  不合格時の修正指示: 「バックテスト出力に 総損益(手数料・税引後)/勝率/プロフィットファクター/最大DD/シャープレシオ を追加せよ。
  入力は約定リスト(entry/exit/qty/price)。手数料は約定代金×0.099%、税は年間利益×20.315%、
  スリッページは約定価格×0.05%を一律控除。出力は JSON ファイル backtest_report.json。」

[CHK-02] バックテストにPnLが存在するか(priority=1)
  方法: services/oms-paper/src/oms_paper/backtest/runner.py を読む。
  合格: BacktestSummary に実現損益の合計フィールドがあり、テストで検証されている。
  不合格時の修正指示: 「fills から realized_pnl を計算して summary に追加せよ。手数料0.099%控除込み。テストを書け。」

[CHK-03] パラメータスイープ(priority=2)
  方法: scripts/ に parameter_sweep または grid_search を含むファイルがあるか確認。
  合格: RSI閾値・SMA窓・ボリンジャーtoleranceを複数値で回し、CHK-01の指標で比較するスクリプトが存在する。
  不合格時の修正指示: 「scripts/parameter-sweep.py を作成せよ。RSI buy∈{20,25,30}, sell∈{70,75,80}、
  SMA短期∈{5,10,20}/長期∈{25,50,75}、bollinger tolerance∈{0.0,0.05,0.15} の全組合せを
  daily_ohlcv の直近2年でバックテストし、結果を sweep_results.csv に出力せよ。
  期間を 前半(学習)/後半(検証) に分け、両方の成績を別列で出すこと。」

[CHK-04] AI戦略の生存監視(priority=2)
  方法: services/strategy-ai/ で grep -rE "alert|heartbeat|silent" を実行。
  合格: LLM呼び出しの 成功数/パース失敗数/エラー数 がカウントされ、1時間シグナルゼロのとき WARNING 以上のログが出る。
  不合格時の修正指示: 「strategy-ai に呼び出し統計カウンタを追加し、市場時間中に60分連続で
  有効シグナル0件なら logger.error("AI_STRATEGY_SILENT") を出せ。テストを書け。」

[CHK-05] 総資金の動的取得(priority=3) ※gateway変更=HITL必須
  方法: services/gateway/src/gateway/config.py の capital を確認。
  合格: 固定値 Decimal("1000000") ではなく、kabu API の買付余力 または Supabase から起動時+定期取得している。
  不合格時の修正指示: 「lot_calculator の総資金を kabu /wallet/cash 相当の値から取得するよう変更せよ。
  取得失敗時は直近キャッシュ値を使い WARNING を出す。フォールバックの既定値は現行の100万円。」

[CHK-06] キルスイッチの原子性(priority=3) ※gateway変更=HITL必須
  方法: gateway の kill_switch 評価が read-then-act になっていないか確認。
  合格: daily_pnl の判定と注文許可が DB レベルで原子的(RPC/ストアドプロシージャ等)に行われる。
  不合格時の修正指示: 「Supabase に RPC 関数 check_and_reserve_risk(amount) を作り、
  daily_pnl と上限の比較・予約を1トランザクションで行え。Gateway はその戻り値だけで判断せよ。」

[CHK-07] 部分約定の処理(priority=3) ※oms-live変更=HITL必須
  方法: services/oms-live/ で部分約定(partial)後の残数量処理を確認。
  合格: 部分約定時に残数量が記録され、再発注 または 明示的な放棄ログ のどちらかが実装されている。
  不合格時の修正指示: 「部分約定時、残数量を trades_live 系のログに reason="partial_abandoned" として
  必ず記録せよ。再発注はしない(スコープ外)。記録のテストを書け。」

[CHK-08] ルックアヘッド除去(priority=2)
  方法: services/universe-scanner/src/universe_scanner/filters/dynamic.py と ingest/daily_ohlcv.py を読む。
  合格: スコアリングに使う OHLCV の end が「前営業日」になっている。
  不合格時の修正指示: 「as_of=当日 のとき必ず previous_business_day(as_of) までのデータで
  スコアリングするよう修正せよ。当日データを使わないことを検証するテストを書け。」

[CHK-09] バックテストレポートの実在(priority=2)
  方法: docs/ または reports/ に backtest_report*.json か *.md の結果報告があるか確認。
  合格: 直近の全戦略について CHK-01 の指標が記載されたレポートが存在する。
  不合格時の修正指示: 「CHK-01〜03 完了後、scripts/parameter-sweep.py を実行し、
  結果を docs/reports/backtest-YYYY-MM.md として保存せよ。」

[CHK-10] 負け戦略の停止判定(priority=4)
  方法: docs/reports/ の最新レポートを読む。
  合格: 検証期間でプロフィットファクター < 1.0 の戦略が config の DEFAULT_STRATEGIES から外されている。
  不合格時の修正指示: 「レポートで PF<1.0 の戦略名を DEFAULT_STRATEGIES から除外する PR を作れ。理由をPR本文に書け。」

出力形式(これ以外を出力しない):
{
  "audited_at": "<ISO日時>",
  "findings": [
    {"id": "CHK-XX", "priority": 1, "status": "open", "evidence": "<確認したファイルと行>", "fix_instruction": "<上記の修正指示をそのまま>", "hitl_required": true/false}
  ],
  "passed": ["CHK-XX", ...]
}
```

### プロンプトB: FIX(修正)

```text
あなたは修正担当です。渡された finding 1件だけを修正します。

入力: finding(JSON 1件)

手順(必ずこの順):
1. feature ブランチを作る: fix/<finding.id を小文字化>
2. finding.fix_instruction に書かれた変更だけを行う。書かれていないリファクタ・改善・最適化は一切しない。
3. 変更したサービスのディレクトリで以下を実行し、全部成功するまで修正する:
   uv run ruff format && uv run ruff check && uv run mypy --strict && uv run pytest
4. contracts/ を変更した場合のみ make test-all を実行する。
5. 以下のJSONだけを出力する:
{
  "finding_id": "CHK-XX",
  "branch": "fix/chk-xx",
  "files_changed": ["パス", ...],
  "tests_added": ["テスト名", ...],
  "lint_pass": true/false,
  "test_pass": true/false,
  "summary_ja": "<何をどう変えたか3文以内>"
}

禁止事項:
- finding に関係ないファイルの変更。
- テスト・lint が落ちた状態での完了報告。落ちたら lint_pass/test_pass を false にして正直に報告する。
- services/oms-live/ または services/gateway/ を変更した場合、マージ・デプロイをしない(HITL待ち)。
- 環境変数の既定値を変える場合、infra/docker-compose.prod.yml との整合を必ず確認し、summary_ja に明記する。
```

### プロンプトC: HITL(人間承認ゲート)

```text
あなたは報告担当です。コードは変更しません。人間(資金の持ち主)に以下のテンプレートで報告し、
"approve" か "reject: <理由>" の回答を待ちます。回答を推測したり代行したりしてはいけません。

---
## 承認依頼: {finding_id}

**何が問題だったか(1文):** {finding.evidence を平易な日本語で}
**何を変えたか(3文以内):** {fix結果の summary_ja}
**お金への影響:** 次のうち該当するもの → [発注ロジック変更あり / リスク計算変更あり / 計測・レポートのみで発注に影響なし]
**テスト:** lint={lint_pass} / test={test_pass} / 追加テスト {tests_added の件数}件
**この変更で起きうる最悪のこと(1文):** {fix_instruction から機械的に: 発注系なら「誤発注・過大ロット」、計測系なら「レポート数値の誤り」}

確認方法(人間向け、コピペで実行可):
  cd {変更したサービスのパス} && uv run pytest tests/ -v

approve / reject: <理由> のどちらかで回答してください。
oms-live / gateway の変更は、approve 後も paper モードで最低5営業日検証してから live に反映してください(あなたはこの注意書きを毎回必ず表示する)。
---
```

### 優先順位の根拠

priority 1(CHK-01/02)が全ての土台です。**収益が測れない限り、他のどの改善も「効いたかどうか不明」のままだからです。**

priority 2(スイープ・ルックアヘッド除去・AI監視・レポート)で「偽の儲け」と「沈黙する戦略」を排除し、priority 3(資金・キルスイッチ・部分約定)で破産経路を塞ぎ、priority 4 で初めて「負け戦略を切って勝ち筋に資金を寄せる」段に入ります。

**逆に言うと、マイクロサービスの追加・LLMモデルの変更・ダッシュボード強化はこのリストに入っていません — 儲けに寄与しないからです。**

---

## 最後に一点だけ釘を刺すと

5月実績の+46,766円(うち1日-45,540円)はサンプル9日では完全にノイズです。このプロンプトループを回してCHK-09のレポートに「検証期間でPF>1.2・最大DDが資金の10%未満」と数字が出るまでは、**live資金を増やす判断材料は何もない、というのが酷評の最終結論です。**