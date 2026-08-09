# Handoff Memo (for coding AIs)

最終更新: 2026-08-08

このファイルは、次の coding AI が最初に読むための短い索引です。日次の長い運用ログはここに積まず、必要な詳細だけリンク先で確認してください。

## 1. Project Snapshot

日本国内現物株（auカブコム証券）向けの自律トレードシステム。ルールベース + AI/LLM 戦略を、Google Cloud Pub/Sub で疎結合した Python マイクロサービス群と Next.js Dashboard で構成します。

主要サービス:

- `universe-scanner`, `feeder`, `feature-engine`
- `strategy-rule`, `strategy-ai`, `aggregator`
- `gateway`, `oms-live`, `oms-paper`
- `dashboard`

詳細は [CLAUDE.md](../CLAUDE.md) を参照。

## 2. Current State

2026-08-08 時点の要点:

- cross-sectional adaptation research cycle は、事前登録した最大 2 候補を
  development で使い切って終了した。LIQIMP は PF 0.368 / 最大 daily MTM DD
  58.55% で棄却。IMOM6M は Gate A の exact month-end 完全月が 28 中 5
  （最低 24）で棄却し、完全月でも D10-D1 平均 -1.0583%、rank IC -0.03249。
  欠損銘柄だけを落とす再計算、Gate B、validation、locked OOS、IMOM 派生variantは
  禁止する。この結果は Project Kill Switch 証拠ではなく、現行戦略も変更しない。
  正本は `research/cross-sectional-adaptation-v0/cycle-closure.json` と
  `docs/reports/imom6m-top5-fixed20-v0-gate-a-result-2026-08-08.md`。

- 非 alpha の `portfolio_researchability_reset_2026_v0` Phase 1 を既存 archive
  だけで実施した。Gate A の 57 missing outcomes は全件、exact outcome bar と
  同日付 historical master の同一 code がともに不在だった。取得日 batch と
  raw/normalized hash は整合したが、delisting / merger / cash consideration /
  code lineage を明示する dataset がないため、推測せず全件 `UNKNOWN`。
  判定は `NO_GO_CURRENT_ARCHIVE_FOR_ALL_UNIVERSE_CROSS_SECTIONAL_RESEARCH`。
  この結果を受けて Phase 2 は別認可で実施したが、新candidate、性能計算は
  引き続き未承認。Phase 1 の正本は
  `docs/reports/portfolio-researchability-missingness-audit-2026-08-08.md` と
  `out/portfolio-researchability-reset-2026-v0/phase1-missingness-existing-archive-v0/`。

- `portfolio_researchability_reset_2026_v0` Phase 2 の investable-instrument
  inventory を完了した。2026-06-30 historical master の ETF 412 件を公式 JPX
  category と交差し、Japanese Equity (Market) category 34、sector/industry 18
  （1615 + TOPIX-17 17 件）、短期日本国債 ETF 1（570A）の計 53 件を収録。Market
  category 34件は broad-market とみなさず全件 `CLASSIFICATION_PENDING`。TOPIX-17の
  17件は `INDUSTRY_SECTOR / TOPIX_17 / SECTOR_EXPOSURE`。1615は経済分類を
  `INDUSTRY_SECTOR / TOPIX_33_SECTOR / BANKS / CONFIRMED`、portfolio roleを
  `CLASSIFICATION_PENDING` と分離し、現行文書取得済みでもPIT methodology versionと
  benchmark lineageは未完備とする。570A は
  `CASH_PROXY_CANDIDATE / UNVALIDATED` で、settlement cash / strict cash-equivalent
  ではない。普通株 3,899 件は aggregate baseline のみ。全 class で PIT
  termination/lineage、cash distribution を含む outcome、過去板/auction、商品別
  kabu K1〜K4 が未完備で、判定は
  `NO_GO_PHASE3_CURRENT_INSTRUMENT_DATA_FOUNDATION`。Phase 3、商品選択、strategy、
  paper/live は未承認。Phase 2 status は `COMPLETE_NO_PERFORMANCE_USED`。初回の
  sector 1615欠落と、その後2回のsemantic classification訂正は収益を見ずに実施し、
  旧成果物を監査隔離した。公式上限はPUSH専用ではなくREST/PUSH共通のAPI登録銘柄上限50。
  static inventoryやsequential verificationは妨げないが、53件同時runtime monitoringと
  寄り・引けauction observationには制約となる。K5はK5A submit/cancel（意図的fillなし）と
  K5B minimum-lot execution/exitに分け、paper後・live前かつ別認可まで行わない。Phase 3を
  再認可する場合はscope、benchmark lineage、methodology version coverage、条件付きPIT
  look-through、K4 expiry、execution-data mode、venue/SOR policy、provenance、superseded
  active-path exclusionをfail-closedで固定する。正本は
  `docs/reports/portfolio-researchability-instrument-inventory-2026-08-08.md` と
  `out/portfolio-researchability-reset-2026-v0/phase2-instrument-inventory-v0/`。

