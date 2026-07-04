# Swing 収益向上: 改善指示書 (2026-07-04)

宛先: 実装エージェント (Codex)
発行者: レビューセッション 2026-07-04 (strategy/swing-rebuild ブランチ全体レビューに基づく)

このブランチのリサーチ規律 (事前登録 / matched random baseline / purge付きsplit /
placebo / locked OOS 一発勝負) は正しく機能している。以下の指示は、その規律を
**維持したまま**、ボトルネックを「方法論」から「統計的検出力と運用証拠の収集」へ
移すためのもの。

## 全作業共通のガードレール

1. locked OOS (`--split locked-oos --include-locked-oos`) を**新たに実行しない**。
   本指示書のどのタスクも locked OOS の閲覧を要求しない。
2. 既存 rejected / research-continuation 候補の閾値 (PER, yield, veto, exit horizon,
   stop) を変更しない。変更が必要に見えた場合は実装せず、理由を書いて停止する。
3. live 経路 (`oms-live`, `live-orders`) に触れない。paper 経路の変更も
   `trade_mode=paper` 前提のコードパスに限定する。
4. 各タスクは独立コミット (Conventional Commits)。push はユーザー指示待ち。
5. push 前ゲート (`make lint-all`, 対象サービス unit test, contracts 変更時は
   `make test-all`) は CLAUDE.md の通り。
6. フェーズごとにユーザー確認を取る。特に P0-2 は Phase 0 の設計ドキュメントを
   ユーザーが承認するまで実装に進まない。

---

## P0-1: Locked OOS 検査レジストリと凍結宣言

種別: ドキュメントのみ。小。

背景: 同一の locked OOS 窓を dividend 候補・forecast revision 候補・cluster v0・
cluster v1 で既に複数回閲覧しており、「一発勝負」の前提が侵食されている。
生き残った cluster v1 は複数回試行の生存者である可能性を明示的に管理する必要がある。

作業:

1. `docs/adr/0005-locked-oos-inspection-freeze.md` を新規作成する。内容:
   - これまでの locked OOS 閲覧履歴の一覧表
     (候補ID / 閲覧日 / 結果ファイル / 根拠レポートへのリンク)。
     `docs/features/event-ai-swing-plan.md` と `docs/reports/` から漏れなく収集する。
   - 決定: 現行 locked OOS 窓 (`locked_oos_start` 以降) は本サイクルで凍結。
     新規閲覧は「新しい forward データが 60 営業日以上蓄積した後」または
     「ユーザーの明示承認」のみ許可。
   - 今後の真の OOS は 2026-07 以降の forward データと paper 観測で構成する方針。
2. `docs/features/event-ai-swing-plan.md` の末尾に ADR-0005 への参照を1段落追加する。

受け入れ条件: 閲覧履歴表に cluster v0/v1、dividend、forecast revision、
earnings deep value の各閲覧が全て記載されていること。

## P0-2: cluster v1 の paper 観測導線 (最大の実装タスク)

種別: 設計 + 実装。大。フェーズ制。

背景: `event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research` は
ADR-0004 の Paper Observation Gate を数値上ほぼ満たしている
(locked OOS 1M: PF 2.036 / DD 41,194 / percentile 0.737。1M の percentile のみ
p75 に僅かに未達、2M/5M は 0.853/0.927)。paper 観測は資金リスクゼロの証拠収集で
あり、低頻度戦略では開始が遅いほど live 判断が遅れる。
エントリ側の導線 (日次イベント検知 → シグナル発行) が未実装。
exit 側は `oms-paper opening-swing-exits` CLI と
`docs/runbook/swing-paper-opening-exit-gate.md` まで整備済み。

### Phase 0: 設計ドキュメント (実装前にユーザー承認必須)

`docs/features/event-cluster-paper-observation-plan.md` を作成する。含めるもの:

1. Gate 判定の明文化:
   - ADR-0004 Paper Observation Gate との対照表 (各条件 PASS/FAIL/借用値)。
   - 1M percentile 0.737 < 0.75 の扱い: 「2M/5M では満たす」「観測のみで
     資金リスクなし」を根拠に paper 観測を開始する判断案を記載し、
     最終承認はユーザーに委ねる。
   - paper 観測の成功/失敗の事前定義 (例: 観測 6 ヶ月 or 15 トレードで、
     バックテスト想定スリッページとの乖離、fill 率、運用順序の再現性を判定)。
     ここで決めた基準を後から緩めない。
