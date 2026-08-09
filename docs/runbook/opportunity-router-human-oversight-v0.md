# Opportunity Router Human Oversight Runbook v0

作成日: 2026-08-09

Status: `PLAN_ONLY_NO_OPERATION_AUTHORITY`

## Purpose

`policy_authorized_opportunity_router_v0`で、人間がいつ何を確認し、どの記録を残すかを
定義する。個別案件への即時回答を要求せず、人間の不在時もシステムは事前policyに従って
動き、欠測・曖昧・failure時はfail-closedとする。

このrunbookは運用案の記録だけである。Phase 1 library実装は別の明示認可により完了したが、
このrunbookはplaybook admission、runtime、outcome計算、forward収集、shadow、paper、liveを
開始しない。現在のadmitted playbookは0である。

## Human Role

人間はリアルタイムtraderではなく、次の役割を担う。

- research proposer: economic mechanismと新playbook versionを提案する
- policy authorizer: 将来有効なpolicy/playbook versionを認可または拒否する
- process auditor: outcomeを見ずに事前contractへの適合を監査する
- outcome analyst: lock済みprocess auditと結果の差を診断する
- risk controller: 異常時に将来entryを停止し、Gatewayのrisk削減を妨げない

一人で全役割を担ってよいが、記録上のstageを混ぜない。特にprocess auditをlockするまで
outcomeを表示せず、outcomeを見た後に元decisionまたはprocess auditを変更しない。

## Recommended Cadence

| Timing | System | Human | Response deadline |
|---|---|---|---|
| policy作成時 | hashとversionを検証する | contract一式を確認し、将来versionを認可または拒否する | activation前。案件発生時ではない |
| 各営業日のpre-open | healthとactive versionを自動検証する | 通常は何もしない。異常通知を後から確認できる | なし。異常時は自動fail-closed |
| 市場中・案件判断時 | gateを評価しterminal decisionを保存する | 何もしない | なし。`WAITING_HUMAN`を作らない |
| decision後 | blind process-audit queueを作る | 都合のよい時間に事前contract適合を確認する | 売買・次decisionを待たせない |
| outcome確定後 | process audit lockを確認して結果を提示する | outcome diagnosticを追記する | 月次reviewまでを推奨、運用依存にはしない |
| 月次 | cohort、calibration、逸脱を集計する | 傾向を監査し、新仮説だけをresearch ledgerへ送る | 次回月次まで。active versionを直接変更しない |
| 四半期または十分性到達時 | frozen cohort reportを作る | 継続、retire、新version提案を審査する | future effective-atより前の別認可 |
| 異常時 | entryをfail-closedし、exit責務を維持する | 見た時点で将来entry停止を指示できる | 即応を前提にしない |

推奨する人間の時間配分は、通常営業日は0分、月次30〜60分、四半期60〜120分程度である。
これはSLAではない。人間の不応答によってentryが承認されたり、既存exitが止まったりしない。

## Policy Authorization Checklist

人間は個別candidateではなく、将来versionについて次を確認する。

- policy、playbook、candidate-intake、assignment、capacity ruleのID/version/hash
- eligible instrument、source version、cutoff、population hash、zero-candidate定義
- static mechanism、dynamic fit、全gateと`FAIL / UNKNOWN`時の動作
- fixed notional/risk、同時保有、symbol/sector集中、tie-breaker
- entry、invalidation、exit、holding horizon、activation、expiration
- outcome horizon、cost、counterfactual class、sample sufficiency、promotion/termination
- policy停止後も既存positionのversion-bound exitが残ること
- evidence lineageと、既存historyをuntouched OOSとして扱っていないこと
- 現行Project Kill Switchの期限・基準・証拠を変更していないこと

一項目でも未固定またはhash不一致なら認可しない。認可はfuture effective-atを持つ
append-only recordとし、当日案件への遡及適用を禁止する。

## Process Audit Checklist

process auditでは損益、MAE/MFE、将来価格、counterfactual returnを表示しない。

1. decision時点で見えていたsource、cutoff、receipt、hashを確認する。
2. candidate intakeとeligible populationが登録versionに一致したか確認する。
3. playbook assignment、capacity、Evidence/Mechanism/Context/Execution/Portfolio gateを確認する。
4. hypothesis、information、context、timing、execution、size、disciplineを評価する。
5. `PROCESS_COMPLIANT / PROCESS_DEVIATION / UNKNOWN`とreason codeを記録する。
6. visible-information cutoff、reviewer、reviewed-at、record hashを保存してlockする。