- 個別案件への人間承認がタイムリーに行えない運用制約を受け、次期案を半裁量ではなく
  `policy_authorized_opportunity_router_v0`として設計した。人間はpolicyと
  playbook versionを事前承認し、システムが案件ごとに全gateを自動評価するhuman-on-the-loop
  方式。`WAITING_HUMAN`やpositive per-trade overrideは作らず、欠測・曖昧・期限切れ・
  failureは`NO_TRADE`。初期admitted playbookは0、上限3で、棄却済みLIQIMP/IMOM6M、
  既存event/technicalを自動採用しない。Gatewayは引き続き唯一の最終risk執行者。
  全candidateと候補ゼロを保存し、損益と判断品質を分けたappend-only reviewを行う。
  取引時には規則を変えず、月次監査と四半期の別認可で将来versionだけを見直す。
  Phase 2前にcandidate intake/population hash、static mechanism対dynamic fit、複数playbook
  assignment、capacity resolution、counterfactual class、outcome-blind process review、
  position version/exit lifecycleを固定する。candidate非依存のPhase 2 design v0 draftは
  `docs/features/opportunity-router-phase2-admission-forward-evidence-design-v0.md`と
  `research/opportunity-router/playbook-admission-forward-evidence-design-v0.json`へ作成済みだが、
  freezeは未承認。未固定・競合・tieはfail-closed。
  人間は通常営業日・市場中の操作を要せず、事前認可、blind process audit、月次監査、
  四半期改版、リスク削減停止だけを担う。運用正本は
  `docs/runbook/opportunity-router-human-oversight-v0.md`と
  `research/opportunity-router/human-oversight-operating-contract-v0.json`。いずれもplan-only。
  明示認可済みPhase 1として`services/opportunity-router`に純関数、SHA-256 binding、
  決定論的capacity、冪等なhash-chain local JSONL ledgerをlibrary-onlyで実装した。
  CLI/runtime/外部I/Oはなく、既存9 serviceのhealth-check対象にも追加しない。playbook選定、
  outcome計算、forward収集、shadow、paper/liveは未承認で、現行Kill Switchとは分離する。
  新candidate IDと選定は9/30判定後かつ別認可まで禁止し、design freeze、candidate search、
  runtime、収集を別々の認可点にする。
  正本は `docs/features/policy-authorized-opportunity-router-v0.md` と
  `research/opportunity-router/policy-authorized-opportunity-router-v0-charter.json`。

- 9/30 project kill switch判定を自動化した。
  `scripts/report-project-kill-switch-readiness.py`は、fixed20で期限内決済可能な
  clean cohortをsignal date 2026-07-21〜08-27の27営業日に固定し、source/outcome
  hash chain、artifact binding、欠測日、feature欠損、未確定outcomeをfail-closedで
  検査してから2M portfolioのPF/DDを再現する。期限前は`PENDING_UNTIL_DEADLINE`、
  期限時にcoverage/outcome/PF>1.2/DD<200,000のいずれか未達なら
  `KILL_SWITCH_TRIGGERED`。経済条件通過でもactivationはfalse。forward runnerは
  ledger追記後にfinalizerとreadiness reportを自動実行する。signal date
  2026-08-07までに期待14/27日を全て記録し、欠測0、完全候補0、不完全候補0、
  対象eventは合計2,209件。引き続き
  `NOT_DEMONSTRATED / PENDING_UNTIL_DEADLINE`、activationはfalse。正本は
  `docs/features/project-kill-switch-readiness.md`。

- 9/30までの手数不足に対し、7/21以降をuntouched prospective OOSとして、既存全
  92,185観測をcontaminated development扱いにした高頻度event fixed2 screenを
  事前登録して3案だけ実行した。結果は`NO_CANDIDATE`。広い2案はhistorical
  Jul-Sep median opened 30を満たすが、最良でもPF 1.109、DD 26.4%、stress PF
  0.937。quality tier 0-2はPF 1.353まで改善したがDD 13.2%、stress PF 1.154、
  Jul-Sep median opened 9で頻度不足。4案目やgate緩和は行わず、causal/paper/live
  routeも追加しない。正本は
  `docs/reports/event-prospective-high-frequency-development-screen-result-2026-07-18.md`。

- prospective event candidate用のappend-only outcome finalizerを追加した。
  `scripts/finalize-event-forward-outcomes.py`はsource ledger/artifact/hashを
  再検証し、official next-openから20営業日closeまたは-10% stopまでを、凍結済み
  0.298%往復cost込みで別hash-chain ledgerへ冪等追記する。欠損済みbarは
  fail-closed、未成熟はpending、候補0ならOHLCVを読まずno-op。これは
  `registered_backtest_shadow`であり、`paper_execution_observed=false`、
  `execution_evidence_eligible=false`。paper/live evidenceや2M portfolio集計ではない。
  2026-08-07 signal dateまでのledger 16行は全て候補0のため、finalized 0で
  outcome fileは未作成。

- 2026-07-17 signal dateのcausal forward evidenceを2026-07-18 JSTに記録した。
  financial summary 10件から候補0・除外0・publish 0の完全artifactとなり、ledger
  2行目をhash `5998bac0...c88c`で追記、chain検証済み。初回の23:03 JST fetchは
  next-calendar-day coverage開始より57分早くfail-closedしたため監査保存した。
  runnerが`--resume`で早すぎるcomplete fetchを再利用する問題を修正し、explicit
  signal dateのfinancial summaryは常にfresh responseをappendする。さらに翌暦日
  00:00 JST以上・次TSE営業日09:00 JST未満をpreflightで強制し、窓外実行はAPIや
  artifact write前に拒否する。7/13の遡及artifactは全49件`late_data_receipt`で
  ledgerへ入れていない。正本は
  `docs/reports/event-forward-evidence-2026-07-17.md`。

- 2026-07-18 に train-only screen から新規候補
  `event_multi_event_fundamental_technical_fixed5_v0_research` を1件だけ
  事前登録し、validationを1回実行した。主評価2Mは純益`+141,337円`、PF
  `2.037`、最大DD `41,059円`、matched-random percentile `0.797`で数値条件を
  通過したが、opened `29`が事前登録minimum `30`に1件不足したため判定は
  `INCONCLUSIVE`。minimumを緩めず、別exitをvalidationで見ず、現データcycleでは
  freezeする。paper/live/locked-OOSは引き続き禁止。正本は
  `docs/reports/event-multi-event-fixed5-validation-result-2026-07-18.md`。

