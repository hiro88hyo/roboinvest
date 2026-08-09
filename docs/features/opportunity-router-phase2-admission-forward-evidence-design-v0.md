# Opportunity Router Phase 2 Admission And Forward Evidence Design V0

作成日: 2026-08-09

Status: `FROZEN_DESIGN_ONLY_NOT_ACTIVATED`

Identity: `opportunity_router_playbook_admission_forward_evidence_design_v0`

## Authority And Purpose

ユーザーの2026-08-09の「続けて」を、candidate非依存の
`PLAYBOOK_ADMISSION_AND_FORWARD_EVIDENCE_DESIGN`開始だけの認可として記録する。認可記録は
`research/opportunity-router/phase2-design-start-authorization.json`である。

ユーザーは2026-08-09に「Phase 2設計v0をfreezeする。candidate選定・runtime実装・収集は
認可しない。」と明示した。freeze認可記録は
`research/opportunity-router/phase2-design-freeze-authorization.json`である。

この文書は、candidateを見る前に探索回数、入力母集団、gate、cost、outcome、監査、十分性、
停止条件を固定した設計正本である。freezeは次を認可しない。

- strategy candidate IDの作成、候補の選定・順位付け、playbook admission
- Phase 1 routerをfixture以外のhistorical/forward inputへ実行すること
- runtime、外部contract、Supabase、Pub/Sub、Dashboard、Gateway、OMSの変更
- historical/forward return、PnL、PF、DD、MAE/MFE、counterfactual outcomeの計算
- prospective collection、shadow、paper、live
- 2026-09-30 Project Kill Switchの条件、期限、証拠cohortの変更

`portfolio_researchability_reset_2026_v0`は9月30日の判定前に新しいstrategy candidateを
作らないと固定している。このため、本設計をfreezeしてもcandidate選定は9月30日判定後かつ
別の明示認可後でなければ開始しない。

## Frozen V0 Search Boundary

最初のrouter cycleは、比較可能性と実行可能性を優先して次へ限定する。

- 日本国内の現物普通株、long-only、100株単元
- SOR前提でauカブコム証券から現物買いできる銘柄
- candidate evidence cutoffはentry日の08:30 JST以前
- decisionは09:00:00以上09:01:00未満の最初の有効な板で行う
- entryは同じ板のbest askをshadow referenceとする
- holding horizonは1〜20 TSE sessionsの範囲でplaybookごとに一つ固定する
- exitは固定session close、事前invalidation、または事前stopのいずれか早いもの
- LLMをcandidate生成、gate、priority、entry判断へ使わない

ETF、投資信託、指数、先物、オプション、信用、空売り、レバレッジ、FX、暗号資産、
intraday round trip、裁量entryはv0 search boundary外とする。既存ETF Phase 2の
`NO_GO_PHASE3_CURRENT_INSTRUMENT_DATA_FOUNDATION`を回避しない。

## Trial Budget Before Candidate Selection

- cycle ID: `opportunity_router_v0_prospective_playbook_cycle`
- maximum proposal slots: 3
- maximum admitted playbooks: 3
- one proposal slot must contain exactly one economic mechanism and one parameter version
- proposal registration後のincomplete、withdraw、fail、freezeもslotを消費する
- outcomeを見た後のparameter、threshold、horizon、direction、universe、cost変更は別proposal
  としてslotを消費し、元cohortを再解釈しない
- 3 slots消費後はcycleを閉じ、名称変更や小変形で追加slotを作らない
- fallbackは先行proposalのoutcomeを見る前に同時登録しなければならない

異なるproposalは経済的な価格反映mechanismが異ならなければならない。feature名、window、
thresholdだけが違う案は別mechanismと数えない。

明示的に禁止するもの:

- `liqimp1m_logdiff_v0_research`、`imom6m_top5_fixed20_v0_research`の復活または変形
- closed cycleで禁止済みのIMOM12M、skip-month、sign reversal、quantile、regime、
  quality/value合成
