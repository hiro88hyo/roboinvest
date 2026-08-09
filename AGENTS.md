# AGENTS.md

Codex / coding AI 向けの作業メモ。Claude Code 時代の引き継ぎ資料
`docs/HANDOFF.md` とルート `CLAUDE.md` は参照資料として残っているが、
現在の主な作業入口はこの `AGENTS.md` とする。

## Project Overview

このリポジトリは、日本国内の現物株（auカブコム証券）を対象とした
自律型トレードシステム。ルールベース戦略と LLM/AI 戦略を組み合わせ、
Google Cloud Pub/Sub で疎結合にした Python マイクロサービス群と
Next.js Dashboard で構成されている。

主要コンポーネントは以下の 9 サービス + Dashboard。

- `services/universe-scanner`: J-Quants から監視銘柄を生成する日次バッチ
- `services/feeder`: kabu.com API から market data を取得して Pub/Sub に流す
- `services/feature-engine`: Tick/板情報からテクニカル指標を計算する
- `services/strategy-rule`: ルールベースの `StrategySignal` を生成する
- `services/strategy-ai`: LLM/AI ベースの `StrategySignal` を生成する
- `services/aggregator`: Strategy A/B のシグナルを統合する
- `services/gateway`: リスク検証と live/paper ルーティングを担当する
- `services/oms-live`: kabu.com API へ実発注し live 約定を記録する
- `services/oms-paper`: 板情報を使って paper 約定をシミュレートする
- `dashboard`: Supabase Realtime を使う Next.js ダッシュボード

## Current State

2026-05-15 時点の引き継ぎでは、ローカル paper trading と OMS Live Phase 3
までは完成している。

- 全 9 サービス + Dashboard 実装済み
- Paper エンドツーエンドは 4 ラウンドと 14:50 closeout を観測済み
- OMS Live Phase 3 は本番 28080 / 実発注 e2e の 4 ケースが PASSED
- リスク管理は 2% ルール、キルスイッチ、day closeout、swing 自動決済が稼働
- CI は python / dashboard / e2e の 3 job、Dependabot、coverage が緑

未着手または次の山:

- ADR-0001 の本番デプロイ実装
- J-Quants 有料プラン移行後の Universe Scanner 本番自動化
- 24/7 運用整備（監視、ログ集約、アラート、バックアップ）

## First Files To Read

作業開始時は、最低限これらを確認する。

1. `AGENTS.md`: Codex 向けの現行作業メモ
2. `docs/HANDOFF.md`: 最新の引き継ぎメモ
3. `CLAUDE.md`: 旧 Claude Code 向けのアーキテクチャ、Pub/Sub、Supabase、リスクルール、規約
4. 対象サービスの `services/<name>/CLAUDE.md`
5. `contracts/`: Pydantic / SQL / TypeScript の Single Source of Truth
6. `git status --short` と直近の履歴

## Core Principles

- `contracts/` を Single Source of Truth とする。スキーマ変更はここから始める。
- サービス間の直接通信は禁止。連携は Pub/Sub 経由にする。
- Gateway がリスクルールを単独で執行する。他サービスへ判断を分散しない。
- OMS Live は本番資金に直結するため、変更は最小限にし、先に OMS Paper で検証する。
- 純関数ロジックとストリーミング層を分離する。
- 既存のサービスごとの設計、fixture、テスト命名規約に合わせる。

## Project Kill Switch

資金ではなく、プロジェクト継続そのものに適用する反証契約。
戦略開発・live 拡大・資本増額の前に、この条件を優先して扱う。

- 期限: 2026-09-30
- 判定条件: アウトオブサンプル期間で `profit_factor > 1.2` かつ
  `max_drawdown < capital * 0.10` を達成すること。
- 判定対象: 事前登録した戦略・パラメータ・コスト前提のみ。判定期間開始後に
  都合よくパラメータを変更した結果は合格扱いにしない。
- 条件未達の場合: live 戦略開発を停止し、本リポジトリの資産を以下へ転用する。
  1. AI 協働開発リファレンスとしての公開・記事化
  2. 板・tick アーカイブの研究データセット化
  3. トレード再開は、資本スケール計画を先に文書化した場合のみ
- このルールを変更する場合は、変更理由を文書化し、少なくとも 1 週間の
  cooling-off 期間を置く。
- Codex は、この条件を弱める変更、判定先送り、判定後の後付け例外を
  通常の改善として扱わない。必要ならユーザーに明示確認する。

## Closed Cross-Sectional Research Cycle (2026-08-08)

`cross_sectional_adaptation_v0` は、事前登録した上限 2 候補を使い切り、
development で終了した。これは Project Kill Switch の判定証拠とは別であり、
kill switch の期限・条件や現行 live/paper 戦略を変更しない。

