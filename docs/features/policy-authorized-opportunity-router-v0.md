# Policy-Authorized Opportunity Router v0

作成日: 2026-08-09

Status: `PHASE1_IMPLEMENTED_PHASE2_DESIGN_FROZEN_NOT_ACTIVATED`

## Purpose

単一戦略を相場局面ごとに後付けで切り替えるのではなく、事前承認した少数の
economic-mechanism playbookから、案件ごとに使用可否を自動判定する上位プロセスを
検討する。

ユーザーは個別案件へタイムリーに回答できない前提とする。このため、案件ごとの
人間承認を待つhuman-in-the-loop方式は採用しない。人間はpolicy versionを事前承認し、
定期監査と緊急停止を行うhuman-on-the-loop方式を想定する。

ユーザーは2026-08-09に、純関数router、version/hash検証、ローカルappend-only JSONL、
unit testだけを含むPhase 1を明示認可した。認可記録は
`research/opportunity-router/phase1-implementation-authorization.json`である。

ユーザーの同日の「続けて」は、candidate非依存の
`PLAYBOOK_ADMISSION_AND_FORWARD_EVIDENCE_DESIGN`をdraftする認可として限定解釈した。
設計開始記録は`research/opportunity-router/phase2-design-start-authorization.json`、設計正本は
`docs/features/opportunity-router-phase2-admission-forward-evidence-design-v0.md`と
`research/opportunity-router/playbook-admission-forward-evidence-design-v0.json`である。
ユーザーは同日に設計v0だけを明示的にfreezeした。freeze認可記録は
`research/opportunity-router/phase2-design-freeze-authorization.json`である。candidate選定、
admission、runtime、outcome計算、収集は認可しない。

Phase 1を超える次のいずれも認可しない。

- playbookの採用または再評価
- runtime runner、外部contract、database、Pub/Sub、Supabase、Dashboardの実装
- historical backtest、既存期間のregime再分類、性能計算
- prospective shadowの開始
- paper/liveへのsignalまたはorderの送信

## Why This Is A Separate Process

これまでの研究で蓄積した主な資産は、複数の承認済みalphaではなく、データ欠損、
lot、gap、spread、銘柄・期間集中、候補不足、後付け条件分岐などの失敗知識である。
したがって、棄却済み戦略の良かった部分集合を探索してrouterへ入れることは禁止する。

`cross_sectional_adaptation_v0`は2候補を使い切って終了している。このrouterは同cycleの
Candidate 3でも、2026-09-30 Project Kill Switchの救済策でもない。新しいmeta-decision
processとして別charter、別forward evidence、別認可を必要とする。

## Operating Model

運用モデルは`POLICY_AUTHORIZED_AUTOMATION_WITH_ASYNC_HUMAN_OVERSIGHT`とする。

人間が事前に承認するもの:

- active playbook IDsとversion
- playbookごとの対象instrument、evidence cutoff、holding horizon、失効条件
- gateの必須項目、閾値、欠測時の扱い
- fixed notionalまたはfixed risk、同時保有上限、集中上限
- policy activation/expiration日時

システムが案件ごとに行うもの:

- candidateの時点整合性と重複を検証する
- active policy versionとplaybook versionを固定する
- 必須gateを評価する
- `ENTER_SHADOW`または`NO_TRADE`を自動決定する
- 採用、却下、期限切れ、重複、候補ゼロをすべて記録する

人間が非同期に行うもの:

- decision ledgerとconfidence calibrationの定期監査
- 将来のpolicy versionを別認可でactivate/deactivateする
- 異常時に将来の判断を停止する

人間の不応答は承認でも拒否でもなく、通常処理へ影響しない。`NO_TRADE`を人間が
後から`ENTER`へ変更するpositive overrideは禁止する。リスク削減方向の停止は許せるが、
記録済みdecisionを別playbookへ付け替えない。