- 既存event lane、technical strategy、実装済みpluginの自動admission
- 既存validation、locked OOS、prospective outcomeを見てからmechanismを選ぶこと
- 複数parameterを一つのproposal slotで比較すること
- 「局面」を事後returnで命名してcandidate searchへ戻すこと

## Candidate Intake And Population Contract

各playbook proposalはoutcomeを読む前に、次のsource-specific値をすべて固定する。

```yaml
candidate_intake:
  source_id:
  source_version:
  session_definition:
  eligible_instrument_rule:
  detection_rule:
  required_source_ids: []
  evidence_cutoff_rule: "<= 08:30:00 JST on entry date"
  source_received_at_rule:
  deduplication_rule:
  candidate_id_rule:
  zero_candidate_rule:
  missing_or_late_data_rule: "INVALID_SESSION_NO_ENTRY"
```

一つのsession snapshotは次をcanonical JSON化し、SHA-256で拘束する。

- intake ID/version/hash
- TSE session dateとcalendar version
- cutoffまでに適格だった全instrument IDのsort済み集合
- cutoffまでに生成された全candidate IDのsort済み集合
- instrument master、source receipt、feature/input artifactのhash
- `candidate_count`と`zero_candidate`
- snapshot作成時刻とdata completeness

candidate IDは最低限`source_id`、`source_version`、instrument、detection key、
evidence cutoffをcanonical hashへ含める。routerへ渡ったcandidateだけを母集団にせず、
候補ゼロ、gate前の全candidate、invalid sessionを保存する。

次の場合、sessionは`INTAKE_CONTRACT_INVALID`または`POPULATION_HASH_MISMATCH`で終了し、
entryを生成せず、forward performance denominatorへ算入しない。ただしfailure heartbeatは残す。

- eligible instrumentまたはcandidate populationをPITで再現できない
- 必須sourceのreceipt/hash/cutoffが欠ける
- 古いbarや後着dataを代用する
- intakeまたはpopulation hashが不一致
- zero-candidate sessionが記録されていない

## Playbook Static Validity And Dynamic Fit

proposalは次を一つのimmutable playbook contractへ固定する。

- economic mechanism、price-discovery channel、反証可能な主張
- admissible primary factsと推測禁止事項
- candidate source、eligible instrument、evidence cutoff
- static entry conditionとcandidate-specific dynamic fit clauses
- context conditionsとすべての数値threshold
- maximum allowed opening gap（0%超、10%以下）
- stop distance（entryの2%以上10%以下）
- invalidation、fixed exit、maximum hold（1〜20 sessions）
- no-fill、halt、special quote、limit-down、delisting時の扱い
- source、schema、calendar、cost、assignment、capacity version

static validityはmechanismと契約の監査であり、過去成績を条件にしない。dynamic fitは事前登録した
boolean clauseだけを評価し、自由記述でmechanismや例外を追加しない。一項目でも
`UNKNOWN`なら`NO_TRADE`とする。

## Decision Gates

加重平均scoreは使わず、全gateを`PASS / FAIL / UNKNOWN`で保存する。

### Evidence

- required source、receipt time、effective time、artifact hashが完全
- required factがcutoff以前に利用可能
- source内の事実と推測が分離されている
- late correctionや後日barを過去decisionへ混ぜない

### Mechanism

- admitted playbookのstatic contractがhash一致
- required triggerとdynamic fit clauseがすべてPASS
- contradictory triggerまたはinvalidationが一つでも成立すればFAIL

### Context

- playbookに固定したmarket、sector、instrument context thresholdがすべてPASS
- context featureとpopulationのversion/hashが一致
- thresholdの未指定、欠測、複数regime一致はUNKNOWN

### Execution

共通threshold:

- 20-session median daily traded value `>= 200,000,000 JPY`
- first valid opening snapshot age `<= 5 seconds`
- spread `<= 30 bps`かつ`<= 2 ticks`
- best-askから5 levelsの表示売り数量がproposed quantity以上
- proposed order notionalがmedian daily traded valueの`<= 1%`
- 100-share board lotがposition-notional cap内で最低1 lot成立
- observed opening gapがplaybook固有max gap以下
- crossed/locked book、売買停止、特別気配、価格制限で実行不能ならFAIL