- Candidate 1 `liqimp1m_logdiff_v0_research`: development で
  `FROZEN_REJECTED_DEVELOPMENT`。PF 0.368、最大 daily MTM DD 58.55%。
- Candidate 2 `imom6m_top5_fixed20_v0_research`: Gate A で
  `FROZEN_REJECTED_DEVELOPMENT_GATE_A`。28 形成月中、事前固定した exact
  month-end outcome 完全性を満たしたのは 5 か月（最低 24）。完全 5 か月でも
  D10-D1 平均 -1.0583%、rank IC 平均 -0.03249、後半 spread -1.8805%。
- IMOM の欠損処理は outcome を見る前に固定した。適格銘柄に exact 翌月末終値が
  1 件でも欠ける月は全体を不完全とし、前後日の価格代用や欠損銘柄だけを除く
  complete-case 再計算は禁止する。
- IMOM Gate B、trade/PF/DD、validation、locked OOS は未実行で、今後もこの
  cycle の救済として実行しない。IMOM12M、skip-month、符号反転、quantile変更、
  regime/HTP/quality/value 組合せも即時 variant として試さない。
- 正本は `research/cross-sectional-adaptation-v0/cycle-closure.json`、
  `research/imom/imom6m-top5-fixed20-v0-disposition.json`、
  `docs/reports/imom6m-top5-fixed20-v0-gate-a-result-2026-08-08.md`。
- 次の研究を行う場合は、上位の戦略方針へ戻り、新しい仮説・trial budget・
  outcome 計算前の明示承認を別 cycle として文書化する。

## Portfolio Researchability Reset Phase 1 (2026-08-08)

`portfolio_researchability_reset_2026_v0` は alpha candidate ではない。9月30日まで
新 candidate を作らず、将来の研究に必要なデータ基盤を非性能情報だけで監査する。

- Phase 1 は既存 archive だけで実施し、Gate A の 57 missing symbol outcomes を再現。
  57 件すべてで exact outcome bar row と同日付の同一 code historical master row が
  ともに存在しなかった。
- 日付 batch、receipt、raw/normalized hash、partition は整合したため archive 全体の
  fetch/ingest failure ではない。ただし delisting、merger、share exchange、cash
  consideration、code lineage/reuse の明示 dataset がなく、全 57 件を `UNKNOWN` とした。
- 判定は `NO_GO_CURRENT_ARCHIVE_FOR_ALL_UNIVERSE_CROSS_SECTIONAL_RESEARCH`。
  現 archive で欠損銘柄を落とす、前後日の bar を代用する、消失を推測で delisting
  扱いする、IMOM/Gate A を再計算することは禁止する。
- Phase 2、追加 source/API 取得、strategy candidate、return/PnL 計算、paper/live は
  未承認。別の明示承認なしに進めない。
- 正本は `out/portfolio-researchability-reset-2026-v0/phase1-missingness-existing-archive-v0/`
  と `docs/reports/portfolio-researchability-missingness-audit-2026-08-08.md`。
- この NO-GO は Project Kill Switch evidence ではなく、9月30日の期限・条件を変更しない。

## Portfolio Researchability Reset Phase 2 (2026-08-08)

- 非性能の inventory は `COMPLETE_NO_PERFORMANCE_USED`。2026-06-30 historical master
  と公式 JPX category の交差から ETF 53件を固定し、判定は
  `NO_GO_PHASE3_CURRENT_INSTRUMENT_DATA_FOUNDATION`。
- 内訳は Japanese Equity (Market) category 34、sector/industry 18、570A 1。
  Market category 34件を broad-market とみなさず、methodology/version 完備までは
  `CLASSIFICATION_PENDING`。sector/industry は TOPIX-17 の17件を
  `INDUSTRY_SECTOR / TOPIX_17 / SECTOR_EXPOSURE` とし、1615は経済分類を
  `INDUSTRY_SECTOR / TOPIX_33_SECTOR / BANKS / CONFIRMED`、portfolio roleを
  `CLASSIFICATION_PENDING` と分離する。現行methodology文書の取得済み状態を、
  PIT methodology version coverageやbenchmark lineage完備とはみなさない。570A は
  `JPY_JAPAN_GOVERNMENT_BOND_0_1Y` exposure の
  `CASH_PROXY_CANDIDATE / UNVALIDATED` で、settlement cash / strict cash-equivalent
  ではない。
- kabu compatibility は K1 production symbol、K2 production board/register/PUSH、
  K3 validation order schema、K4 product/account cash-order/SOR、K5A production
  submit/cancel（意図的fillなし）、K5B minimum-lot execution/exit に分離。Phase 3 前に
  K1〜K4 が必要。K5A/K5B は paper 後・live 前かつ別認可まで禁止。
