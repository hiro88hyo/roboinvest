# Handoff Memo (for coding AIs)

最終更新: 2026-05-29 / HEAD: `main`

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

2026-05-29 時点の要点:

- 全 9 サービス + Dashboard は実装済み。
- production compose / Cloud Supabase / managed Pub/Sub / Vercel Dashboard は一通り稼働済み。
- Live trading は 2026-05-21 から 2026-05-29 まで運用実績あり。
- 5月 live 成績は合計 `+46,766円`、ただし 2026-05-29 は `-45,540円` の大きな負け。
- Paper trading は 2026-05-19 から 2026-05-21 まで確認済みで、合計 `+68,100円`。
- 2026-05-29 に `AI_MAX_OUTPUT_TOKENS=256` が思考型 Gemini の JSON 出力を潰していた可能性を特定。`2048` で JSON 出力確認済み。

長い時系列ログ:

- [docs/handoff/2026-05-operations-log.md](handoff/2026-05-operations-log.md)

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

1. **AI 戦略の復旧を production で確認する**
   - PR #66 で `AI_MAX_OUTPUT_TOKENS` のデフォルトを `2048` へ変更済み。
   - production 再起動後、`strategy-ai` の JSON 生成、parser、signal publish、`strategy_logs` / `aggregator_logs` 反映を確認する。

2. **寄り付き直後の live BUY guard を観測する**
   - 5/29 の損失は 09:00-09:05 の急変動エントリーが大きい。
   - PR #66 で `gateway` の live/day 新規 BUY は 09:15 JST より前に `opening_live_buy` で reject するよう変更済み。
   - 数営業日、reject reason 分布、09:00-09:15 の missed profit / avoided loss を観測する。

3. **Aggregator の source 別 confidence threshold を観測する**
   - RULE / AI 単独シグナルは `0.5`、RULE+AI consensus は `0.3` を下限にする方針。
   - 弱い RULE 単独通過を減らしつつ、AI 復旧後の consensus は落としすぎない狙い。

4. **保有時間制限を検討する**
   - 15分以内の決済が利益の大半を稼ぎ、60分超は勝率が落ちている。
   - 45分前後の time-based closeout を候補にする。

5. **carry / closeout の堅牢化を続ける**
   - closeout 後に live position が残る場合は `CRITICAL` ログまで実装済み。
   - 通知系、Dashboard 明示表示、翌営業日 pre-open 手順の強化は継続課題。

6. **損切り exit / Universe Scanner 改善は別実験に分ける**
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
op run --env-file infra/env.production -- uv run python scripts/health-check.py --check supabase --timeout 30
op run --env-file infra/env.production -- docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml ps
```

## 7. Test And Lint Conventions

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
