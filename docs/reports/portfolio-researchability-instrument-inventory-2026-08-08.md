# Portfolio Researchability Instrument Inventory — 2026-08-08

## Decision

Phase 2 の状態は `COMPLETE_NO_PERFORMANCE_USED`、最終判定は
`NO_GO_PHASE3_CURRENT_INSTRUMENT_DATA_FOUNDATION`。

2026-06-30 の point-in-time 銘柄マスターと、2026-08-08 に保存した公式一次資料を
交差し、収益・価格・signal・順位を使わずに 53 instrument を inventory 化した。
商品候補は存在するが、全 class で以下が未完備であるため Phase 3 へ進めない。

1. entry から exit / termination / security lineage までの一貫した PIT contract
2. cash distribution を含む total-outcome contract
3. 過去の quoted spread、板 depth、寄り・引け auction の観測
4. 各商品の kabu K1〜K4 compatibility gate
5. REST/PUSH共通の登録上限50に対する同時監視・auction観測計画

この NO-GO はデータ基盤の判定であり、商品収益性や推奨を意味しない。Project Kill
Switch の期限・PF・DD 条件を変更せず、Phase 3、strategy、paper、live を許可しない。

## Bound Population

- snapshot: `2026-06-30`
- historical-master ETF (`product_category=014`): 412
- ordinary equity baseline (`product_category=011`): 3,899（集計のみ。候補列挙なし）
- official-source classified instruments: 53
- three definitions に該当しない snapshot ETF: 359
- descriptor window: 2026-04-02〜2026-06-30 の直近 60 TSE sessions
- OHLCV から読んだ列: `date`, `code`, `turnover_jpy` のみ

JPX の current page を snapshot membership の代用にはしていない。公式ページの行を
同日 historical master に exact code で交差し、snapshot 後の current membership を
backfill しなかった。

## Inventory Result

| Class | Members | 60 source rows | Current methodology doc | PIT methodology / benchmark | Decision |
|---|---:|---:|---:|---:|---|
| JPX Japanese Equity (Market) category ETF | 34 | 34 | 0 | 0 | NO-GO |
| Japanese equity sector / industry index | 18 | 18 | 17 | 0 | NO-GO |
| JPY Japan Government Bond 0-1Y ETF | 1 | 0 | 0 | 0 | NO-GO |

`60 source rows` と `60 non-null turnover` は instrument 数である。570A は
2026-05-27 上場のため、snapshot まで 25 source rows / 25 non-null turnover
rows であり、60 行未満は欠測代替をしていない。

### JPX Japanese Equity (Market) category ETF

