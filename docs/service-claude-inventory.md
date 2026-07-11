# Service CLAUDE.md Inventory

最終確認: 2026-05-15

`services/*/CLAUDE.md` の棚卸しメモ。各ファイルはサービス別の設計意図を読む入口として有用だが、いくつかは初期実装フェーズ中の文章が残っている。実装状況の最新判断は `docs/HANDOFF.md`、テスト結果、実コードを優先する。

## Overview

| Service | CLAUDE.md の主な内容 | 棚卸しメモ |
|---|---|---|
| `universe-scanner` | J-Quants ingest、静的/動的フィルタ、`watchlist` 書き込み | 構成説明は有用。env は `SUPABASE_SERVICE_ROLE_KEY` 表記が残るが、現行コード/他 docs は `SUPABASE_SECRET_KEY` 寄り。2026-05-19 に paid cutover の batch 手動実行まで確認済み。 |
| `feeder` | kabu API 接続、PUSH parser、watchlist 登録、Pub/Sub publish | kabu 接続制約と token cache の注意が重要。古い本番リバプロ記述に nginx が残るが、引き継ぎ上の現方針は Windows 上の Caddy。`KABU_DEFAULT_EXCHANGE=1` 記述は feeder の登録用として扱い、OMS Live の発注 `Exchange=9` と混同しない。 |
| `feature-engine` | 指標計算、streaming、position price update、storage、PnL reset | 実装フェーズ説明は初期計画の名残。env に `SUPABASE_SERVICE_ROLE_KEY` 表記が残るが、現行実装は `SUPABASE_SECRET_KEY` を読む箇所がある。 |
| `strategy-rule` | ルール戦略 plugin、backtest、streaming、strategy logs | 「下流未実装」など初期計画文が残る。戦略パラメータと plugin 規約は今も参照価値あり。 |
| `strategy-ai` | LLM client abstraction、prompt/parser、fixture LLM、streaming | fixture 優先、実 LLM は手動オプションというコスト管理方針が重要。 |
| `aggregator` | A/B signal pairing、consensus、unified signal publish | 「下流未実装」など古い記述あり。pairing bucket と片側 signal fallback の設計メモは有用。 |
| `gateway` | risk validation、lot calculation、kill switch、routing | リスクルールの中心。`SELL` は既存 LONG 決済のみ、保有なし SELL reject の注意が重要。 |
| `oms-paper` | fill simulation、paper positions/trades、day closeout、swing monitor | Paper 検証の中心。二重約定回避は現行コードと contracts を確認してから変更する。 |
| `oms-live` | kabu sendorder、live positions/trades、closeout、Phase 3 e2e | 最重要。`KABU_ORDER_PASSWORD` 分離、`KABU_DEFAULT_EXCHANGE=9`、dry-run、安全装備、28080 本番 e2e の注意を必ず守る。 |

## Stale Or Conflicting Notes

- 複数サービスに「Phase 1/2/3 で今後実装」「下流サービス未実装」といった初期計画の文が残っている。2026-05-15 時点では 9 サービス + Dashboard は実装済み。
- `SUPABASE_SERVICE_ROLE_KEY` 表記が一部サービス文書に残っている。現行のローカル setup と多くのサービスは `SUPABASE_SECRET_KEY` を使う。変更時は対象サービスの `config.py` / `__main__.py` を正とする。
- Pub/Sub 件数は古い文書で固定値が残る。現行 SSOT は
  `infra/pubsub/topics.json` 9 件 / `infra/pubsub/subscriptions.json` 13 件。
- Feeder 文書の本番 reverse proxy は nginx 記述が残る。引き継ぎ上の現方針は Windows 上の Caddy reverse proxy。
- Universe Scanner の実装と paid cutover の手動実行確認までは完了。次の論点は日次自動化の起動方法と、`daily_ohlcv` の大きい upsert を前提にした運用時間の見積もり。

## Read Order By Task

- 本番デプロイ: `docs/adr/0001-deployment-architecture.md` → `docs/adr/0001-implementation-checklist.md` → `oms-live` / `feeder` / `gateway`
- Paper trading 調査: `scripts/start-paper-trading.sh` → `feature-engine` → `strategy-rule` / `strategy-ai` → `aggregator` → `gateway` → `oms-paper`
- kabu 接続調査: `feeder` → `oms-live` → `docs/runbook/oms-live-phase3.md`
- contracts/schema 変更: root `CLAUDE.md` → `contracts/` → 影響する service CLAUDE.md
- Dashboard 変更: `dashboard/CLAUDE.md` → `contracts/typescript/src/generated/database.types.ts`

## Recommendation

サービス別 `CLAUDE.md` は設計背景を読む資料として残し、最新状態は `docs/HANDOFF.md` とこの棚卸しメモで補正する。大きな機能変更を入れるタイミングで、触るサービスの `CLAUDE.md` だけを実コードに合わせて更新するのが安全。
