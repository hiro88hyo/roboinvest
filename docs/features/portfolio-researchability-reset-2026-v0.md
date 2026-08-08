# Portfolio Researchability Reset 2026 V0

作成日: 2026-08-08  
Status: `PLAN_ONLY_NO_EXECUTION_AUTHORITY`  
Identity: `portfolio_researchability_reset_2026_v0`（非 alpha・非 strategy candidate）

## Decision

2026-09-30 の Project Kill Switch 判定が完了するまで、新しい strategy candidate
ID、売買 signal、alpha 仮説、performance artifact を作らない。

`cross_sectional_adaptation_v0` の 2 候補は閉じたままとし、LIQIMP、IMOM、
quality、value、momentum 等の派生案をこの reset で比較しない。代わりに、将来の
研究を開始できるだけのデータ、投資対象、資本・lot、portfolio geometry、独立した
評価期間が存在するかを、将来 return を使わずに判定する計画だけを定義する。

この文書は調査・実装・データ取得・計算を許可しない。各 phase は、scope と入力
hash を固定した別の明示承認なしには開始しない。

## Motivation

閉鎖済み cycle の 2 候補は、異なる理由で development を通過しなかった。

- `liqimp1m_logdiff_v0_research` は 141 trades、PF 0.368、最大 daily MTM DD
  58.55% で、登録済みの long-only top-5 実装に経済的 edge を示さなかった。
- `imom6m_top5_fixed20_v0_research` は Gate A の 28 形成月中、exact 翌月末
  outcome が全適格銘柄で揃った月が 5 か月だけだった。登録最低 24 か月に届かず、
  完全 5 か月でも D10-D1 平均 -1.0583%、rank IC 平均 -0.03249 だった。

したがって、次に別の個別株特徴量を試す前に、次の二点を切り分ける必要がある。

1. 全銘柄型研究を point-in-time かつ terminal outcome を含めて再構築できるか。
2. 2M JPY と日本株の売買単位で、意図した分散 portfolio を実装できるか。

IMOM の一般的な不存在を主張するものではない。一方、欠損銘柄だけを落とした
complete-case performance 再計算は、事後的な cohort 変更なので行わない。

## Frozen Boundaries

この reset の全 phase で、以下を禁止する。

- LIQIMP または IMOM の signal、return、spread、rank IC、trade、PF、DD の再計算
- IMOM12M、skip-month、符号反転、quantile、regime、HTP、quality/value 合成
- 新しい個別株 alpha family の順位付けまたは成績比較
- ETF、指数、sector の過去または将来 return、risk-on/off signal、仮想 PnL の比較
- 既存 validation、locked OOS、prospective OOS の outcome inspection
- Project Kill Switch の期限、PF、DD、cohort、cost、判定規則の変更
- paper/live route、watchlist、publisher、Gateway、OMS、Supabase、Pub/Sub の変更
- reset の GO 判定を strategy 実装・paper/live activation と解釈すること

欠測診断では「価格行が存在するか」「欠損理由は何か」という非性能情報だけを扱う。
欠損銘柄の return は計算・保存しない。

## Inputs That Remain Authoritative

- `AGENTS.md` の Project Kill Switch
- `docs/features/project-kill-switch-readiness.md`
- `research/cross-sectional-adaptation-v0/cycle-closure.json`
- `research/liquidity/liqimp1m-logdiff-v0-disposition.json`
- `research/imom/imom6m-top5-fixed20-v0-disposition.json`
- `docs/adr/0005-locked-oos-inspection-freeze.md`
- `docs/adr/0006-primary-evaluation-capital.md`
- `docs/reports/imom6m-top5-fixed20-v0-gate-a-result-2026-08-08.md`

