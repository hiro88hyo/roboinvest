# Portfolio Researchability Reset Phase 1: Missingness Audit

Date: 2026-08-08  
Reset: `portfolio_researchability_reset_2026_v0`  
Decision: `NO_GO_CURRENT_ARCHIVE_FOR_ALL_UNIVERSE_CROSS_SECTIONAL_RESEARCH`

## 結論

現在の J-Quants archive は、全銘柄型の monthly cross-sectional 研究に使わない。

Gate A で欠けた 57 symbol outcomes は、23 形成月について完全に再現できた。しかし
57 件すべてで、exact outcome month-end の日次 bar source row と、同日付の同一 code
historical master row がともに存在しなかった。日付単位の取得 batch、receipt、正規化
partition はすべて存在し、raw/normalized row count と hash も整合したため、archive
全体の取得漏れや正規化脱落ではない。

一方、bound archive には delisting、merger、share exchange、cash consideration、
issue-code lineage、code reuse を明示する event dataset がない。銘柄の消失だけから
terminal event を推定することは事前固定した分類規則で禁止したため、57 件すべてを
`UNKNOWN` とした。`UNKNOWN >= 1` かつ point-in-time terminal-outcome contract を
再現できないため、Phase 1 は NO-GO で終了する。

これは IMOM の一般的な不存在を主張する結果ではない。現在の archive では、その種の
仮説を survivor/terminal bias なしに検定できないという data-researchability 判定である。

## 実測結果

| 項目 | 件数 |
|---|---:|
| Gate A attempted formation months | 28 |
| 欠損を含む formation months | 23 |
| 欠損 case | 57 |
| formation exact endpoint が有効 | 57 |
| outcome exact bar source row が存在しない | 57 |
| outcome historical master に同一 code が存在しない | 57 |
| outcome 後に同一 code の有効 bar が再出現 | 1 |

分類内訳:

| Frozen reason | Count |
|---|---:|
| `DELISTING_OR_TERMINAL_EVENT` | 0 |
| `TRADING_SUSPENSION_OR_NO_MONTH_END_TRADE` | 0 |
| `ISSUE_CODE_OR_SECURITY_LINEAGE_CHANGE` | 0 |
| `CORPORATE_ACTION` | 0 |
| `HISTORICAL_MASTER_PRODUCT_CATEGORY_MISMATCH` | 0 |
| `SOURCE_API_OR_PLAN_COVERAGE_LIMITATION` | 0 |
| `ARCHIVE_FETCH_OR_INGEST_FAILURE` | 0 |
| `UNEXPLAINED_SOURCE_DATA_ABSENCE` | 0 |
| `UNKNOWN` | 57 |

同一 code の後年再出現は code `83030` の 1 件で、欠損 outcome は 2023-09-29、
archive 上の次の有効 bar は 2025-12-17 だった。この事実だけでは、同一 security の
取引再開、code reuse、または lineage change のいずれかを決められない。したがって
後日の bar を outcome として代用せず、分類も `UNKNOWN` のままとした。

## Authority と非性能境界

Phase 1 authorization は 57 case の code を読む前に、入力 hash、分類優先順位、
NO-GO 条件を固定した。監査は既存 archive の次だけを扱った。

- exact row の存在、null/positive state
- historical master membership と product-category continuity
- source fetch ID、receipt timestamp、partition identity と SHA-256
- nearest prior/later valid-bar date（値は保存しない）

価格値、価格比、symbol/portfolio return、decile performance、rank IC、signal、trade、
PnL、PF、DD は計算・保存していない。Gate A の再計算、complete-case 化、validation、
locked OOS、外部 API fetch、paper/live 変更も行っていない。

## Terminal-outcome contract の不足

現在の archive が保持する adjustment factor と月末 listed-info snapshot だけでは、
次を point-in-time に再現できない。

- delisting / merger / share exchange / code change の event type と effective date
- cash や新 security を受け取る terminal consideration
- old code と successor security の明示 lineage
- code reuse の分離
- delisting return を含む一貫した terminal outcome

これらの source を追加できる可能性の調査は Phase 1 の権限外であり、実施していない。
追加取得を行う場合は、source identity、PIT availability、total-outcome policy、入力 hash
を固定した別 authorization が必要である。Phase 2 以降も未承認である。

## Execution note

実データ command の最初の試行は、artifact write 前の禁止キー検査が分類名
`TRADING_SUSPENSION_OR_NO_MONTH_END_TRADE` の `TRADE` を performance field と誤認し、
停止した。禁止対象を `trade_pnl` 等の具体的な性能キーへ狭め、分類規則、bound input、
case reconstruction を変えずに再実行した。実行試行は 2 回、artifact write は 1 回で
ある。この非性能 rerun を隠さず completion record にも残す。

## Artifacts and verification

| Artifact | SHA-256 |
|---|---|
| Phase 1 authorization | `88ea378f5080194586420f9842be0700b5c89a46458a2ca152e624d28ca7fc6a` |
| Auditor | `7644dc00b12b9a6e1597f459960778b9dc0d9aea49dcaf4289848fd9b173ab20` |
| Synthetic tests | `817eb92bfa45c4bfb6f4b9d4ff4820364deb601117d3e9e36d12ec54145787ff` |
| Missingness audit | `6a6ef8bd793f8de12720f187cbb9a23adadad479d6e67587d102e51cf38f7b18` |
| Run manifest | `6a462ea6344f368f660b318dd57e16a7855abdad385e74bb579118065b1aa002` |

Independent JSON consistency checks passed: 57 case rows, 23 monthly summaries, reason-count
reaggregation, and all 57 valid formation endpoints matched the artifact. Repository verification
passed with `make lint-all`, 1,653 Python tests passed / 29 environment-dependent skips at 86%
coverage, and 47 Dashboard tests passed.

この結果は 2026-09-30 Project Kill Switch の evidence ではなく、その期限、PF/DD 条件、
cohort、現行 paper/live 方針を変更しない。