2. アーキテクチャ決定: 日次バッチがどこから signal を流すか。
   - 制約: サービス間直接通信禁止。Gateway のリスク検証
     (2% rule, lot calculator, kill switch) を必ず通す。
   - 推奨案: 日次バッチ (universe-scanner と同様のバッチ形態) が
     J-Quants `/fins/summary` の当日開示を取得 → 既存
     `event_research_common` のルールで cluster v1 該当を判定 →
     `StrategySignal` (source=RULE, stop_loss_price=catastrophic stop 相当) を
     `strategy-signals-a` に publish → aggregator → gateway → `paper-orders`。
   - aggregator が単一ソースシグナルを通すかを確認し、必要な合議制設定
     (RULE 単独 fallback) を設計に含める。
   - 約定後 `positions.scheduled_exit_date = entry + 20 TSE 営業日` が
     設定される経路 (oms-paper 側の対応状況) を確認し、不足があれば列挙する。
3. 運用スケジュール: 開示取得タイミング (夕方バッチ)、翌営業日寄りエントリ、
   `opening-swing-exits` の朝バッチ、の時系列図。
4. 観測ログ設計: バックテストとの突合に必要な記録
   (signal 日時、想定エントリ価格、実 fill、乖離 bps、除外理由)。

### Phase 1 以降 (Phase 0 承認後)

- Phase 1: 日次イベント検知バッチ (dry-run 出力のみ、publish なし) + unit tests。
  実データで 1 週間分の検知結果を再現し、リサーチ側の event 判定と一致することを
  fixture テストで固定する。
- Phase 2: paper 限定 publish 有効化 (環境変数 flag、デフォルト off) +
  runbook 更新。
- Phase 3: 観測レポートスクリプト (`scripts/report-event-paper-observation.py`
  相当): trades_paper とバックテスト想定の突合表を出す。

受け入れ条件: Phase 0 ドキュメントに「これは live 昇格ではない。live gate
(PF > 1.2 / DD < 10% / paper 再現) は不変」が明記されていること。

## P1-3: AI アームの最小効果量ゲート (タイムボックス)

種別: ドキュメント + 小実装。中。

背景: train 部分結果 (13,003 labels) で AI 単独は全 exit で負、
rule 通過後の二段目でも fixed_2d PF 1.310→1.328 と薄い。ローカル LLM 可用性
問題・62k jobs・placebo 検証体制のコストに対し、撤退基準がないと
sunk cost で走り続けるリスクがある。

作業:

1. `docs/features/event-ai-swing-plan.md` に「Train Minimum Effect Gate」節を
   追加し、以下を事前登録する (値はこの通りで固定。変更はユーザー判断):
   - 対象: earnings train 全 labels 完了時点の
     `ai_fundamental_and_technical` vs `fundamental_and_technical`。
   - 継続条件 (全て必須):
     - fixed_2d または fixed_5d で PF 改善幅 >= +0.10
     - 同 exit で net PnL が rule-only を下回らない
     - 除外されたトレード群 (rule pass かつ ai reject) の PF < 1.0
       (AI が実際に悪いトレードを落としている証拠)
   - 条件を満たさない場合: validation を閲覧せず AI アームを凍結し、
     凍結レポートを `docs/reports/` に書く。
2. `scripts/report-event-ai-train-labels.py` に上記ゲートの自動判定を追加する
   (`train_minimum_effect_gate: PASS/FAIL/INSUFFICIENT_LABELS` を JSON/CSV 出力に
   追加。ラベル完了率 100% 未満は INSUFFICIENT_LABELS)。unit test を追加する。
3. 判定は train のみで完結させること。validation / locked OOS には一切触れない。

## P1-4: train-only スキャナの盲点修正 (ポートフォリオレベル指標の追加)

種別: 実装。中。

背景: forecast revision fixed_5d は observation レベルでは locked OOS まで
生き残ったが、portfolio レベルの validation random 比較で落ちた。つまり
「observation レベルで選んで portfolio レベルで死ぬ」が既知の失敗モード。
現行 `scripts/scan-event-rule-only-train.py` は observation レベル指標のみで
次の仮説を選ばせるため、同じ失敗を繰り返す構造になっている。

