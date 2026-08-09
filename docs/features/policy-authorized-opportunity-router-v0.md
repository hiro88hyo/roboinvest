# Policy-Authorized Opportunity Router v0

作成日: 2026-08-09

Status: `PLAN_ONLY_NO_IMPLEMENTATION_AUTHORITY`

## Purpose

単一戦略を相場局面ごとに後付けで切り替えるのではなく、事前承認した少数の
economic-mechanism playbookから、案件ごとに使用可否を自動判定する上位プロセスを
検討する。

ユーザーは個別案件へタイムリーに回答できない前提とする。このため、案件ごとの
人間承認を待つhuman-in-the-loop方式は採用しない。人間はpolicy versionを事前承認し、
定期監査と緊急停止を行うhuman-on-the-loop方式を想定する。

この文書は設計案であり、次のいずれも認可しない。

- playbookの採用または再評価
- router、contract、database、Pub/Sub、Dashboardの実装
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

## Decision Gates

初期decisionは加重平均scoreで決めない。次の必須gateを個別に`PASS / FAIL / UNKNOWN`
で保存し、一つでも`PASS`でなければ`NO_TRADE`とする。

| Gate | Owner | Minimum contract |
|---|---|---|
| Evidence | router | 一次情報、cutoff、receipt、hash、事実と推測の分離 |
| Mechanism | router | playbook固有の伝達経路、holding horizon、反証条件 |
| Context | router | decision時点で利用可能な市場・銘柄状態と適合規則 |
| Execution | router precheck | staleでない価格・gap・spread・depth・lot・session |
| Portfolio | Gateway authoritative | position、集中、capital、kill switch、risk、order feasibility |

RouterのPortfolio gateはshadow上の暫定評価に限る。paper/liveが将来認可された場合も、
Gatewayだけが最終risk判定とquantity調整を行う。RouterやLLMへrisk執行を分散しない。

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

## Proposed Decision Record

実装が別途認可された場合、少なくとも次をimmutableに保存する。

```yaml
decision_id:
decision_at:
policy_id:
policy_version:
playbook_id:
playbook_version:
candidate_id:
instrument:
evidence_cutoff_at:
valid_until:

thesis:
expected_transmission_mechanism:
expected_holding_period:
invalidation_condition:

gates:
  evidence: PASS | FAIL | UNKNOWN
  mechanism: PASS | FAIL | UNKNOWN
  context: PASS | FAIL | UNKNOWN
  execution: PASS | FAIL | UNKNOWN
  portfolio_precheck: PASS | FAIL | UNKNOWN

decision: ENTER_SHADOW | NO_TRADE | EXPIRED | DUPLICATE | POLICY_DISABLED
reason_codes: []
target_notional_policy:
entry_condition:
exit_condition:
maximum_acceptable_gap:

human_action: NONE | DEACTIVATE_FUTURE_POLICY
source_provenance: []
```

`WATCH`は注文待ち状態にしない。再評価時には新しいcutoffとdecision IDを持つ別decisionを
作る。期限後の人間承認や遅延データで過去decisionを更新しない。

候補ゼロのsessionもheartbeatとして保存する。採用取引だけを残して選球眼を過大評価する
ことを防ぐため、全candidateのdecisionと、将来認可された場合のcounterfactual outcomeを
同じ母集団で追跡する。

## Architecture Boundary

現時点では既存Aggregatorをrouterとして改造しない。AggregatorはRule/AIの同一
`strategy_key / candidate_id`合議を継続し、Gatewayは`trade-signals`以降のriskとroutingを
継続する。

将来の実装案は次の段階に分ける。

1. 純関数routerとJSONL decision ledger。外部I/O、Pub/Sub、Supabaseなし。
2. shadow-only candidate intakeとledger writer。`trade-signals`へpublishしない。
3. forward shadow evaluator。accepted/rejected/no-candidateを同じcohortでfinalizeする。
4. 2026-09-30判定後、別認可がある場合だけpaper接続を設計する。
5. paper evidenceと別live認可後に限りGatewayへ既存contract互換のintentを渡す。

新topic、subscription、table、contract、新サービスの要否はPhase 1実装認可時に決める。
サービス間の直接通信は禁止し、`contracts/`をSSOTとする。

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
- playbook別expectancy、MAE/MFE、hold期間乖離
- 最大drawdown、instrument/playbook/sectorへの依存
- policy version別成績と停止理由

sample size、outcome horizon、cost、pass/fail threshold、shadow期間はまだ固定しない。
これらを決める前にforward collectionを開始してはならない。

## Authority Boundary

現在の権限はplan記録だけである。

- implementation authorized: false
- playbook admission authorized: false
- historical or forward outcome computation authorized: false
- shadow collection authorized: false
- paper/live authorized: false
- current strategy parameters changed: false
- Project Kill Switch changed: false
- counts as 2026-09-30 evidence: false

次へ進むには、最大3つの候補を選ぶ前に、playbook admission contract、trial budget、
forward outcome contract、sample sufficiency、cost、promotion/freeze条件を明示し、別のユーザー
承認を得る。現行戦略、既存event shadow、ETF Phase 3 NO-GOはそのまま維持する。
