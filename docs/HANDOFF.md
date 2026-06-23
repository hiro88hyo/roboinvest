# Handoff Memo (for coding AIs)

最終更新: 2026-06-23 / HEAD: `main` (`a662f82`)

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

2026-06-23 時点の要点:

- 全 9 サービス + Dashboard は実装済み。
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

0. **2026-06-23 strategy reset: 既存 intraday strategy は live 候補から外す**
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
