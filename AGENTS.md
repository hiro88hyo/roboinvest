# AGENT.md

Codex / coding AI 向けの作業メモ。Claude Code からの引き継ぎ資料
`docs/HANDOFF.md` とルート `CLAUDE.md` を読んだ上での理解をまとめる。

## Project Overview

このリポジトリは、日本国内の現物株（auカブコム証券）を対象とした
自律型トレードシステム。ルールベース戦略と LLM/AI 戦略を組み合わせ、
Google Cloud Pub/Sub で疎結合にした Python マイクロサービス群と
Next.js Dashboard で構成されている。

主要コンポーネントは以下の 9 サービス + Dashboard。

- `services/universe-scanner`: J-Quants から監視銘柄を生成する日次バッチ
- `services/feeder`: kabu.com API から market data を取得して Pub/Sub に流す
- `services/feature-engine`: Tick/板情報からテクニカル指標を計算する
- `services/strategy-rule`: ルールベースの `StrategySignal` を生成する
- `services/strategy-ai`: LLM/AI ベースの `StrategySignal` を生成する
- `services/aggregator`: Strategy A/B のシグナルを統合する
- `services/gateway`: リスク検証と live/paper ルーティングを担当する
- `services/oms-live`: kabu.com API へ実発注し live 約定を記録する
- `services/oms-paper`: 板情報を使って paper 約定をシミュレートする
- `dashboard`: Supabase Realtime を使う Next.js ダッシュボード

## Current State

2026-05-15 時点の引き継ぎでは、ローカル paper trading と OMS Live Phase 3
までは完成している。

- 全 9 サービス + Dashboard 実装済み
- Paper エンドツーエンドは 4 ラウンドと 14:50 closeout を観測済み
- OMS Live Phase 3 は本番 28080 / 実発注 e2e の 4 ケースが PASSED
- リスク管理は 2% ルール、キルスイッチ、day closeout、swing 自動決済が稼働
- CI は python / dashboard / e2e の 3 job、Dependabot、coverage が緑

未着手または次の山:

- ADR-0001 の本番デプロイ実装
- J-Quants 有料プラン移行後の Universe Scanner 本番自動化
- 24/7 運用整備（監視、ログ集約、アラート、バックアップ）

## First Files To Read

作業開始時は、最低限これらを確認する。

1. `CLAUDE.md`: アーキテクチャ、Pub/Sub、Supabase、リスクルール、規約
2. `docs/HANDOFF.md`: 最新の引き継ぎメモ
3. 対象サービスの `services/<name>/CLAUDE.md`
4. `contracts/`: Pydantic / SQL / TypeScript の Single Source of Truth
5. `git status --short` と直近の履歴

## Core Principles

- `contracts/` を Single Source of Truth とする。スキーマ変更はここから始める。
- サービス間の直接通信は禁止。連携は Pub/Sub 経由にする。
- Gateway がリスクルールを単独で執行する。他サービスへ判断を分散しない。
- OMS Live は本番資金に直結するため、変更は最小限にし、先に OMS Paper で検証する。
- 純関数ロジックとストリーミング層を分離する。
- 既存のサービスごとの設計、fixture、テスト命名規約に合わせる。

## Common Commands

```bash
make lint-all
make test-all
bash scripts/start-paper-trading.sh
./scripts/gen-supabase-types.sh
uv run python scripts/health-check.py
```

Python は `uv` を使う。`pip` や `poetry` の直叩きは避ける。
Dashboard は Volta 管理の Node/npm を使う。

## Health Check Notes

`scripts/health-check.py` はローカル開発環境の軽量スモークテスト。
`uv run scripts/health-check.py` または対象を絞って `uv run scripts/health-check.py --check pubsub supabase` のように使う。

検査内容:

- Pub/Sub: `infra/pubsub/topics.json` の 7 topics と `infra/pubsub/subscriptions.json` の 9 subscriptions が emulator 上に存在するか確認する。
- Supabase: `system_status`, `positions`, `strategy_logs`, `aggregator_logs`, `trades_live`, `trades_paper`, `watchlist`, `master_stocks`, `daily_ohlcv` の 9 tables を PostgREST で `limit=0` read できるか確認する。
- Services: 9 service modules (`universe_scanner`, `feature_engine`, `strategy_rule`, `strategy_ai`, `aggregator`, `gateway`, `oms_paper`, `oms_live`, `feeder`) の `python -m <module> --help` が起動できるか確認する。

必要 env がないセクションは `SKIP` で、失敗扱いではない。`NG` が 1 件でもあれば exit code 1。
Pub/Sub は `PUBSUB_EMULATOR_HOST` / `PUBSUB_PROJECT_ID`、Supabase は `SUPABASE_URL` / `SUPABASE_SECRET_KEY` が必要。

## Important Operational Notes

- Pub/Sub topics は 7 件、subscriptions は現行 `infra/pubsub/subscriptions.json` で 9 件が前提。
- Pub/Sub emulator は長時間稼働で OOM することがある。再起動後は topic/subscription の再 seed が必要。
- 市場開始前の主な失敗要因は subscription 未作成、`daily_ohlcv` 空、`watchlist` 未更新。
- kabuステーションは localhost 限定。本番は Windows 上の Caddy reverse proxy と SSH tunnel 前提。
- 本番 kabu.com API では SOR 必須で、`KABU_DEFAULT_EXCHANGE=9` が前提。
- 検証環境 18081 は `sendorder` を黙殺するため、実発注 e2e は本番 28080 のみ。
- Feeder と OMS Live は kabu token を `KABU_TOKEN_CACHE_FILE` で共有する。
- OMS Live は `KABU_API_PASSWORD` と `KABU_ORDER_PASSWORD` を別 env として読む。
- `OMS_LIVE_DRY_RUN=true` が `.env` に残っていることがあるので live 検証時は必ず確認する。

## Supabase And Contracts Notes

- `positions` は OMS Live 単一プロセス前提で非アトミック。複数プロセス化するなら Postgres RPC が必要。
- kabu 実保有との乖離は `scripts/reconcile-positions.py` で照合する。
- closeout 由来の `unified_signal_id` は `None` になり得る。
- TypeScript の Supabase 型は手動編集せず、`./scripts/gen-supabase-types.sh` で再生成する。

## Test And Lint Conventions

- 新サービスで `tests/conftest.py` を作らない。
- fixture は `src/<service>/_testing.py` に置く。
- `tests/__init__.py` は作らない。
- テストファイル名は `test_<service>_*` プレフィックスで衝突を避ける。
- `strategy-ai` の `--fixture-responses` は、文字列化された JSON object 配列が必要。

## Strategy Parameters To Preserve

- `RSI_BUY_THRESHOLD=25`
- `RSI_SELL_THRESHOLD=75`
- `SMA min_gap_ratio=0.005`
- `Bollinger tolerance=0.15`

テストや調査で緩めた場合は戻し忘れに注意する。

## Known Working Tree Note

引き継ぎ確認時点では `docs/HANDOFF.md` が未追跡だった。
ユーザーまたは Claude Code が作成したファイルとして扱い、明示依頼なしに削除・巻き戻ししない。

## Codex Environment Note

この環境では通常の sandbox 実行が `bubblewrap is unavailable` で失敗した。
必要な読み取り・検証コマンドは、ユーザー承認付きの escalated 実行が必要になる場合がある。