- 日々の運用を「day戦略のpaper収益検証」から「tick/板/featureの継続収集 +
  2M event/swing shadow forward」へ変更し、ユーザー承認済みの固定ルールを
  `docs/runbook/data-capture-shadow-forward-operations.md` に記録した。既存day BUY、
  event target publish、liveは無効のまま。Universe Scanner、Feeder、Feature Engine、
  J-Quants export、causal detector、forward ledgerは継続する。

- ユーザー承認により、cluster v1の主評価資本を1Mから2M JPYへ変更する方針を
  採用した。1Mは小資本感応度、5Mはcapacity診断として維持する。結果確認後の
  gate変更なので、ADR-0006に理由を記録し、2026-07-19 JSTまでは1週間の
  cooling-offとする。この変更だけではpaper/liveを有効化しない。

- 2026-07-12 に、明示承認された一回限りの cluster v1 locked-OOS
  matched-random corrective inspectionを実施した。selected/random双方を凍結済み
  10% stopへ揃えた結果、percentileは1M `0.713`、2M `0.837`、5M `0.937`。
  主判定の1Mがp75未達のためpaper activationは引き続きBLOCKED。追加のlocked-OOS
  再実行・retune・target publicationは許可されていない。正本は
  `docs/reports/event-cluster-matched-random-corrective-inspection-2026-07-12.md`。
- `frozen_opening_close_v1` execution profileをtransport stressと別identityで実装。
  entry bookは09:00:00〜09:00:59 JSTだけを許可し、09:01以降の代用を拒否する。
  共有dispatch contractとSupabase RPC allowlistも2つの明示profileだけへ拡張した
  （contracts migration 023 / infra migration 024）。ローカル実Pub/Sub + Supabase
  E2Eは両profileで成功し、paper-only、重複抑止、fill基準10% stop、20営業日目
  15:30 exit、live topic流出ゼロを確認した。target DBへmigration 024は未適用で、
  managed publisherの禁止も維持している。
- 2026-07以降のprospective evidence用にhash-chain ledgerを追加した。
  `scripts/record-event-forward-evidence.py`はcausal schema-v3 artifactだけを受付し、
  改変・重複・逆順・legacy artifactを拒否する。
  2026-07-10はfinancial summary 93件、OHLCV 4,196件からcausal artifactを生成し、
  完全な候補0件を`no_candidate_complete_artifact`として初回記録した。正本は
  `docs/reports/event-forward-evidence-2026-07-10.md`。
- 日次timerは`op run ... uv run python scripts/run-event-forward-evidence.py
  --signal-date YYYY-MM-DD`で、取得→detector→ledger追記→outcome finalizer→
  readiness reportを一括実行する。明示したsignal dateの市場・開示終了後、
  翌暦日00:00 JST以上かつ次TSE営業日09:00 JST未満のcausal window以外は
  preflightがAPI取得前に拒否する。直近の成功対象は2026-08-07 signal date。

- 全 9 サービス + Dashboard は実装済み。
- 現在の最優先は event candidate の因果性修正。運用候補と研究用
  forward label を分離し、J-Quants export の受信時刻 provenance と
  disclosure-time の feature vintage を分離して dry-run artifact を再現する。
- event の relative-stop execution contract は実装済み。
  `StrategySignal` / `UnifiedTradeSignal` / `OrderRequest` は相対 stop と
  holding metadata を運び、Gateway は live BUY の相対 stop を拒否し、OMS Paper
  は新規 BUY の実約定値から絶対 stop を固定する。14:50 day closeout は swing
  position を対象外にした。
- event の固定保有退出には、`scheduled_exit_time` を追加した。event 専用の
  `15:30 JST` は `StrategySignal` → `UnifiedTradeSignal` → `OrderRequest` →
  OMS Paper position/RPC まで伝播し、同日15:30までは保有、以後に決済する。
  時刻未指定の既存 position は従来どおり、予定日の開始時から決済対象となる。
  対応する migration/RPC source は `contracts/sql/022_positions_scheduled_exit_time.sql`
  （infra migration 023）。2026-07-11 に target DB へ migration 018〜023 を
  適用し、`health-check.py --check supabase` は必須列と4 RPCを含む `OK 20` を確認した。
  main のproduction deployと `event-paper-raw-books` のbook-filter subscription作成も
  完了した。2026-07-13 JST 向けの Universe Scanner は scanner gate 通過30銘柄を
  watchlist と OMS Live の許可リストへ同期済みで、`--kabu-offline` の pre-open check は
  `OK 130 / WARN 1 / NG 0` を確認した（WARN は停止中の kabu station による HTTP 401）。
  `TRADE_MODE=paper` と `OMS_LIVE_DRY_RUN=true` は維持している。event publisher の
  target activation は禁止のままである。
- Feeder `received_at`、専用 `event-paper-raw-books`、PAPER_ONLY の
  Gateway/Order/OMS 防御、strategy/candidate pairing 分離、決定的 ID、OMS の
  wall-clock stale/future 判定も実装済み。