人間の具体的なcadence、authorization、process audit、outcome diagnostic、月次・四半期
review、emergency actionは
`docs/runbook/opportunity-router-human-oversight-v0.md`に分離する。同runbookと機械可読contractも
plan-onlyであり、運用開始や新しい権限を意味しない。

## Initial Playbook Admission State

- maximum active playbooks: 3
- currently admitted playbooks: 0
- LIQIMP: `FROZEN_REJECTED_DEVELOPMENT`のためadmission禁止
- IMOM6M: `FROZEN_REJECTED_DEVELOPMENT_GATE_A`のためadmission禁止
- existing event lanes: 現行のprospective evidenceを継続するだけで自動admissionしない
- existing technical strategies: 実装済みであることを独立証拠とみなさない

playbook候補は、異なるeconomic mechanism、独立した事前登録、時点整合した入力、
実行可能なinvalid/exit contractを持たなければならない。採用判断は別の明示認可まで
行わない。

## Candidate Intake And Population Contract

routerへ到達したcandidateだけを保存しても母集団は再現できない。playbook admissionより
前に、candidate生成自体をversion固定し、候補ゼロを含むupstream populationをhashで
拘束する。

```yaml
candidate_intake_contract:
  source_id:
  source_version:
  eligible_instrument_contract:
  detection_rule:
  evidence_cutoff_rule:
  deduplication_rule:
  candidate_id_generation:
  zero_candidate_session_definition:
  upstream_population_hash:
```

上記のいずれかが未固定、入力populationのhashが不一致、またはeligible instrumentの
point-in-time再現ができない場合、そのsessionはrouter evidenceへ算入せずentryを生成しない。
失敗自体は`INTAKE_CONTRACT_INVALID`または`POPULATION_HASH_MISMATCH` heartbeatとして残す。
候補生成規則をforward開始後に変更する場合は別versionと別cohortを必要とする。

## Decision Gates

初期decisionは加重平均scoreで決めない。次の必須gateを個別に`PASS / FAIL / UNKNOWN`
で保存し、一つでも`PASS`でなければ`NO_TRADE`とする。

| Gate | Owner | Minimum contract |
|---|---|---|
| Evidence | router | 一次情報、cutoff、receipt、hash、事実と推測の分離 |
| Mechanism fit | router | 事前登録済みmechanismへの案件の動的適合。案件ごとの新規作文は禁止 |
| Context | router | decision時点で利用可能な市場・銘柄状態と適合規則 |
| Execution | router precheck | staleでない価格・gap・spread・depth・lot・session |
| Portfolio | Gateway authoritative | position、集中、capital、kill switch、risk、order feasibility |

RouterのPortfolio gateはshadow上の暫定評価に限る。paper/liveが将来認可された場合も、
Gatewayだけが最終risk判定とquantity調整を行う。RouterやLLMへrisk執行を分散しない。

Mechanismは二層に分ける。admission時の`playbook_static_validity`でeconomic mechanism、
falsifiability、holding horizon、admissible evidenceを固定する。案件時には
`candidate_dynamic_fit`としてrequired event、magnitude、contradictory event、horizonを
機械評価するだけとし、LLMが案件ごとにmechanismや判定条件を発明しない。

`confidence`は当初entry/no-entryの監査属性であり、position sizingに使わない。
総合confidenceは必須gateの最小値より強く表現してはならず、単一の高得点で弱いgateを
相殺しない。

## LLM Boundary

LLMは一次情報の整理、反証候補、playbook分類候補を構造化できる。ただし、次を満たさない
LLM出力は`UNKNOWN`として`NO_TRADE`へ倒す。

- model、prompt、tool、schema versionが固定されている
- evidence cutoffより後の情報を使っていない
- required factが保存済みsourceへ紐づく
- playbook固有の機械検証可能な条件を満たす
- parser disagreement、timeout、malformed outputがない

自由記述の主観confidenceだけで`ENTER_SHADOW`を生成しない。

## Playbook Assignment And Capacity Contract