ChatGPT Pro の[共有回答](https://chatgpt.com/share/6a76884a-0184-83ee-ac84-3cbcbbfed29f)
は方向性を検討する advisory input とする。商品・API・データ仕様の根拠には使わず、
将来の調査時に JPX、J-Quants、auカブコム／kabu API の一次情報で再検証する。

## Phase 0 — Plan Only

現在許可されているのは、この文書の作成とレビューだけである。

完了条件:

- reset が strategy candidate でないことを明記する
- outcome、PnL、signal を扱わないことを明記する
- 各 phase の入力、成果物、GO/NO-GO、権限境界を定義する
- 9月30日まで新candidateを作らないことを明記する

Phase 0 の完了は Phase 1 の開始を自動的に許可しない。

## Phase 1 — Missingness And Data-Lineage Audit

開始には別の明示承認が必要。目的は IMOM の救済ではなく、将来の全銘柄研究でも
同じ fail-closed 崩壊が起きるかを判定することである。

### Scope

Gate A の 28 形成月で欠けた 57 symbol outcomes について、exact formation
month-end と exact next global TSE month-end の行存在・値存在だけを再現し、次の
いずれか一つへ分類する。

1. delisting / terminal event
2. trading suspension / no month-end trade
3. issue-code or security-lineage change
4. merger, share exchange, split, consolidation, or other corporate action
5. historical master/product-category mismatch
6. source API or contracted-plan coverage limitation
7. archive fetch or ingest failure
8. unexplained source-data absence
9. unknown

分類は formation 時点で利用可能だった情報と、outcome 後に確定した terminal-event
metadata を区別して記録する。現在銘柄マスターからの逆算や future backfill で
formation universe を変更しない。

### Required provenance

- source endpoint / dataset identity
- query or archive partition identity
- source receipt timestamp where available
- historical master effective date
- corporate-action or delisting effective date
- raw and normalized artifact SHA-256
- deterministic classifier version and per-reason counts

### Prohibited outputs

- symbol return、decile return、rank IC、PnL
- complete-case Gate A または別 eligibility rule
- performance と欠損理由の関連付け

### Draft GO/NO-GO

- `unknown` または unexplained source-data absence が 1 件でも残る場合、現在の
  archive を使う全銘柄 cross-sectional 研究は NO-GO。
- delisting、lineage、corporate action を含む terminal total-return contract を
  point-in-time に再現できない場合、個別株 all-universe monthly strategy は NO-GO。
- GO はデータ基盤の候補資格にすぎず、IMOM 再開や strategy candidate 作成を
  許可しない。

数値や分類規則は Phase 1 authorization で outcome availability を確認する前に
hash 固定する。

## Phase 2 — Investable-Instrument Inventory

開始には別の明示承認が必要。instrument の収益性は比較しない。

### Universe classes to inventory

- broad Japanese equity index instruments
- sector or industry index instruments
- cash or cash-equivalent sleeve candidates, if legally and operationally usable
- current individual-stock universe as a lot-geometry baseline only

特定 ETF を事前に採用候補とは扱わない。一次情報から機械的に inventory を作る。

### Required fields

- security code, legal product type, listing and termination dates
- tracked index and index methodology identifier
- trading unit, tick size, daily price-limit treatment
- management fee and other known holding costs
- distribution and total-return adjustment availability
- split, consolidation, redemption, delisting, and code-change history
- historical OHLCV and point-in-time master coverage
- median traded value, quoted spread/depth coverage, auction observability
- kabu API quote, board-registration, and order-route compatibility
- source URL, effective date, receipt date, and artifact hash

売買代金や spread は tradability の記述統計としてのみ扱い、将来 return や strategy
selection と結合しない。

### Draft GO/NO-GO

- entry、exit、distribution、corporate action、termination を一貫した PIT contract
  で再現できない instrument class は NO-GO。
- kabu API で必要な quote/order lifecycle を再現できない class は NO-GO。
- product-level source provenance を固定できない inventory は NO-GO。

## Phase 3 — Outcome-Blind 2M Portfolio Geometry

開始には別の明示承認が必要。価格、取引単位、制約だけを用い、形成後 return は
一切読まない。

### Questions

- 目標 weight を board lot へ丸めた際の absolute weight error
- lot rounding 後の cash residual
- feasible position count と effective number of bets
- broad index、sector、issuer、manager の look-through overlap
- sector concentration と同一指数重複
- order notional / median traded value
- current individual-stock 20% cap を instrument classへ機械適用した場合の歪み
- class-aware cap が必要なら、どのリスク単位を新 charter で定義すべきか

### Frozen geometry outputs

- no-return snapshot dates fixed before computation
- target versus realized weights
- uninvested cash fraction
- lot-infeasible instrument count
- concentration and overlap measures
- liquidity participation measures
- infeasibility reasons

個別株、broad instrument、sector instrument の比較は実装可能性に限定する。
どれが儲かったか、過去の winner は何か、どの regime が良かったかは計算しない。

### Draft GO/NO-GO

- lot rounding または共通 cap が portfolio identity を支配し、事前に定めた
  target exposure を安定して実現できない class は NO-GO。
- 2M JPY で分散を主張する場合、effective bets、look-through concentration、cash
  residual の上限を将来の Phase 3 authorization で価格snapshotを見る前に固定する。
- class-aware cap が必要な場合、それは既存 20% rule の調整ではなく、新 project
  charter の portfolio policy として cooling-off を含めて承認する。

## Phase 4 — Post-Kill-Switch Go/No-Go Review

2026-09-30 の Project Kill Switch adjudication 後にのみ実施できる。Phase 1〜3 が
未実施でも、9月30日の判定を延期しない。

### GO requires all

1. Project Kill Switch の判定が完了し、その結果に従っている。
2. trigger の場合、新 project charter、資本スケール計画、損失予算、再停止条件、
   cooling-off が先に承認されている。
3. selected instrument universe の PIT data と terminal-event contract が完全。
4. 2M JPY の lot geometry が signal ranking や exposure policy を支配しない。
5. 既存 event OOS、cross-sectional development、locked OOS と重ならない split を
   固定できる。
6. multiple testing を考慮した trial budget を outcome inspection 前に固定できる。

いずれか未達なら NO-GO。GO でも許可されるのは新 project charter または新
strategy preregistration の提案までであり、実装・return計算・paper/liveは別承認とする。

## Future Evidence Design — Draft Only

新 charter が承認された場合に検討する設計値であり、現時点では固定しない。

- development + validation: minimum 60 monthly formations; 120 preferred
- untouched prospective OOS: minimum 12 monthly formations
- selected instrument entry/exit/terminal outcome completeness: 100%
- one primary candidate per new cycle
- fallback は primary outcome を見た同一期間へ後付けしない
- benchmark-relative return、cash-relative return、DD、volatility、Calmar、turnover、
  worst rolling period、concentration、implementation shortfallを事前選択する

ETF allocation では PF だけを主評価にせず、市場 exposure を放棄して cash に逃げた
だけの低DDを合格にしない。具体的な benchmark、risk budget、position cap、frequency、
cost、execution profile は新 charter と preregistration の対象であり、この reset では
決めない。

## Proposed Deliverables By Future Phase

現時点では以下を作成しない。各 phase が別途承認された場合の予定先である。

| Phase | Proposed artifact | Contains performance? |
|---|---|---|
| 1 | `out/portfolio-researchability-reset-2026-v0/missingness-audit.json` | No |
| 1 | `docs/reports/portfolio-researchability-missingness-audit.md` | No |
| 2 | `out/portfolio-researchability-reset-2026-v0/instrument-inventory.json` | No |
| 2 | `docs/reports/portfolio-researchability-instrument-inventory.md` | No |
| 3 | `out/portfolio-researchability-reset-2026-v0/portfolio-geometry.json` | No |
| 3 | `docs/reports/portfolio-researchability-geometry.md` | No |
| 4 | `docs/reports/portfolio-researchability-go-no-go.md` | No |

## Authority Matrix

| Action | Authorized now? |
|---|---|
| Review or edit this plan | Yes |
| Create a strategy candidate ID | No |
| Implement an audit or fetch data | No |
| Reconstruct missingness flags | No |
| Read or calculate future returns | No |
| Compare instrument performance | No |
| Calculate portfolio geometry | No |
| Inspect validation / locked OOS | No |
| Change Project Kill Switch | No |
| Change paper / live systems | No |

## Stop Conditions

- Phase scope cannot be expressed without return、PnL、signal、candidate ranking。
- Required primary-source data cannot be obtained with provenance and effective dates。
- A proposed classification silently changes an existing research cohort。
- A geometry proposal requires relaxing the existing Project Kill Switch。
- A task would create a de facto new strategy before 2026-09-30 adjudication。

いずれかに該当した時点で停止し、推測や代替 performance proxy で埋めない。

## Review Outcome

未レビュー。Phase 0 の文書作成のみ完了。Phase 1〜4 はすべて未承認・未実施。
