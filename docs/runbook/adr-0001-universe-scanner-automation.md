# ADR-0001 Universe Scanner Automation

作成日: 2026-05-19

LAN host の production compose 構成で `universe-scanner` を日次実行するための運用手順。
`universe-scanner` 自体は常駐せず、host 側 systemd user timer から batch profile を 1 回起動する。

## 1. Decision

日次起動方式は次で固定する。

- 実行主体: LAN host の `systemd --user` timer
- 呼び出し口: `systemd` unit `roboinvest-universe-scanner.service` -> `bash scripts/run-production-universe-scanner.sh`
- 推奨時刻: `07:55 JST`
- 実行頻度: 平日毎日
- 休日の扱い: timer は平日のみ発火する。祝日でも起動しうるが、`universe-scanner` 本体が東証非営業日を検知して `watchlist_size=0` で正常終了する

`daily_ohlcv` の chunk upsert に数分かかることがあるため、寄り付き前に余裕を持たせて `07:55 JST` を推奨する。

## 2. Wrapper Script

ラッパーは `scripts/run-production-universe-scanner.sh` を使う。

役割:

- `infra/.op.service-account.env` の自動読込
- `infra/env.production` と
  `${GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH:-/dev/shm/roboinvest/gcp-pubsub-sa.json}`
  の存在確認
- `docker compose ... --profile batch config` の事前検証
- batch 実行後に当日 `watchlist` から `infra/env.production` の `OMS_LIVE_ALLOWED_SYMBOLS` を同期
- `oms-live` が稼働中なら、同期後に `oms-live` だけ再作成して env を反映
- batch 実行前後の `health-check.py --check supabase`
- `/tmp/roboinvest-universe-scanner.lock` による重複起動防止

手動実行:

```bash
cd /home/hiroyuki/workspaces/roboinvest
bash scripts/run-production-universe-scanner.sh
```

`/dev/shm/roboinvest/gcp-pubsub-sa.json` が実ファイルとして使えない host では、
代替 credential を `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH` で指定して実行する。
ラッパーは Docker Compose 呼び出し時にその値を明示的に渡す。

```bash
GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=/tmp/roboinvest-gcp-pubsub-sa.json \
  bash scripts/run-production-universe-scanner.sh
```

特定日の再実行:

```bash
bash scripts/run-production-universe-scanner.sh --date 2026-05-19
```

`OMS_LIVE_ALLOWED_SYMBOLS` の同期を避ける場合:

```bash
bash scripts/run-production-universe-scanner.sh --skip-oms-live-sync
```

イメージ build を含める場合:

```bash
bash scripts/run-production-universe-scanner.sh --build
```

## 3. systemd Timer

host の timezone は `Asia/Tokyo` を前提にする。user unit/timer は repo 配下の次を使う。

- `infra/systemd/roboinvest-universe-scanner.service`
- `infra/systemd/roboinvest-universe-scanner.timer`
- `scripts/install-universe-scanner-timer.sh`

導入:

```bash
cd /home/hiroyuki/workspaces/roboinvest
bash scripts/install-universe-scanner-timer.sh
```

確認:

```bash
systemctl --user status roboinvest-universe-scanner.timer
systemctl --user list-timers roboinvest-universe-scanner.timer --all
journalctl --user -u roboinvest-universe-scanner.service -n 100 --no-pager
```

補足:

- timer は `OnCalendar=Mon..Fri 07:55:00 Asia/Tokyo`
- `Persistent=true` のため、停止中に取り逃した平日実行は次回起動時に catch-up される
- 1Password service account token は `infra/.op.service-account.env` から読み込む
- ログはファイルではなく user journal で確認する
- reboot 後も login 前から動かすなら `loginctl enable-linger hiroyuki` が必要

## 3.5 Current Host Status

2026-05-26 JST 時点の確認済み事項:

- `bash scripts/install-universe-scanner-timer.sh` 実行済み
- `systemctl --user status roboinvest-universe-scanner.timer` は `enabled` / `active (waiting)`
- `loginctl show-user hiroyuki --property=Linger` は `Linger=yes`
- `systemctl --user start roboinvest-universe-scanner.service` は `status=0/SUCCESS` で完走
- 初回の service 起動では `uv` が `PATH` になく失敗したため、service に `Environment=PATH=/home/hiroyuki/.local/bin:/usr/local/bin:/usr/bin:/bin` を追加して修正済み
- 定時発火は観測済み。`2026-05-25 07:55 JST` は `done: valid_date=2026-05-25 watchlist_size=30`、`2026-05-26 07:55 JST` も timer から起動し、`07:59:39 JST` に正常終了した。2026-05-26 は手動実行後の逐次実行であり、同時二重起動ではない。

定時発火の確認コマンド:

```bash
journalctl --user -u roboinvest-universe-scanner.service -n 100 --no-pager
systemctl --user status roboinvest-universe-scanner.timer --no-pager
systemctl --user list-timers roboinvest-universe-scanner.timer --all --no-pager
```

## 4. Success Criteria

成功時に最低限確認したいログ:

```text
done: valid_date=YYYY-MM-DD watchlist_size=N
=== YYYY-MM-DD HH:MM:SS JST completed ===
```

`watchlist_size` は通常 `20-50` を想定する。非営業日は `watchlist_size=0` でも正常。
非営業日や `watchlist_size=0` の場合、`OMS_LIVE_ALLOWED_SYMBOLS` の同期は skip される。

## 5. Failure Handling

失敗時はまず timer / service の状態を確認する。寄り付き前チェックを `07:55 JST` より前に始めた場合、手動実行する前に timer の次回発火を待つか、明示的に止めるかを決める。

```bash
systemctl --user status roboinvest-universe-scanner.timer --no-pager
systemctl --user list-timers roboinvest-universe-scanner.timer --all --no-pager
journalctl --user -u roboinvest-universe-scanner.service -n 100 --no-pager
```

当日分が未実行、または失敗していることを確認してから手動で再実行し、どこで落ちるかを分ける。

```bash
cd /home/hiroyuki/workspaces/roboinvest
bash scripts/run-production-universe-scanner.sh
```

見る順序:

1. `infra/.op.service-account.env` が読めるか
2. `op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml --profile batch config` が通るか
3. `op run --env-file infra/env.production -- uv run python scripts/health-check.py --check supabase --timeout 30` が通るか
4. `done: valid_date=... watchlist_size=...` が出たか

注意:

- 2026-05-26 の寄り前チェックでは `07:55 JST` の timer 発火前に手動実行してしまい、その後 timer も予定通り起動した。手動実行は `07:51:09 JST` に完了し、timer は `07:55:08 JST` に起動して `07:59:39 JST` に正常終了したため、同時二重起動ではない。lock は同時起動防止であり、完了後の同日2回目実行は防がない。
- `scripts/run-production-universe-scanner.sh` は成功後に `OMS_LIVE_ALLOWED_SYMBOLS` を当日 watchlist へ同期し、`oms-live` が起動中なら recreate する。live env (`TRADE_MODE=live` / `OMS_LIVE_DRY_RUN=false`) では実運用の副作用として扱うこと。
- Codex 等の sandbox から直接実行すると `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` で失敗することがある。その場合は sandbox 外で実行する。

失敗後に紙運用を進める場合は、`docs/runbook/paper-open-checklist.md` に戻って手動確認に切り替える。

## 6. Relationship To Manual Open Procedure

寄り付き前に人間が操作する日は、引き続き `docs/runbook/paper-open-checklist.md` の手順を優先してよい。
systemd timer 自動化は Universe Scanner の先回り実行を置き換えるだけで、paper services の起動や寄り付き後監視までは自動化しない。