同一candidateが複数playbookへ一致した場合、事前固定した一意のassignment ruleがない
v0既定動作は`AMBIGUOUS_PLAYBOOK`による`NO_TRADE`とする。案件を見てから「より適切な」
playbookへ付け替えない。将来priority方式を採る場合も、priority version、same-instrument、
same-sector、tie-breakerをforward開始前に固定する。

全gate通過candidateが同時保有・capital上限を超える場合も、到着順や人間判断で選ばない。
routerまたは将来のportfolio proposal層が次を事前固定し、選外を`CAPACITY_REJECTED`として
保存する。規則未設定または一意に解けないtieはentryを生成しない。

```yaml
capacity_resolution:
  playbook_priority:
  candidate_priority:
  tie_breaker:
  same_instrument_rule:
  same_sector_rule:
  reason_code: CAPACITY_REJECTED
```

これは候補選択規則であり、Gatewayの権限を置き換えない。Gatewayは提出されたproposalを
最終risk上さらに拒否・縮小できるが、routerのpriorityを事後変更したりrisk違反を許可しない。

## Phase 1 Decision Record

Phase 1のローカルledgerは、次をimmutableかつSHA-256 hash chain付きで保存できる。

```yaml
decision_id:
decision_at:
policy_id:
policy_version:
policy_sha256:
playbook_id:
playbook_version:
playbook_contract_sha256:
candidate_id:
candidate_intake_version:
candidate_intake_contract_sha256:
upstream_population_hash:
instrument:
sector:
evidence_cutoff_at:
valid_until:
matched_playbook_ids: []
assignment_rule_version:
capacity_rule_id:
capacity_rule_version:

gates:
  evidence: PASS | FAIL | UNKNOWN
  mechanism: PASS | FAIL | UNKNOWN
  context: PASS | FAIL | UNKNOWN
  execution: PASS | FAIL | UNKNOWN
  portfolio_precheck: PASS | FAIL | UNKNOWN

decision: ENTER_SHADOW | NO_TRADE | EXPIRED | DUPLICATE | POLICY_DISABLED
reason_codes: []
counterfactual_class: POLICY_EVALUABLE | ECONOMIC_ONLY_NOT_EXECUTABLE | ADMINISTRATIVE_TERMINAL | NOT_APPLICABLE
candidate_priority:
```

thesis、source provenance、entry/invalidation/exit、notional/risk等のdomain fieldは、実playbook
contractを固定する次段階まで追加しない。Phase 1 fixtureの`ENTER_SHADOW`は純関数のterminal
branchを検証するだけで、forward shadowの開始または証拠保存を意味しない。

`WATCH`は注文待ち状態にしない。再評価時には新しいcutoffとdecision IDを持つ別decisionを
作る。期限後の人間承認や遅延データで過去decisionを更新しない。

候補ゼロのsessionもheartbeatとして保存する。採用取引だけを残して選球眼を過大評価する
ことを防ぐため、全candidateのdecisionと、将来認可された場合のcounterfactual outcomeを
同じ母集団で追跡する。

counterfactualはreasonを無視して一括比較しない。`POLICY_EVALUABLE`は必要データと実行可能性が
揃いながらpolicy gateで却下された案件、`ECONOMIC_ONLY_NOT_EXECUTABLE`は経済評価は可能でも
execution不能、`ADMINISTRATIVE_TERMINAL`はdisabled・duplicate・expiry等、
`NOT_APPLICABLE`は候補ゼロ等とする。selector比較の主対象は`ENTER_SHADOW`対
`POLICY_EVALUABLE`に限定する。欠測案件へ後から終値だけを当てて「見逃し利益」とみなさない。

## Async Review And Learning Contract

人間が案件時刻に立ち会えなくても、判断品質の改善は非同期に行える。損益と判断品質を
混同せず、`ENTER_SHADOW`だけでなく`NO_TRADE / EXPIRED / DUPLICATE /
POLICY_DISABLED`と候補ゼロも同じdecision cohortに残す。