- detector 内蔵の event `--publish-paper` は引き続き fail closed。独立した
  paper-only one-shot publisher、single-attempt CAS journal、Aggregator/Gateway の
  event 専用 durable dispatch journal、confirmed/ambiguous receipt、Pub/Sub +
  Supabase 実 E2E はローカル実装・検証済み。下流は confirmed の再送を抑止し、
  prepared journal の再開時も現在の入力payload/hashを照合し、不一致なら publish/ack
  せず fail closed とする。外部 publish が曖昧なら自動再送しない。ただし現
  publisher は `opening_transport_stress_v1` で、凍結済み next-open / 20日目
  close を再現せず `comparable_to_registered_backtest=false`。そのため trades/PnL
  は v1 evidence に数えず、target 実行は禁止。加えて将来の実行には
  `contracts/sql/018_oms_paper_apply_fill_rpc.sql`（infra migration 019）と
  `contracts/sql/019_event_paper_claim_cas_rpc.sql`（infra migration 020）、
  `contracts/sql/020_event_paper_stage_dispatch_journal.sql`（infra migration 021）、
  `contracts/sql/021_oms_paper_position_generation_lineage.sql`（infra migration 022）、
  `contracts/sql/022_positions_scheduled_exit_time.sql`（infra migration 023）は
  targetへ適用済みで、必須列と4 RPC healthも確認済み。残るpublish前提は、単一
  coordinator の運用確認とmatched-random evidenceである。
- detector は feature cutoff から必須OHLCV sessionを厳密に決める（15:30 JST
  以降は signal-date、それ以前は直前TSE営業日）。当該行が欠けても古いbarを
  代用せず、凍結済み occurrence を `feature_data_complete=false` として記録する。
  artifact は報告可能だが、pre-open/watchlist/publisher は実行を拒否する。これにより
  データ欠損を理由に research cohort を変更しない。
- ローカル transport stress は loopback Pub/Sub emulator + `--no-seek`、
  loopback Supabase、`trade-ai-dev` / `local-dev` project の組合せだけ許可する。
  remote emulator、cloud Supabase、production project は client 構築前に拒否し、
  event 用 Supabase client と emulator gRPC channel は ambient proxy を継承しない。
- 2026-07-09 / 2026-07-10 の legacy detector による候補 0 件は、T+1 OHLCV
  依存による構造的 false zero の可能性があるため unreliable / inconclusive。
  候補不在の証拠にも、実在した証拠にも使わない。
- day `relative_momentum` の損益は swing 検証の代替にしない。
- earnings の AI ラベル train 46,757 件は完了したが、固定2日/5日とも
  preregistered gate は FAIL だった（PF 改善 +0.051 / +0.030）。この AI selector は
  freeze とし、validation / locked OOS は実行しない。記録は
  `docs/reports/event-ai-earnings-train-freeze-2026-07-11.md` を正とする。
- 2026-07-06 JST paper observation の前日準備は完了。
  `production-preopen-check.py --expected-trade-mode paper --target-date 2026-07-06 --kabu-offline`
  で `OK 127 / WARN 2 / NG 0`。WARN は kabu station / Windows proxy 停止前提の
  feeder/kabu だけ。明朝は kabu station 起動後に `--kabu-offline` なし、
  可能なら `--refresh-kabu-token` 付きで再確認する。
- `/dev/shm/roboinvest/gcp-pubsub-sa.json` が root-owned directory だったため、
  現在の compose は `/tmp/roboinvest-gcp-pubsub-sa.json` を
  `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH` として mount している。
  詳細は [2026-07-05 Paper Ready Handoff](handoff/2026-07-05-paper-ready.md)。
- production compose / Cloud Supabase / managed Pub/Sub / Vercel Dashboard は一通り稼働済み。
- 2026-06-23 の paper 結果と直近日次成績を受け、既存 intraday
  RULE/AI judge stack は live 候補から降格。小手先の gate 追加ではなく
  strategy reset としてゼロから作り直す判断。
- 2026-06-23 paper は BUY 21 / SELL 21、closed 21、勝ち/負け/同値
  `4/16/1`、実現 paper PnL `-12,500円`。source 別は RULE `-11,500円`,
  CONSENSUS `-1,000円`。
- 直近 paper 観測 4 日分の FIFO PnL は `2026-06-16 -12,200円`,
  `2026-06-19 -10,100円`, `2026-06-22 -6,500円`,
  `2026-06-23 -12,500円`、合計 `-41,300円`。
- Live trading は 2026-05-21 から 2026-05-29 まで運用実績あり。
- 5月 live 成績は合計 `+46,766円`、ただし 2026-05-29 は `-45,540円` の大きな負け。
- Paper trading は 2026-05-19 から 2026-05-21 まで確認済みで、合計 `+68,100円`。
- 2026-05-31 に production 反映済み: `AI_MAX_OUTPUT_TOKENS=2048`、live/day 新規 BUY 開始 `09:15 JST`、Aggregator source 別 threshold (`RULE_ONLY=0.5`, `AI_ONLY=0.5`, `CONSENSUS=0.3`)。
- `scripts/production-preopen-check.py` を追加済み。kabu station 起動後、`--kabu-offline` なしで `OK 60 / WARN 0 / NG 0` を確認済み。
- 2026-06-17 paper hardening で Gateway paper guards、Strategy Rule entry filter、Universe Scanner risk penalty、OMS Paper raw book subscription を production 反映済み。
- 2026-06-17 追加実装で OMS Paper day stop monitor を有効化し、OMS Live stop monitor は raw book subscription まで配線したうえで `OMS_LIVE_STOP_MONITOR_ENABLED=false` のまま安全側に保持。
- PR #93 `[paper observation] Harden execution safety gates` と PR #94 `Add paper observation report script` は main へ merge 済み。
- 最新 production pre-open check は Pub/Sub smoke あり、`--kabu-offline --expected-trade-mode paper` で `OK 93 / WARN 0 / NG 0`。

長い時系列ログ:

- [docs/handoff/2026-05-operations-log.md](handoff/2026-05-operations-log.md)
- [docs/handoff/2026-06-operations-log.md](handoff/2026-06-operations-log.md)
- [docs/handoff/2026-06-17-paper-hardening-handoff.md](handoff/2026-06-17-paper-hardening-handoff.md)
- [docs/handoff/2026-06-23-strategy-reset.md](handoff/2026-06-23-strategy-reset.md)
- [docs/handoff/2026-06-24-relative-momentum-failure.md](handoff/2026-06-24-relative-momentum-failure.md)
- [docs/handoff/2026-07-05-paper-ready.md](handoff/2026-07-05-paper-ready.md)
- [docs/adr/0003-strategy-layer-rebuild.md](adr/0003-strategy-layer-rebuild.md)

5月成績レビュー:

- [docs/handoff/2026-05-performance-review.md](handoff/2026-05-performance-review.md)

## 3. Read First

作業開始時は最低限これを読む:

1. [CLAUDE.md](../CLAUDE.md) - アーキテクチャ、Pub/Sub、Supabase、リスクルール、規約
2. 対象サービスの `services/<name>/CLAUDE.md`
3. [contracts/](../contracts/) - Pydantic / SQL / TypeScript の Single Source of Truth
4. [docs/handoff/2026-05-operations-log.md](handoff/2026-05-operations-log.md) - 直近の運用経緯が必要な場合だけ
5. `git status --short --branch` と直近履歴

## 4. Active Follow-ups

優先度が高い順:

0. **2026-07-11 event paper close-session profile と AI selector freeze**
   - `scheduled_exit_time=15:30 JST` を event paper 固定保有の execution contract に
     追加済み。ローカル loopback Supabase の migration/RPC と event pipeline E2E で
     確認し、`make lint-all` / `make test-all` も成功した。
   - target DB migration 018〜023、必須列/4 RPC health（OK 20）、
     `event-paper-raw-books` subscriptionのbook filter、mainのproduction deployは
     2026-07-11に確認済み。2026-07-13向けのwatchlist 30件と OMS Live許可リストも
     scanner gate通過銘柄で同期し、offline pre-open check は `OK 130 / WARN 1 / NG 0`
     だった。`opening_transport_stress_v1` は引き続き
     `comparable_to_registered_backtest=false` であり、target 実行・paper/live evidence
     への算入はしない。
   - 次に進める条件は、20営業日目15:30 close と -10% stop を揃えた matched-random
     evidenceと単一 coordinator の確認である。これらが揃うまで publisher のtarget
     activationは禁止する。
   - earnings train AI labeling は 46,757/46,757 で終了したが gate FAIL。AI selector は
     freeze し、validation と locked OOS を回さない。詳細は
     `docs/reports/event-ai-earnings-train-freeze-2026-07-11.md`。

0. **2026-07-10 event candidate causality audit**
   - 外部レビューで、event detector が T+1 OHLCV と実際の寄付値を候補生成、
     `StrategySignal.price`、絶対 stop に使用していたことを確認。
   - `fix/event-candidate-causality` では運用候補特徴量と研究用 forward label を分離し、
     entry date を未来 OHLCV ではなく東証営業日カレンダーから解決する。
   - 候補 artifact は entry price / absolute stop を持たない。研究と同じ
     disclosure-time `data_available_at/feature_cutoff_at` を保持し、翌朝の実受信は
     `source_received_at` に分離する。signal date 終了後から次営業日 09:00 JST
     前までの complete snapshot だけを運用 artifact として認める。
   - legacy detector の `candidate_count=0` は構造的な偽陰性の可能性が
     あるため unreliable / inconclusive。候補不在と断定しない。
   - relative stop は `0 < stop_loss_pct < 1`、absolute stop と排他にして
     Strategy/Unified/Order を通過する。Gateway は holding/max-hold/scheduled
     exit を保持し、live BUY の relative stop を
     `relative_stop_live_unsupported` で reject する。OMS Paper は新規 BUY の
     実約定値から absolute stop を固定する。
   - 14:50 day closeout は `holding_type=day` だけを対象とし、swing position を
     保持する。
   - fresh quote provenance、専用 subscription、PAPER_ONLY 強制、deterministic
     ID / strategy isolation、wall-clock stale/future 判定は実装済み。
     `candidate_id` は戦略 ID ではなく cluster/observation occurrence を使う。
   - OMS Paper の全 fill path は `oms_paper_apply_fill` RPC へ統一済み。
     order/signal/trade ID 冪等性、symbol lock、rollback、partial exit cache 維持、
     `opened_at` の ABA 照合、初回 BUY `trade_id` を固定する
     `position_generation_id` lineage、closeout の fresh-book bounded retry、Python と
     SQL の1円平均単価丸めを実装。ローカル実 RPC の並行 BUY・時刻/約定値が
     変わる redelivery・partial/full SELL・FK rollback・ABA reject・並行 trailing
     stop の単調増加・anon deny を確認済み。
   - 将来の独立 hardening として、stale exit/stop の expected 値にも
     `position_generation_id` を伝播する余地がある。現行の `opened_at` ABA guard を
     置換するには RPC interface の migration が必要なため、このレビューでは混在させない。
   - detector 内蔵の event `--publish-paper` は fail closed のまま。独立 publisher
     と実 E2E は `opening_transport_stress_v1` として完成したが、next-open / 20日目
     close の整合と stop 条件を揃えた matched-random evidence がない。target DB healthと
     managed subscriptionのbook filterは確認済みだが、単一 coordinator 所有も含め、
     すべて解消するまでactivationしない。
   - kill switch、PER threshold、20日 exit、-10% stop の戦略値は変更しない。