後刻の板をopening quoteへ代用しない。09:01までに有効な板がなければexecution missとして
denominatorに残す。

### Portfolio Precheck

shadowの比較資本とrisk geometryを固定する。

- shadow capital: `2,000,000 JPY`
- risk per position: `0.50% of current shadow equity`
- max notional per position: `20% of current shadow equity`
- max concurrent positions: 3
- max new entries per TSE session: 1
- same instrument concurrent limit: 1
- same sector concurrent limit: 1
- quantity: risk quantityとnotional quantityの小さい方を100株単元へ切り下げる

minimum lotが成立しなければ`NO_TRADE`であり、銘柄をpopulationから消さない。paper/liveが
将来認可されてもGatewayが最終risk authorityであり、routerのprecheckは置き換えない。

## Assignment And Capacity Resolution

- matching playbookが0件: `NO_PLAYBOOK_MATCH`
- 2件以上: `AMBIGUOUS_PLAYBOOK`
- playbookごとのproposal slot numberを固定priorityとする
- 同じpriority内は`SHA256(policy_hash, session_date, candidate_id)`昇順
- hash tieはcandidate ID昇順
- capacity外は`CAPACITY_REJECTED`として全gate、priority、counterfactual classを保存
- arrival order、人間の選択、LLM score、過去成績でtieを解かない

priority rule自体が未固定または再現不能なら、該当sessionのeligible candidateをすべて
`NO_TRADE`とする。

## Outcome And Cost Contract

outcomeはpolicy、playbook、candidate、entry、exit、cost versionへ拘束する。

Entry:

- 09:00:00以上09:01:00未満の最初の有効なbest ask
- missing/stale/locked/crossed/special quoteはno-fillであり、後刻価格を代用しない
- official openを後日attachしてimplementation shortfallを分離する

Exit:

- playbookのfixed session close、invalidation、stopのうち最初に成立したもの
- same-barでstopと有利なexitが競合する場合はstopを先に適用
- limit-down等で約定不能なら価格を捏造せず、unfilled状態とdaily MTMを継続
- policy deactivate後も拘束済みexit責務を残す
- open positionを含むdaily mark-to-market equity curveでdrawdownを計算する
- promotion review時に未確定positionまたは未分類outcomeを残さない

Cost:

- base commission: `9.9 bps per side`
- base additional slippage: `5 bps per side`
- stress additional slippage: entry `10 bps`、exit `25 bps`（base slippageを置換する）
- observed best ask/bidが含むspreadを別途二重加算しない
- performanceは税引前、commission/slippage控除後
- baseとstressを同じcohortで両方報告し、都合のよい方だけを採用しない

Counterfactual:

- selector比較は`ENTER_SHADOW`対`POLICY_EVALUABLE`だけを主対象とする
- rejected candidateにも同じentry window、exit、cost、no-fill ruleを適用する
- `ECONOMIC_ONLY_NOT_EXECUTABLE`を実行可能だった利益として数えない
- duplicate、expired、disabled、invalid sessionへ後からreturnを当てない

## Outcome-Blinded Audit Sequence

1. decision、source、gate、hash、逸脱をimmutableに保存する。
2. 自動contract auditを全decisionへ実行する。
3. outcomeを非表示にした人間の`PROCESS_AUDIT`をlockする。
4. lock後だけ`OUTCOME_DIAGNOSTIC`を同じdecisionへ追記する。
5. 元decisionまたはprocess auditを更新せず、訂正は`supersedes`付きの別recordにする。

人間のblind review sample:

- 全`ENTER_SHADOW`
- playbook・月ごとにstable hashで選ぶ最大5件の`POLICY_EVALUABLE NO_TRADE`
- 全hash/cutoff/schema violation

人間が期限内にreviewできなくても売買判断を待たせない。未review cohortは月次reportで
`AUDIT_INCOMPLETE`となり、promotion eligibilityを持たない。

## Sample Sufficiency And Maximum Duration

各playbookが十分とみなされるにはすべて必要とする。