reviewは元のdecisionを変更せず、別のappend-only recordとして紐づける。最初に損益、
MAE/MFE、将来価格を非表示にした`PROCESS_AUDIT`を確定し、その後で
`OUTCOME_DIAGNOSTIC`を行う二段階とする。結果を見てからprocess評価を上書きしない。
新しい規則候補は`HYPOTHESIS_GENERATING`へ分離し、active policyへ直接戻さない。

少なくとも次の軸を、観測事実、事前規則への適合、結果の三つに分けて評価する。

- hypothesis: 想定した価格反映mechanismと反証条件は妥当だったか
- information: 必須事実の欠落、推測の事実扱い、cutoff違反がなかったか
- context: playbookを適用できる局面だったか
- timing: 早過ぎ、遅過ぎ、許容gap超過がなかったか
- execution: spread、depth、lot、流動性、sessionが結果へ与えた影響
- size: 事前のfixed notional/riskと集中上限に適合したか
- discipline: invalidation、exit、expiry、Gateway判断を守ったか

利益になってもprocess違反なら良い判断としない。損失でも事前contractへ適合していれば、
単独事例を理由にplaybookを変更しない。自信度はbucket別にcalibrationし、高confidenceが
実際に高い費用控除後expectancyへ対応したかを見る。

review cadenceは次を初期案とする。

- decision確定時: システムが事実、reason code、逸脱を追記する
- process review: outcomeをblindした状態で事前contractへの適合を確定する
- outcome確定時: process reviewをlockした後に損益、MAE/MFE、費用、counterfactualを診断する
- monthly: 人間が採用対非採用、confidence calibration、gate別傾向、playbook別傾向を監査する
- quarterly: 十分性contractを満たした版だけを継続、version更新、retire候補として審査する
- new hypothesis: 現役playbookへ混ぜず、別research ledgerから新versionのprospective shadow候補へ進める

月次・四半期reviewは売買を待たせない。policy/playbookの変更は常に将来有効な新versionと
別認可を必要とし、単一の大勝ち・大負け、事後的なregime名、却下案件の見逃し利益だけで
activationしない。`WATCH`相当の再観測も注文待ち状態にはせず、新cutoffの別decisionとする。

## Future Position Lifecycle Contract

paper/liveが将来別認可された場合、policy expiration、playbook retire、emergency deactivate、
version移行は新規entryを停止しても既存positionのexit責務を消してはならない。各positionを
entry時のpolicy、playbook、exit、invalidation versionへ拘束する。

```yaml
position_binding:
  entry_policy_version:
  entry_playbook_version:
  exit_contract_version:
  invalidation_contract_version:

policy_deactivation:
  blocks_new_entries: true
  cancels_required_exits: false
  reactivation_requires_new_authorization: true
```

Gatewayのkill switchやrisk削減命令は、拘束済みexitより早い縮小・決済を要求できる。
deactivateを理由にexit monitorや必要なcloseoutを止めず、新versionへ既存positionを付け替えない。

## Architecture Boundary

現時点では既存Aggregatorをrouterとして改造しない。AggregatorはRule/AIの同一
`strategy_key / candidate_id`合議を継続し、Gatewayは`trade-signals`以降のriskとroutingを
継続する。

Phase 1は`services/opportunity-router`へlibrary-only workspace packageとして実装した。
`__main__`、CLI、scheduler、ambient env読込はなく、既存9 runtime servicesのhealth-check対象に
追加していない。実装済み範囲は次のとおり。

- canonical JSONとSHA-256によるcandidate-intake、playbook、policy、population binding
- timezone-aware cutoff、expiry、duplicate、playbook ambiguity、全gateのfail-closed評価
- 事前登録capacity ruleによるinput-order非依存の決定
- deterministic decision ID
- flock、fsync、hash chain、conflict検出を持つ冪等なlocal JSONL ledger