- 公式上限はPUSH専用ではなく、REST/PUSH共通のAPI登録銘柄上限50。static inventoryや
  登録解除・入替を伴うsequential compatibility verificationは妨げないが、53件の
  simultaneous runtime monitoringと寄り・引けauction observationには制約となる。
  上限拡大や単純rotationを仮定せず、価格・return・performanceを見ずに50以下のsubsetを
  固定する、代替market-data sourceを承認する、または必要な同時観測を失わないrotationを
  証明する必要がある。
- Phase 3を再認可する場合はscope、benchmark lineage、methodology version coverage、
  必要時のPIT look-through、K4 evidence expiry、execution-data mode、venue/SOR policy、
  provenanceを先に固定し、superseded成果物をactive pathから除外する。
- Phase 3、追加 broker/API取得、価格・return/PnL、strategy、paper/live は未承認。
  正本は `docs/reports/portfolio-researchability-instrument-inventory-2026-08-08.md`。

## Policy-Authorized Opportunity Router v0 (2026-08-09)

- 個別案件へのタイムリーな人間承認を前提にせず、policy/playbook versionを事前承認する
  `POLICY_AUTHORIZED_AUTOMATION_WITH_ASYNC_HUMAN_OVERSIGHT`をplan-onlyで記録した。
- 初期admitted playbookは0、上限3。LIQIMPとIMOM6Mは棄却状態を維持し、既存event laneや
  実装済みtechnical strategyも自動admissionしない。
- 案件判断はEvidence、Mechanism、Context、Execution、Portfolioの全gate必須。一つでも
  `FAIL / UNKNOWN`なら`NO_TRADE`。confidenceは当初sizingに使わず、LLM自由記述だけで
  entryを生成しない。
- 人間の不応答は判断へ影響せず、`WAITING_HUMAN`を作らない。人間は将来policyの事前承認、
  非同期監査、リスク削減方向の停止だけを担い、`NO_TRADE`からのpositive overrideは禁止。
- 全candidateと候補ゼロを保存し、損益と判断品質を分けたappend-only reviewを行う。
  取引時には規則を変えず、月次監査と四半期の別認可で将来versionだけを見直す。
- 実装前にcandidate intake/population hash、static mechanism対dynamic fit、複数playbook
  assignment、capacity resolution、counterfactual class、outcome-blind process review、
  position version/exit lifecycleを固定する。未固定・競合・tieはfail-closed。
- Gatewayが最終risk執行者である既存境界を維持する。Aggregator、contract、Pub/Sub、DB、
  Dashboardは未変更。実装、playbook選定、outcome計算、shadow、paper/liveは未承認。
- このmeta-processは現行Kill Switchの救済・証拠ではない。正本は
  `docs/features/policy-authorized-opportunity-router-v0.md` と
  `research/opportunity-router/policy-authorized-opportunity-router-v0-charter.json`。

## Common Commands

```bash
make lint-all
make test-all
bash scripts/start-paper-trading.sh
./scripts/gen-supabase-types.sh
uv run python scripts/health-check.py
```

Python は `uv` を使う。`pip` や `poetry` の直叩きは避ける。
Dashboard は Volta 管理の Node/npm を使う。

## Health Check Notes

`scripts/health-check.py` はローカル開発環境の軽量スモークテスト。
`uv run scripts/health-check.py` または対象を絞って `uv run scripts/health-check.py --check pubsub supabase` のように使う。

検査内容:

- Pub/Sub: `infra/pubsub/topics.json` の全 topics（現行 9 件）と
  `infra/pubsub/subscriptions.json` の全 subscriptions（現行 13 件）が emulator 上に存在するか確認する。
- Supabase: 主要 11 tables（上記 10 件 + `event_paper_stage_dispatches`）の read、
  `positions.scheduled_exit_date` / `positions.position_generation_id` /
  `trades_paper.order_id` / `trades_paper.position_generation_id`、および安全な validation
  probe による `event_paper_cas_strategy_reasoning` / `event_paper_stage_dispatch` /
  `oms_paper_apply_fill` / `oms_paper_update_stop_loss` の存在・service-role 実行可否を確認する。
- Services: 9 service modules (`universe_scanner`, `feature_engine`, `strategy_rule`, `strategy_ai`, `aggregator`, `gateway`, `oms_paper`, `oms_live`, `feeder`) の `python -m <module> --help` が起動できるか確認する。

必要 env がないセクションは `SKIP` で、失敗扱いではない。`NG` が 1 件でもあれば exit code 1。
Pub/Sub は `PUBSUB_EMULATOR_HOST` / `PUBSUB_PROJECT_ID`、Supabase は `SUPABASE_URL` / `SUPABASE_SECRET_KEY` が必要。