0. **2026-06-23 strategy reset: 既存 intraday strategy は live 候補から外す**
   - 2026-06-24 に方針転換を ADR 化:
     [ADR-0003 Strategy Layer Rebuild](adr/0003-strategy-layer-rebuild.md)。
     現行 intraday BUY stack は閾値調整ではなく戦略層の作り直し対象。
     次候補は小幅 intraday reversal/momentum ではなく、より値幅のある
     daily OHLCV / swing 系から検証する。
   - 2026-06-23 paper は BUY 21 / SELL 21、closed 21、勝ち/負け/同値
     `4/16/1`、realized paper PnL `-12,500円`。
   - source 別では RULE `-11,500円`、CONSENSUS `-1,000円`。
   - 直近 paper 観測 4 日分は合計 `-41,300円`。これは日次の偶然ではなく、
     既存 RULE entry / judge stack が構造的に負けている可能性が高い。
   - 既存 strategy に小さな gate を追加して live に近づける作業は停止。
   - 次作業は [2026-06-23 Strategy Reset Decision](handoff/2026-06-23-strategy-reset.md)
     を起点に、opening range breakout / VWAP / relative momentum などから
     新しい仮説を明文化して設計する。
   - 2026-06-23 に ORB plugin と end-to-end replay 経路を追加して検証したが、
     現行条件では 2026-06-18 / 2026-06-22 とも net PnL / PF が不合格。
     ORB は primary 候補から外し、次は VWAP continuation または relative momentum を優先する。
   - VWAP continuation 初期診断は execution filter 後に 15/30m forward return が悪化。
     Relative momentum は watchlist 内 peer percentile 代理で 30m forward return が比較的良好。
     `return_from_open_bps` / `intraday_peer_percentile` / `intraday_high_price` と
     `relative_momentum` plugin は追加済み。次の feature archive で end-to-end replay する。
   - 既存 archive を enrichment して strict relative momentum を replay したところ、
     `150 bps` 条件は 2026-06-18 / 2026-06-22 では net positive だったが、
     2026-06-23 当日 archive で net `-57,611.636` となり失格。
   - `300 bps` 条件は 2026-06-18 net `+19,676.9899758`,
     2026-06-22 net `+5,739.25714235`, 2026-06-23 net `0`。
     ただし 3 日合計 closed trade は 5 件、6/22 / 6/23 no-fill が高いため
     live-ready ではない。次は paper observation candidate として扱う。
   - 2026-06-24 paper / archive OOS で relative momentum は失格。
     strict `300 bps` は 6/24 feature 診断で candidates `5`、avg 30m return
     `-95.399 bps`、positive 30m `0.0%`。実注文 replay も net
     `-5,713.332`、PF `0`、gate `FAIL`。
   - 2026-07-08 に `STRATEGIES_ENABLED=` の no-op のまま paper observation を走らせ、
     シグナル検証にならない運用ミスを確認。paper 検証日は
     `STRATEGIES_ENABLED=relative_momentum` を前提にし、preopen check も no-op を
     NG として扱う。
   - 2026-06-24 に `vwap_reclaim` と `oversold_reclaim` を一時的な
     non-default plugin として検証。`oversold_reclaim` は feature-level
     forward return は momentum 系より良いが、OMS Paper + コスト後は不合格。
     cleanup 後、失格 plugin code は production strategy registry から削除済み。
   - Gateway backtest に `--max-notional-per-order-pct` を追加し、tight stop による
     過大ロットを抑制できるようにした。
   - `oversold_reclaim` の 30分固定退出診断は target/stop より改善したが、
     4日合計 net `-802.346` で不合格。主因は no-fill 率の高さ。
   - 2026-06-24 に `liquid_trend_pullback` 診断と feature-rule grid を追加。
     6752 型の liquid trend pullback は 6/24 で 3186/8233 など悪い候補を拾い、
     6752 の実注文も 9:16 高値追いから 9:22 stop で損切りしていた。
     Scanner は主因ではなく、場中 entry/exit/execution の edge が薄い。
   - `rsi_vwap_recovery` を一時的な non-default plugin として検証。
     feature-level 60分 forward return は一見 `+13.64 bps` だったが、
     OMS Paper + コスト後は 4日合計 net `-3,660.758` で不合格。
     `+1 tick` BUY crossing も 6/18 / 6/24 で悪化した。
   - random-entry baseline を追加。Gateway 通過可能な同一 execution/exit 条件で
     seed 1-3 を回した結果、4日合計 net は `-7,515.128`,
     `-6,357.903`, `-6,458.052`。`rsi_vwap_recovery` は random より
     負け幅が小さいが、絶対値でマイナスのため採用不可。
   - RSI oversold + MACD golden cross 診断を追加。production features には
     MACD は未追加で、archive から1分足 MACD を計算する研究用。
     target/stop は4日合計 `-2,084.363` で不合格。15分固定 exit は
     `+757.574` と初のプラスだが、closed 6件・no-fill 高率で、
     `+1 tick` BUY crossing では `-1,110.322` に悪化。研究継続可だが
     paper/live 有効化不可。
   - 複合指標 grid を追加し、RSI / MACD / VWAP / Bollinger /
     return-from-open / peer percentile を特徴量レベルで探索。追加 feature day
     (6/16, 6/17, 6/19) を含めると上位は `RSI<=30` + `15m lookback`
     + `MACD histogram positive/rising` + `near/above VWAP`。
     ただし OMS replay では、15分固定 exit passive BUY が5日合計
     `-896.602`、BUY `+1 tick` が `-3,919.316` で不合格。
     約定率を上げても PnL が悪化するため、主因は scanner ではなく
     intraday entry/exit signal の edge 不足。
   - `scripts/check-replay-report-set.py` を追加し、複数日合計 PnL /
     closed trade 数 / 勝ち日数 / weighted no-fill / stress replay を
     明示 gate 化。上記 RSI+MACD histogram 候補は base net `-896.602`,
     closed `7 < 20`, positive days `2/5 < 3`, weighted no-fill `0.65 > 0.30`,
     stress net `-3919.316` で `FAIL`。単日 gate CLI も
     `--max-no-fill-rate` と tick spread 閾値を渡せるよう修正済み。
   - 明示的な新戦略レビューなしに `relative_momentum` / 旧 RULE BUY を
     paper/live route に戻さない。
   - 明示的な再評価なしに、現行 RULE BUY / judge BUY を live entry 根拠にしない。