- minimum eligible TSE sessions: 120
- minimum completed calendar months: 6
- minimum finalized economic candidates: 30
- minimum `ENTER_SHADOW` with executable outcomes: 10
- minimum `POLICY_EVALUABLE NO_TRADE` with counterfactual outcomes: 10
- required outcome/no-fill classification completeness: 100%
- required blind process-audit sample completeness: 100%

cycle maximumは252 eligible TSE sessionsとする。maximum到達時に不足するplaybookは
`TERMINATED_INSUFFICIENT_SAMPLE`とし、自動延長、threshold緩和、候補補充をしない。

## Promotion, Freeze, And Termination

このgateを通っても自動paper/liveへ進まず、paper設計を申請できるだけである。

全体gate:

- base cost-adjusted net PnL `> 0`
- base profit factor `> 1.2`
- max drawdown `< 200,000 JPY`（2M capitalの10%）
- stress cost-adjusted net PnL `>= 0`
- stress profit factor `> 1.0`
- `ENTER_SHADOW` expectancyが`POLICY_EVALUABLE NO_TRADE`よりbase cost後で高い
- audited `ENTER_SHADOW`の重大process violationが0
- single instrumentのgross profit寄与 `< 40%`
- single sectorのgross profit寄与 `< 50%`

playbook別gate:

- base net PnL `> 0`
- base profit factor `> 1.0`
- playbook単独max drawdown `< 200,000 JPY`
- sample sufficiencyを個別に満たす

複数playbookの有効性を主張するには、少なくとも2 playbookが個別gateを満たす。1 playbookだけの
passはstandalone shadow resultであり、routerが局面適応に成功した証拠としない。
lossが0件でprofit factorが未定義の場合、任意の巨大値へ置換せず`INSUFFICIENT`とする。

即時cohort invalidation:

- future data、outcome leakage、population omission、hash mismatchの証拠算入
- decision後のthreshold、cost、priority、exit変更
- acceptedだけを残す、zero-candidateまたはrejectedを落とす
- blind audit前にoutcomeをreviewerへ開示する

performance gate failは該当policy/playbook versionをfreezeする。fail後の新versionは残slotを
消費し、同じcohortを再利用しない。trial budget終了後は別cycleと別charterなしに続けない。

## Position Version Lifecycle

shadow positionもentry時のpolicy、playbook、entry、invalidation、exit、cost versionへ拘束する。
policy expiration、playbook retire、emergency deactivateは新規entryを止めるが、既存positionの
exit、MTM、outcome finalizationを止めない。既存positionを新versionへ移さない。

## Required Records Before Any Collection

- design freeze authorization
- post-2026-09-30 candidate-search authorization
- trial registry with at most 3 empty-to-filled proposal slots
- candidate-intake registration and canonical hash
- playbook static contract and canonical hash
- policy/assignment/capacity registration and canonical hash
- outcome/cost/audit/sufficiency registration and canonical hash
- runtime implementation authorization
- prospective collection authorization with effective/expiry timestamps

一つでも欠ける場合、Phase 1 codeをfixture以外へ実行しない。

## Relationship To The Project Kill Switch

このcycle、将来のrouter shadow、counterfactual outcomeは2026-09-30 Project Kill Switchの
証拠でも救済策でもない。9月30日の判定を延期せず、既存cohortへ混ぜない。判定がtriggerなら、
AGENTS.mdに従ってlive strategy developmentを停止する。その後router研究を再開する場合も、
資本スケール計画と新project charterを先に認可する。

## Approval State

- design drafting authorized: true
- design frozen: true
- candidate search authorized: false
- candidate IDs created: 0
- trial slots consumed: 0 of 3
- playbooks admitted: 0
- historical/forward outcome computation authorized: false
- runtime/external integration authorized: false
- prospective shadow authorized: false
- paper/live authorized: false
- Project Kill Switch changed: false

この設計の数値と境界はfreeze済みである。変更は新versionと別の明示認可を必要とし、v0を
上書きしない。candidate searchは2026-09-30 Project Kill Switch判定後、かつ別の明示認可まで
開始しない。runtime実装、outcome計算、prospective collectionもそれぞれ別認可とする。