## Important Operational Notes

- event detector の凍結済み feature vintage は disclosure-time
  `data_available_at/feature_cutoff_at`。翌朝の実受信は
  `source_received_at` へ分離し、PER/valuation cutoff を進めない。
- event detector の必須OHLCV session は feature cutoff から決める。15:30 JST
  以降は signal-date、それ以前は直前TSE営業日であり、当該行が欠けても古いbarを
  代用しない。候補は `feature_data_complete=false` として残し cohort を維持するが、
  pre-open、watchlist、publisher は実行不可として拒否する。
- 現 event publisher/E2E は `opening_transport_stress_v1` であり、凍結済み
  next-open / 20日目 close を再現しない。receipt/report の
  `comparable_to_registered_backtest=false` を厳守し、target 実行や v1
  paper/live evidence への算入を行わない。
- event transport stress CLI は loopback Pub/Sub emulator + `--no-seek` に加え、
  loopback Supabase と allowlist 済み dev project ID だけを許可する。
  event 用 Supabase client と emulator gRPC channel は ambient proxy を継承しない。
- 既存の same-symbol random percentile は random 側 8% stop、選定側 10% stop
  で比較条件が不一致。simulator の将来コードは 10% に修正済みだが、locked
  report を再計算・再解釈せず、既存 percentile を gate evidence に使わない。
- 1Password 経由で env を読むときは、先に `infra/.op.service-account.env`
  があるか確認し、`set -a && . infra/.op.service-account.env && set +a`
  で読み込む。`OP_SERVICE_ACCOUNT_TOKEN` を手入力・別ファイルから流用しない。
  別 vault / 別用途の 1Password API token を使うと、`op run` は通っても
  必要な secrets が空または別値になり得る。token 値は terminal に表示しない。
- 1Password secret 参照は `op://roboinvest/...` が正。`op://Trade AI/...` ではない。
- Pub/Sub topics / subscriptions の SSOT は各 JSON。現行は topics 9 件、
  subscriptions 13 件だが、固定件数より JSON と health check の一致を優先する。
- Pub/Sub emulator は長時間稼働で OOM することがある。再起動後は topic/subscription の再 seed が必要。
- 市場開始前の主な失敗要因は subscription 未作成、`daily_ohlcv` 空、`watchlist` 未更新。
- kabuステーションは localhost 限定。本番は Windows 上の Caddy reverse proxy と SSH tunnel 前提。
- 本番 kabu.com API では SOR 必須で、`KABU_DEFAULT_EXCHANGE=9` が前提。
- 検証環境 18081 は `sendorder` を黙殺するため、実発注 e2e は本番 28080 のみ。
- Feeder と OMS Live は kabu token を `KABU_TOKEN_CACHE_FILE` で共有する。
- OMS Live は `KABU_API_PASSWORD` と `KABU_ORDER_PASSWORD` を別 env として読む。
- `OMS_LIVE_DRY_RUN=true` が `.env` に残っていることがあるので live 検証時は必ず確認する。

## Supabase And Contracts Notes

- `positions` は OMS Live 単一プロセス前提で非アトミック。複数プロセス化するなら Postgres RPC が必要。
- kabu 実保有との乖離は `scripts/reconcile-positions.py` で照合する。
- closeout 由来の `unified_signal_id` は `None` になり得る。
- TypeScript の Supabase 型は手動編集せず、`./scripts/gen-supabase-types.sh` で再生成する。

## Test And Lint Conventions

- 新サービスで `tests/conftest.py` を作らない。
- fixture は `src/<service>/_testing.py` に置く。
- `tests/__init__.py` は作らない。
- テストファイル名は `test_<service>_*` プレフィックスで衝突を避ける。
- `strategy-ai` の `--fixture-responses` は、文字列化された JSON object 配列が必要。

## Strategy Parameters To Preserve

- `RSI_BUY_THRESHOLD=25`
- `RSI_SELL_THRESHOLD=75`
- `SMA min_gap_ratio=0.005`
- `Bollinger tolerance=0.15`

テストや調査で緩めた場合は戻し忘れに注意する。

## Known Working Tree Note

引き継ぎ確認時点では `docs/HANDOFF.md` が未追跡だった。
ユーザーまたは Claude Code が作成したファイルとして扱い、明示依頼なしに削除・巻き戻ししない。

## Codex Environment Note

この環境では通常の sandbox 実行が `bubblewrap is unavailable` で失敗した。
必要な読み取り・検証コマンドは、ユーザー承認付きの escalated 実行が必要になる場合がある。
