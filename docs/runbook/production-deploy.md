# Production Deploy Runbook

本番反映は GitHub Actions の `Deploy Production` workflow を正とする。
手元からは `scripts/deploy-production.sh` を使い、CI 確認、workflow 監視、
post-check までを一括で行う。

## 通常の本番反映

市場終了後など、kabu station / Windows proxy が停止していてよい時間帯:

```bash
bash scripts/deploy-production.sh --apply --kabu-offline
```

このコマンドが行うこと:

- `Deploy Production` workflow を `ref=main` / `dry_run=false` で起動する。
- workflow 内で対象 ref の CI success を確認する。
- production compose の検証と image build を行う。
- production services を `docker compose up -d --build` で再起動する。
- post-deploy に `docker compose ps` と `gateway` / `oms-live` の tail log を確認する。
- `scripts/production-preopen-check.py --timeout 30 --kabu-offline` を実行する。
  host 側の `/dev/shm/roboinvest/gcp-pubsub-sa.json` が読めない場合は、
  1Password から一時 credential を作って Pub/Sub check を継続する。

別の credential を使う特殊ケースだけ `--gcp-credentials <readable-host-path>` を渡す。
Pub/Sub smoke publish/pull/ack を避ける検証では `--no-pubsub-smoke` を追加する。

paper mode の反映・再起動で post-check も paper を期待する場合は
`--expected-trade-mode paper` を追加する。

成功条件:

- GitHub Actions run の conclusion が `success`。
- production compose の主要サービスが `Up`。
- post-check が `NG 0`。
- Supabase の `live positions` が意図しない状態でない。

## Dry Run

再起動せず、workflow 側の検証と build だけ確認する:

```bash
bash scripts/deploy-production.sh
```

これは `dry_run=true` で workflow を起動する。production services は再起動しない。

## kabu 接続ありの寄り前確認

kabu station / Windows proxy を起動済みの寄り前は、`--kabu-offline` を外す:

```bash
bash scripts/deploy-production.sh --apply
```

この場合、feeder / kabu connectivity の失敗は `WARN` ではなく `NG` として扱う。

deploy を伴わない寄り前チェックだけなら:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py --timeout 30
```

副作用なしで managed Pub/Sub の topic/subscription 存在確認までに留める場合は
`--no-pubsub-smoke` を追加する。

## 低出力運用

トークン節約のため、通常は `gh run watch` の全出力を貼らない。
agent は以下だけを報告すればよい。

- deploy run id と URL
- workflow conclusion
- post-check summary (`OK/WARN/NG/SKIP`)
- compose / logs に異常があった場合の要点

失敗時だけ `gh run view <run-id> --log-failed` などで詳細を追う。

## 注意

- `--apply` は `--ref main` のみ許可される。
- local working tree に未コミット差分があっても、deploy 対象は GitHub 上の committed ref。
- 本番資金に直結するため、`oms-live` / `gateway` 変更は unit test と CI success を確認してから反映する。
- pre-open / market hours の deploy は、ユーザー確認なしに行わない。