段階は次のように維持する。

1. **COMPLETED**: 純関数routerとlocal JSONL decision ledger。外部I/Oなし。
2. shadow-only candidate intakeとledger writer。`trade-signals`へpublishしない。
3. forward shadow evaluator。accepted/rejected/no-candidateを同じcohortでfinalizeする。
4. 2026-09-30判定後、別認可がある場合だけpaper接続を設計する。
5. paper evidenceと別live認可後に限りGatewayへ既存contract互換のintentを渡す。

runtime service、topic、subscription、table、外部contractの要否はPhase 2の別認可時に決める。
サービス間の直接通信は禁止し、外部schemaを追加する場合は`contracts/`をSSOTとする。

## Failure And Timing Semantics

- policyまたはplaybook versionが不明: `POLICY_DISABLED`
- evidence、feature、board、position snapshotが欠測: `NO_TRADE`
- `decision_at > valid_until`: `EXPIRED`
- 同じcandidate/policy/playbook/cutoffの再配信: 同一decision IDで冪等化
- decision完了後のlate data: 過去decisionを変更せず、必要なら新candidateとして再評価
- Supabase/Pub/Sub/LLM failure: entryを生成しない
- human response timeout: 存在しない。個別承認を待たない

## Prospective Evaluation Contract

既存historyはplaybook設計と失敗条件の学習にのみ使い、新routerのuntouched OOSとは
みなさない。証拠はpolicy freeze後のprospective shadowから作る。

記録候補の指標:

- all-candidate count、admission rate、no-trade rate、zero-candidate sessions
- gate別fail/unknown率、data completeness、decision latency、expiry率
- confidence bucket別の費用控除後expectancy
- acceptedとrejected candidateのcounterfactual outcome差
- counterfactual class別の件数と、`POLICY_EVALUABLE`に限定したselector差
- playbook別expectancy、MAE/MFE、hold期間乖離
- hypothesis、information、context、timing、execution、size、discipline別の逸脱率
- process-compliant win/lossとprocess-violating win/lossの分離
- 最大drawdown、instrument/playbook/sectorへの依存
- policy version別成績と停止理由

sample size、outcome horizon、cost、pass/fail threshold、shadow期間はPhase 2 design v0で
freeze済みである。これはforward collectionの開始認可ではない。

## Authority Boundary

現在の権限はPhase 1の隔離されたlibrary実装と、candidate非依存のPhase 2設計freezeまでである。

- phase1 implementation authorized: true
- phase1 implementation status: `IMPLEMENTED_NOT_ACTIVATED`
- phase2 design drafting authorized: true
- phase2 design status: `FROZEN_DESIGN_ONLY_NOT_ACTIVATED`
- phase2 design frozen: true
- runtime/external integration authorized: false
- playbook admission authorized: false
- historical or forward outcome computation authorized: false
- shadow collection authorized: false
- paper/live authorized: false
- current strategy parameters changed: false
- Project Kill Switch changed: false
- counts as 2026-09-30 evidence: false

Phase 1 codeをfixture以外のhistorical/forward candidateへ実行してledgerを収集することは、
この認可に含まれない。現行9/30 readiness pipelineとそのartifact/hash chainは変更しない。

Phase 2 design v0はfreeze済みであり、変更はv0の上書きではなく新versionと別認可を必要とする。
candidate選定は2026-09-30 Project Kill Switch判定後かつ別認可まで開始せず、admission、
runtime、outcome計算、prospective collectionもそれぞれ別認可とする。Kill Switchがtriggerした
場合は、資本スケール計画と新project charterの認可をcandidate searchより先に必要とする。
現行戦略、既存event shadow、ETF Phase 3 NO-GOはそのまま維持する。

将来、人間が案件選択を楽しむlaneを設ける場合は`manual_discretionary_sandbox`としてrouterと
別contract・別ledger・別performance evidenceに分離する。人間判断をrouter成績へ混ぜない。