0. **2026-06-17 paper hardening / stop monitor は production 反映済み**
   - OMS Paper は `oms-paper-raw-books` を読み、day position の stop/target/trailing を `PAPER_DAY_STOP_MONITOR_ENABLED=true` で評価する。
   - OMS Live は `oms-live-raw-books` subscription と raw book cache を追加済み。ただし live 自動 stop SELL は `OMS_LIVE_STOP_MONITOR_ENABLED=false` のまま。
   - Managed Pub/Sub は `oms-paper-raw-books` / `oms-live-raw-books` とも `attributes.kind = "book"` filter を確認済み。
   - production では `oms-live` / `oms-paper` を rebuild/recreate 済み。
   - latest pre-open check: Pub/Sub smoke ありで `OK 93 / WARN 0 / NG 0`。
   - 次は paper 中に `day_stop_exit` / `day_stop_trail`、`trades_paper.unified_signal_id is null` の SELL、Gateway reject reason、live stop event が出ていないことを観測する。
   - 観測 summary は `scripts/report-paper-observation.py` で取得できる。
   - Live stop monitor を有効化する前に、paper 観測と HITL での dry-run/ログ確認を挟む。

0. **Cloud Logging は main マージ済み / production 段階反映済み**
   - PR #71 `Add structured Cloud Logging pipeline` を 2026-05-31 に main へマージ済み (`1244060`)。
   - Python サービスログは 1 行 JSON 化済み。`JSON_LOGS=false` で旧テキスト形式に戻せる。
   - OpenTelemetry Collector は `observability` profile で production 起動済み。health check `127.0.0.1:13133` は OK。
   - production では Collector 単体起動後、非発注系 (`feature-engine`, `strategy-rule`, `strategy-ai`, `aggregator`) → `feeder` → `gateway` / `oms-paper` / `oms-live` の順に rebuild/restart 済み。
   - Collector は `service` と `event` を持つアプリ JSON だけを Cloud Logging `logName:"roboinvest"` へ送る。plain text / 他基盤ログは drop する。
   - 検証済み: `make lint-all`、`make test-all` (`937 passed, 21 skipped`; dashboard `47 passed`)、Collector parse fixture OK、SA `entries:write` probe HTTP 200、production pre-open check `OK 60 / WARN 0 / NG 0`。
   - 注意: このホストに `gcloud` がないため CLI での Cloud Logging read は未実施。SA は write 権限のみで read probe は 403。
   - Cloud Logging Console へのログ到達はユーザーが確認済み。保存クエリ名と rollback 手順は [docs/runbook/cloud-logging.md](runbook/cloud-logging.md) を参照。
   - 次の候補: 2026-06-01 の market data で `jsonPayload.event="signal_rejected"` / `order_published` を Cloud Logging Console から確認する。

0.1. **Cloud Monitoring を一次監視基盤にする方針**
   - サービスメトリクス、トレードメトリクス、インフラメトリクスを Google Cloud Monitoring に集約する方針。
   - Cloud Logging は詳細調査、Cloud Monitoring は数値化された状態・ダッシュボード・アラートに使う。
   - Vercel Dashboard は Supabase Realtime ベースの取引オペレーション画面とし、監視基盤そのものにはしない。
   - PnL / 建玉数 / 注文数などは、Supabase を正として `metrics-exporter` が定期集計し、Cloud Monitoring custom metrics に送る案が有力。
   - 詳細メモは [docs/runbook/cloud-monitoring.md](runbook/cloud-monitoring.md) を参照。

0.2. **次セッションで 2026-06-01 のログ設計を振り返る**
   - 2026-06-01 live は `+4,470円`、closeout 後 `positions(live)=0` で終了。
   - Cloud Logging 上で `signal_rejected` / `order_published` / OMS Live fill / closeout が調査しやすいか確認する。
   - OMS Live の `live order filled` や closeout ログは現状 `event="log"` が多い。必要なら `order_filled` / `closeout_completed` / `broker_order_failed` のような構造化 event 名に分ける。
   - `14:16 JST` に kabu `Code 21: 可能額が不足しております` が 1 件発生。エラー継続性は問題なかったが、Gateway の資金見積もりと実買付余力のズレ、アラート対象化を検討する。
   - 詳細は [docs/handoff/2026-06-operations-log.md](handoff/2026-06-operations-log.md) を参照。

1. **翌営業日寄り前に one-command pre-open check を再実行する**
   - kabu station / Windows proxy 起動後に `op run --env-file infra/env.production -- uv run python scripts/production-preopen-check.py --timeout 30 --refresh-kabu-token` を実行する。
   - 2026-05-31 は Cloud Logging 反映後の全 service rebuild/restart 後に `OK 60 / WARN 0 / NG 0` を確認済み。
   - 2026-06-01 はスケジュール発火後に watchlist / daily_ohlcv を確認し、`feeder` が `Up`、`feeder kabu` が `token 200` または `unregister/all 200`、`positions(live)` が空であることを再確認する。