作業:

1. `scan-event-rule-only-train.py` に train-only の portfolio シミュレーション
   指標を追加する (`simulate-event-portfolio.py` / `event_research_common` の
   既存ロジックを再利用。重複実装しない):
   - capital 1M/2M/5M の net PnL, PF, max DD
   - portfolio レベル `same_symbol_random_date` percentile (seeds は既存既定と同じ)
   - 実行時間が問題になる場合は `--portfolio-capital` で対象 capital を
     絞れるようにする。
2. 出力に「既に validation / locked OOS を閲覧済みの候補ファミリー」を
   マークする列を追加する (P0-1 のレジストリと同じ一覧をソースにする)。
   閲覧済みファミリーの再登録を機械的に警告するため。
3. unit test: fixture で portfolio 指標列とマーク列の出力を固定する。

禁止: このスキャナの結果から validation を自動実行しない。次の validation 仮説の
選択・事前登録はユーザーが行う。

## P1-5: 分足データの記録開始 (後から取得できない資産)

種別: 設計 + 小実装。中。

背景: `next_0915_conditional` は分足がないためスキーマのみ。分足は遡及取得
できないため、記録開始が 1 日遅れるごとに将来の研究データが 1 日分失われる。
Feeder / feature-engine には `STORAGE_TICK_RESOLUTION=1m` の保存基盤が既にある。

作業:

1. 現状確認: feature-engine の 1m 保存が event 研究に必要な形
   (symbol, 1分 OHLCV, 寄り〜09:30 を確実にカバー) で Parquet に残るかを
   コードで確認し、結果を短いメモ
   (`docs/features/event-minute-data-capture.md`) にまとめる。
2. ギャップ対応: Feeder は watchlist 銘柄のみ購読するため、前日引け後〜当日朝に
   開示があった「イベント銘柄」が watchlist に入らなければ分足が残らない。
   - 対応案を同メモで設計: 日次イベント検知バッチ (P0-2 Phase 1 と共用) の
     検知銘柄を watchlist に `selected_reasons=["event_capture"]` で追加投入する。
   - kabu.com WebSocket の銘柄登録上限との整合 (既存 watchlist + イベント銘柄で
     上限内に収まるか) を確認し、優先順位ルールを決める。
3. 実装は「watchlist へのイベント銘柄追加投入」の最小構成のみ。feature-engine 側の
   保存パイプラインは既存のまま使う。unit test を追加する。

受け入れ条件: 市場営業日に手動実行すれば、当日イベント銘柄の 09:00-09:30 の
1 分足が Parquet に残る状態になること (実観測はユーザーが行う)。

## P2-6: 履歴拡張に備えたパイプラインの再実行性確認

種別: 確認 + 微修正。小。

背景: cluster v1 の最大の弱点はイベント数 (train 63 / validation 32 /
locked OOS 22)。J-Quants 有料プラン導入で 2021-06-25 以前へ履歴拡張した際、
データセット再構築で split 境界が動き、既存の locked OOS 凍結と矛盾する恐れがある。

作業:

1. `split_manifest` (`scripts/event_research_common.py`) は観測データの日付分位で
   split を決めるため、履歴を過去方向へ拡張すると `train_end` /
   `locked_oos_start` が移動することを確認し、影響をメモにまとめる。
2. 対応: split manifest を外部ファイルで固定できるオプション
   (`--split-manifest path/to/manifest.json`) を evaluate / build 系スクリプトに
   追加する。既存 manifest を渡した場合、境界は固定され、新しい過去データは
   全て train 側に落ちること。unit test で固定する。
3. これにより「過去方向の履歴拡張は train を増やすだけで、validation /
   locked OOS の窓は動かない」ことを保証する。

## 実施順序

```
P0-1 (即日) → P0-2 Phase 0 (設計、ユーザー承認待ち) → P1-3 / P1-4 / P2-6 (並行可)
→ P0-2 Phase 1-3 → P1-5 (P0-2 Phase 1 の検知バッチに依存)
```

P1-3 は次回のローカル LLM train 再開前に完了していること (再開後に基準を
決めるのは事後基準になるため)。