単独案件のprocess auditからactive ruleを変更しない。`UNKNOWN`を後から都合よく`COMPLIANT`へ
変更せず、訂正が必要なら元recordを残したcorrection eventを追記する。

## Outcome Diagnostic Checklist

lock済みprocess auditが存在する場合だけoutcomeを表示する。

- realized/counterfactual outcome、MAE/MFE、gap、spread、費用、holding-period乖離
- `ENTER_SHADOW`対`POLICY_EVALUABLE`の同一contract比較
- execution不能、administrative terminal、候補ゼロの別集計
- process-compliant win/lossとprocess-deviation win/lossの分離
- confidence bucket、playbook、instrument、sector、policy versionへの依存

診断結果は`OUTCOME_DIAGNOSTIC`へ追記する。局面や条件の新しい着想は
`HYPOTHESIS_GENERATING`として別research ledgerへ送り、active versionへ直接反映しない。

## Monthly Review

月次reviewで確認するもの:

- all-candidate、terminal decision、zero-candidate sessionのcoverage
- intake/gate別の`FAIL / UNKNOWN`、expiry、duplicate、ambiguity、capacity rejection
- accepted対`POLICY_EVALUABLE` rejected、confidence calibration
- process deviationの種類と再発、データ・実装上のincident
- playbook/instrument/sector集中とsample sufficiencyの進捗

月次reviewのterminal actionは`NO_CHANGE`または`CREATE_RESEARCH_PROPOSAL`とする。
active parameterの変更、positive per-trade override、retroactive reclassificationを行わない。

## Quarterly Or Sufficiency Review

事前固定したsample sufficiencyとoutcome horizonを満たしたfrozen cohortだけを審査する。

許される提案:

- `CONTINUE_UNCHANGED`: 同一versionを既存expirationまで維持する
- `RETIRE_FUTURE_ENTRIES`: 将来entryを停止する
- `PROPOSE_NEW_VERSION`: 別research/freeze/authorizationへ送る
- `NO_CONCLUSION`: 不十分として変更しない

期間延長、reactivation、新version activationは別のauthorization recordを必要とする。
レビュー中も現行versionを案件ごとに変更しない。

## Emergency And Incident Actions

人間が異常を認識した場合に許される即時操作はリスク削減方向だけである。

- future entryをdeactivateする
- Gatewayの既存kill switchまたはrisk-off手順を使う
- incident時刻、観測事実、操作、対象version、operatorを記録する
- exit monitor、scheduled exit、closeoutを継続する

禁止事項:

- `NO_TRADE`を`ENTER`へ変更する
- rejected candidateを別playbookへ付け替える
- active thresholdをその場で緩める
- policy停止を理由に必要なexitを取り消す
- incident解消だけで旧versionを自動reactivateする

human responseが間に合わない場合は、システムのfail-closedとGatewayが資金を保護する。

## Required Append-Only Human Records

- `PolicyAuthorizationRecord`
- `ProcessAuditRecord`
- `OutcomeDiagnosticRecord`
- `ResearchHypothesisRecord`
- `PolicyDeactivationRecord`
- `IncidentRecord`

各recordはactor、created-at、effective-at、対象ID/version/hash、reason、source provenanceを持つ。
訂正は元recordを消さず、supersedesで連結する。

## Project Kill Switch Boundary

2026-09-30 Project Kill Switchの判定はrouterの月次・四半期reviewと別に行う。このrunbook、
将来のrouter shadow、manual discretionary sandboxは現行判定の証拠でも救済策でもない。
Kill Switch条件を変更する場合は理由の文書化、明示確認、最低1週間のcooling-offを維持する。

## Current Authority

- runbook recording authorized: true
- human operation activated: false
- phase1 library implementation authorized: true
- phase1 library implementation status: `IMPLEMENTED_NOT_ACTIVATED`
- phase2 candidate-independent design drafting authorized: true
- phase2 design status: `DRAFT_COMPLETE_AWAITING_EXPLICIT_FREEZE_AUTHORIZATION`
- phase2 design frozen: false
- policy or playbook admission authorized: false
- runtime/external integration authorized: false
- outcome computation authorized: false
- shadow/paper/live authorized: false
- Project Kill Switch changed: false

candidate非依存の`PLAYBOOK_ADMISSION_AND_FORWARD_EVIDENCE_DESIGN` draftは
`docs/features/opportunity-router-phase2-admission-forward-evidence-design-v0.md`へ作成済みである。
次の人間の判断点は同draftをfreezeするかどうかであり、freezeしてもcandidate選定、admission、
runtime、prospective collectionを認可しない。