2. **AI 戦略の復旧を production で観測する**
   - PR #66 で `AI_MAX_OUTPUT_TOKENS` のデフォルトを `2048` へ変更済み。
   - production `strategy-ai` は再起動済みでコンテナ内 env も `2048` を確認済み。
   - 翌営業日の実 market data で JSON 生成、parser、signal publish、`strategy_logs` / `aggregator_logs` 反映を確認する。

3. **寄り付き直後の live BUY guard を観測する**
   - 5/29 の損失は 09:00-09:05 の急変動エントリーが大きい。
   - PR #66 で `gateway` の live/day 新規 BUY は 09:15 JST より前に `opening_live_buy` で reject するよう変更済み。
   - production `gateway` は `LIVE_DAY_NEW_BUY_START_TIME=09:15` で再起動済み。
   - 数営業日、reject reason 分布、09:00-09:15 の missed profit / avoided loss を観測する。

4. **Aggregator の source 別 confidence threshold を観測する**
   - PR #67 で RULE / AI 単独シグナルは `0.5`、RULE+AI consensus は `0.3` を下限に変更済み。
   - production `aggregator` は `MIN_CONFIDENCE_RULE_ONLY=0.45`、`MIN_CONFIDENCE_AI_ONLY=0.5`、`MIN_CONFIDENCE_CONSENSUS=0.3` で再起動済み。
   - 弱い RULE 単独通過を減らしつつ、AI 復旧後の consensus は落としすぎない狙い。

5. **保有時間制限を検討する**
   - 15分以内の決済が利益の大半を稼ぎ、60分超は勝率が落ちている。
   - 45分前後の time-based closeout を候補にする。

6. **carry / closeout の堅牢化を続ける**
   - closeout 後に live position が残る場合は `CRITICAL` ログまで実装済み。
   - 通知系、Dashboard 明示表示、翌営業日 pre-open 手順の強化は継続課題。

7. **損切り exit / Universe Scanner 改善は別実験に分ける**
   - price-based stop-loss exit は重要だが、live 自動 SELL に直結するため設計と paper / dry-run を挟む。
   - Universe Scanner の `min_volatility` / momentum penalty / `top_n` 可変化は、Gateway / Aggregator guard の効果観測後に扱う。

## 5. Core Rules

- `contracts/` を Single Source of Truth とする。スキーマ変更はここから始める。
- サービス間の直接通信は禁止。連携は Pub/Sub 経由にする。
- Gateway がリスクルールを単独で執行する。他サービスへ判断を分散しない。
- OMS Live は本番資金に直結するため、変更は最小限にし、先に OMS Paper / unit test で検証する。
- production compose は `op run --env-file infra/env.production -- docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml ...` 経由で操作する。
- `KABU_DEFAULT_EXCHANGE=9` が本番前提。検証 18081 は `sendorder` を黙殺するため、実発注 e2e は本番 28080 のみ。
- Feeder と OMS Live は `KABU_TOKEN_CACHE_FILE` を共有する。複数プロセスや probe による `/token` 再発行で token 競合が起きる。

## 6. Common Commands

```bash
make lint-all
make test-all
bash scripts/start-paper-trading.sh
./scripts/gen-supabase-types.sh
uv run python scripts/health-check.py
```

Production 系の確認例:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- uv run python scripts/production-preopen-check.py --timeout 30 --refresh-kabu-token
op run --env-file infra/env.production -- uv run python scripts/health-check.py --check supabase --timeout 30
op run --env-file infra/env.production -- docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml ps
```

Production deploy:

```bash
bash scripts/deploy-production.sh --apply --kabu-offline
```

詳細手順は [docs/runbook/production-deploy.md](runbook/production-deploy.md) を参照。

## 7. Test And Lint Conventions

- `git push` 前は必ずトップレベル [CLAUDE.md](../CLAUDE.md) の **Push 前ゲート** を実行する。最低限 `make lint-all` と、変更したサービスの unit test を push 前に通す。
- formatter 適用や追加コミット後も、push する前に再度 `make lint-all` を通す。CI で初めて `ruff format --check .` の漏れを見つけない。
- この repo では `git config core.hooksPath .githooks` を設定し、pre-push hook で `make pre-push` を走らせる。hook を無効化・回避した場合は、push 前に手動で `make pre-push` を実行する。
- Python は `uv` を使う。`pip` / `poetry` 直叩きは避ける。
- 新サービスで `tests/conftest.py` を作らない。fixture は `src/<service>/_testing.py` に置く。
- `tests/__init__.py` は作らない。
- テストファイル名は `test_<service>_*` プレフィックスで衝突を避ける。
- Dashboard は Volta 管理の Node/npm を使う。

## 8. Strategy Parameters To Preserve

- `RSI_BUY_THRESHOLD=25`
- `RSI_SELL_THRESHOLD=75`
- `SMA min_gap_ratio=0.005`
- `Bollinger tolerance=0.15`

テストや調査で緩めた場合は戻し忘れに注意する。

## 9. Archiving Rule

このファイルに日次ログを追記し続けないこと。

- 直近の実行結果や調査ログ: `docs/handoff/YYYY-MM-operations-log.md`
- 成績レビューや分析: `docs/handoff/YYYY-MM-*-review.md`
- 手順化できたもの: `docs/runbook/`
- 設計判断: `docs/adr/`