[JPX Japanese Equity (Market) ETF list](https://www.jpx.co.jp/english/equities/products/etfs/issues/01-01.html)
を公式 source category 境界とした。active、thematic、style、dividend、minimum
variance、covered call、currency、commodity、bond、leveraged、inverse の明示 token
を先に除外し、snapshot と交差した 34 行を全件収録した。費用、売買代金、上場期間に
よる選別はない。

この JPX category 名を broad-market exposure の証拠には使わない。全 34 商品で完全な
公式 index methodology と version が未固定なので、economic exposure と portfolio role
は全件 `CLASSIFICATION_PENDING`。将来許される role は `BROAD_MARKET_CORE`、
`LARGE_CAP_MARKET_PROXY`、`SECTOR_EXPOSURE`、`SELECTED_MARKET_INDEX`、
`CLASSIFICATION_PENDING` だが、価格・return・性能を根拠に割り当てない。

### Japanese equity sector / industry index

[JPX Japanese Equity (Sector) ETF list](https://www.jpx.co.jp/english/equities/products/etfs/issues/01-03.html)
の non-geared 18 行を全件収録した。内訳は TOPIX Banks の 1615 と TOPIX-17 の
1617〜1633 である。TOPIX-17 の 17 商品は
[TOPIX-17 methodology](https://www.jpx.co.jp/english/markets/indices/line-up/files/e_cal2_13_sector.pdf)
へ class-level に結合できた。economic classification は
`INDUSTRY_SECTOR / TOPIX_17 / CONFIRMED`、portfolio role は `SECTOR_EXPOSURE` とした。
これは `MARKET_SEGMENT` ではない。ただし保存したcurrent documentだけでは全期間の
methodology version coverageやbenchmark lineageを証明できず、Phase 3 eligibleではない。

1615は `INDUSTRY_SECTOR / TOPIX_33_SECTOR / BANKS / CONFIRMED` であり、銀行sectorか
どうかはpendingではない。一方、portfolio roleは `CLASSIFICATION_PENDING`、
`methodology_status=PIT_INCOMPLETE`、`benchmark_lineage_status=INCOMPLETE` と分離した。
「何へのexposureか」と「Phase 3で使用可能か」を同一フィールドで表現しない。

### JPY Japan Government Bond 0-1Y ETF

該当したのは 570A の 1 商品だった。
[JPX 570A product description](https://www.jpx.co.jp/equities/products/etfs/issues/files/570A-j.pdf)
は FTSE 日本国債 0-1 年指数、残存期間 1 年未満、上場予定日 2026-05-27、売買単位
10 口を明記している。2026-06-30 historical master にも `570A0` が存在したため収録した。

円ドル long overlay を含む 488A、1 年を超える債券、海外債券、名称だけが「超短期」の
商品は含めなかった。570A の legal type は ETF、economic exposure は
`JPY_JAPAN_GOVERNMENT_BOND_0_1Y`、portfolio role は `CASH_PROXY_CANDIDATE` とする。
`settlement_cash=false`、`strict_cash_equivalent=false`、
`cash_proxy_status=UNVALIDATED` であり、現金や検証済み cash equivalent として扱わない。

## Data And Operational Coverage

53 商品すべてで code、ETF product type、listing date、tracked index、trading unit、
trust-fee source text、historical-master coverage、daily source-row date range を記録した。
[JPX ETF trading rules](https://www.jpx.co.jp/equities/products/etfs/trading/index.html)
から ETF の generic tick / daily-price-limit treatment を結合したが、Phase 2 は価格を
読まないため商品別 tick amount や limit amount は計算していない。

ローカル archive は adjustment factor を持つが、cash distribution event、明示的な
split/consolidation event、redemption/delisting、security-code lineage を一貫して結ぶ
event table を持たない。[J-Quants](https://jpx-jquants.com/) の current service page が
配当・分割データを案内していても、今回の bound archive に含まれないデータを
「利用可能」とは扱わなかった。current
[JPX delisting reference](https://www.jpx.co.jp/equities/products/etfs/delisting/index.html)
の保存だけでも過去全期間の PIT termination contract にはならない。

過去の quoted spread、板 depth、opening/closing auction book は bound archive にない。
turnover median は保存したが、選択・順位付けには使っていない。

## kabu API Boundary

[official kabu station OpenAPI 1.5](https://kabucom.github.io/kabusapi/reference/kabu_STATION_API.yaml)
で generic `symbol`、`board`、`register` / `unregister`、cash `sendorder` と最大 50 銘柄の
登録制約を確認した。[official service page](https://kabu.com/item/kabustation_api/default.html)
も provenance に固定した。

一方、認可どおり localhost の本番・検証 endpoint は呼んでいない。互換性は次の gate
へ分割し、K1〜K4を Phase 3 前の必須条件、K5A/K5Bを paper 後・live 前の別認可事項とした。

| Gate | Scope | Current status | Phase 3 prerequisite |
|---|---|---|---|
| K1 | production `/symbol`、商品別 | NOT_VERIFIED | required |
| K2 | production `/board`、`/register`、PUSH、商品別 | NOT_VERIFIED | required |
| K3 | validation `/sendorder` schema、凍結済み全 order profile | NOT_VERIFIED | required |
| K4 | 商品・口座別 cash order / SOR eligibility | NOT_VERIFIED | required |
| K5A | production submit / cancel、意図的fillなし | NOT_STARTED_NOT_AUTHORIZED | live前・別認可 |
| K5B | minimum-lot execution / exit | NOT_STARTED_NOT_AUTHORIZED | live前・別認可 |

公式仕様上の制約はPUSH専用ではなく、REST/PUSH共通のAPI登録銘柄上限50である。
53商品のstatic inventoryや、登録解除・入替を伴うsequential compatibility verificationは
妨げない。一方、53商品のsimultaneous runtime monitoringと寄り・引けauction observation
にはhard constraintとなる。上限拡大や単純rotationの妥当性を仮定せず、価格・return・
performanceを見ず50以下のsubsetを凍結する、代替market-data sourceを承認する、または
要求を満たすrotationを証明する必要がある。

## Correction Record

最初の successful artifact は sector page 18 行のうち TOPIX-17 の 17 行だけを収録し、
TOPIX Banks 1615 を誤って落とした。これは価格・収益・turnover を根拠にした変更ではなく、
固定済みの「every unlevered sector or industry row」と公式ページ行数の不一致から検出した
実装欠陥である。

- correction record: `research/portfolio-researchability-reset-2026-v0/phase2-correction-record.json`
- invalid inventory SHA-256: `81203c276fe3388aad25c3d23fe685195a9efb3ee9d9c6afcc3d7eceeff1b86a`
- invalid run manifest SHA-256: `9c4695169e99c5a50bd08a819cac945a1e69239c3b2552e54590d47c3051e799`
- quarantine: `out/portfolio-researchability-reset-2026-v0/phase2-instrument-inventory-v0-invalid-sector-page-coverage`
- status: `INVALID_NOT_PHASE2_EVIDENCE`

訂正版は 1615 を追加し、その methodology を未完備のまま記録した。誤版は削除せず、
監査可能な形で隔離した。

その後の semantic review で、JPX の source category を broad-market exposure と同一視し、
570A を strict cash-equivalent class に置いた意味付けが過大だったと判明した。さらに kabu
compatibility を単一の `quote / board / order route` 状態にまとめていたため、Phase 3
前後の確認境界が不十分だった。母集団・商品数・一次資料・snapshot は変えず、価格・収益・
turnover・証券 endpoint を使わない第2訂正を実施した。

- semantic correction record:
  `research/portfolio-researchability-reset-2026-v0/phase2-semantic-correction-record.json`
- advisory review:
  [ChatGPT Pro shared response](https://chatgpt.com/s/t_6a77a836a6048191a6da824c7bca5971)
- superseded inventory SHA-256:
  `43e845456fbcdfeef112f0f96bdc6cdc98dbea99123bf6a3738b5d44ff588bca`
- superseded run manifest SHA-256:
  `a5fa8237f2d0964bd683704fcd86c518075afa0127d858e8fbb60469c11cc241`
- quarantine:
  `out/portfolio-researchability-reset-2026-v0/phase2-instrument-inventory-v0-superseded-semantic-classification`
- status: `SUPERSEDED_NOT_CURRENT_PHASE2_EVIDENCE`

第2訂正版に対する
[GPT Pro review](https://chatgpt.com/s/t_6a77b85aec048191abd3c4b3e2fc3258)
は `ACCEPT_WITH_REQUIRED_SEMANTIC_CORRECTIONS` と判定した。TOPIX-17のsector role、
1615のeconomic classificationとlineage statusの混同、登録上限のPUSH限定表現を、
母集団・一次資料・価格・収益・broker endpointを変えず第3訂正した。

- postreview correction record:
  `research/portfolio-researchability-reset-2026-v0/phase2-postreview-semantic-correction-record.json`
- superseded v2 inventory SHA-256:
  `a7bbddf3f96c8d0599fca7a22769ba745d962fc7ceb5876418776be575f105bf`
- superseded v2 run manifest SHA-256:
  `08f2f6b27eb6994a4299abfda7ac50ef7e5cf26d5e0844c432fd1a24bb16f02e`
- quarantine:
  `out/portfolio-researchability-reset-2026-v0/phase2-instrument-inventory-v0-superseded-postreview-semantic-v2`
- `eligible_for_active_manifest=false`
- `eligible_for_phase3_input=false`
- status: `SUPERSEDED_NOT_CURRENT_PHASE2_EVIDENCE`

## Final Artifacts And Integrity

- authorization SHA-256: `34c87708fbebda02e558c2657246067325579219e205d8fbda774234f5075837`
- source manifest SHA-256: `b557026b2289be65e1e29095a14ab7e57db31a8b9c2c756b574e1956869869ab`
- semantic correction SHA-256: `5807ffbad8ef0f1c24caa2735a1598fbbdc88d5495900b7ed6f2fc78478e6547`
- postreview semantic correction SHA-256:
  `b902d18177d2752874b73cf34ebb83d11b029487502346c59ee882702b3c9e3e`
- corrected builder SHA-256: `286bdc7e3a9281e31c187417020cf55d18b216b59054c18b214937f6b776787d`
- corrected synthetic tests SHA-256: `7f804d604d17672f37daf54eeb5d931031056246025eab5e8342e9de293bea42`
- final inventory SHA-256: `53ffa04614d7916954ea2a6aa7a4bad71f62bf88f216a9f6bac60e3d68acff0d`
- final run manifest SHA-256: `61061ab6d2672f66e292b8c78ec4ed067d4c0e4fde150bb8c7dcfc67201bd4fc`

一次資料 13 件、normalized bars 59 partitions、historical master 59 partitions を SHA-256
で検証した。最終 inventory は価格列を読まず、return、signal、PF、DD、PnL、score、rank、
recommendation、portfolio weight を作成していない。

## Verification

- corrected Phase 2 synthetic tests: 26 passed
- `make lint-all`: passed
- full Python suite: 1,679 passed, 29 skipped
- Dashboard: 47 passed

## Authority Boundary

Phase 2 は `COMPLETE_NO_PERFORMANCE_USED` で停止する。Phase 3 の 2M JPY portfolio
geometry、商品選択、strategy candidate、return 計算、paper/live 接続は未承認である。
Phase 3 を再検討するには、PIT lineage、distribution/redemption 込み total outcome、
商品別 methodology/version、historical FLEX または `FORWARD_ONLY` の microstructure
mode、K1〜K4、REST/PUSH共通登録上限への運用計画を解消し、別の明示認可を得る。
Phase 3 scopeは `STATIC_LOT_GEOMETRY`、`LOOKTHROUGH_PORTFOLIO_GEOMETRY`、
`EXECUTION_AWARE_FEASIBILITY` のいずれかを先に固定する。benchmark lineage、methodology
version gap、必要な場合のPIT look-through、K4 evidence expiry、execution data mode、
venue/SOR policy、REST/PUSH共通登録上限計画、source provenanceをすべてfail-closedで扱い、
superseded成果物はactive pathから除外する。K5A/K5BはPhase 3 gateではなく、paper後・
live前の別認可必須gateである。いずれも2026-09-30 Project Kill Switch判定を先送りしない。
