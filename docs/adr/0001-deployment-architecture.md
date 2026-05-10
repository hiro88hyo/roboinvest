# ADR-0001: 本番デプロイアーキテクチャ

- Status: Accepted
- Date: 2026-05-10

## Context

Trade AI Agent を本番運用に乗せるにあたり、以下の制約がある:

- kabu API が Windows 機 (LAN 内固定 IP) の `localhost` 限定 (`http.sys` URL ACL 制約、memory `kabu_localhost_only.md` 参照)。Caddy リバプロで LAN ポート 28080 から到達可能だが、外部からの直接アクセスは別途トンネルが必要。
- 個人運用前提で、月額コストと保守工数を最小化したい。
- 9 個の長時間 asyncio サービス (universe-scanner / feeder / feature-engine / strategy-rule / strategy-ai / aggregator / gateway / oms-paper / oms-live) と Dashboard。
- メッセージング層は GCP Pub/Sub 前提でクライアント実装が固まっている (`google.cloud.pubsub_v1` 互換)。

## Decision

### 1. Compute 配置: 全 LAN 内 (kabu 機とは別の Linux サーバ)

| 案 | 月額 | レイテンシ | 保守 |
|---|---|---|---|
| **全 LAN 内 (採用)** | 電気代のみ | 最小 (kabu と同セグメント) | 自宅 NW を自分で見る |
| Hybrid (LAN: feeder/oms-live, GCP: 他) | LAN + Cloud Run × 7 | LAN-GCP 跨ぎで +50ms | 2 環境分の監視 |
| Cloud 全振り + Tailscale 等で kabu に逆トンネル | GCP + トンネル | +30〜100ms | トンネル運用追加 |

**理由**: 個人運用 + kabu が物理的に LAN 内にある時点で「LAN-cloud 跨ぎ」を作る ROI が薄い。WSL 同居は memory `kabu_localhost_only.md` で除外済のため、別 Linux 機 (NUC / Mac mini / 自作 PC) に Docker Compose で 9 サービス相乗り。

### 2. メッセージング: GCP Pub/Sub (managed)

| 案 | 月額 | 既存コード |
|---|---|---|
| **GCP Pub/Sub (採用)** | 個人量で $0〜数 USD | 無修正 |
| Pub/Sub エミュレータを本番常駐 | $0 | 無修正だが永続化なし |
| Redis Streams / NATS に移行 | $0 | 全クライアント書き直し |

**理由**: クライアント実装 (subscriber, ack, retry) が Pub/Sub 想定。エミュレータは再起動でメッセージロストするため本番不可。月数百万メッセージ以下なら Pub/Sub 無料枠に収まる。

### 3. Supabase: Supabase Cloud (managed)

| 案 | 月額 | バックアップ | 保守 |
|---|---|---|---|
| **Supabase Cloud (採用)** | Free $0 / Pro $25 | Pro で 7 日 PITR | ほぼゼロ |
| self-host on LAN | 電気代 | 自前 cron + 外部ストレージ | アップグレード/監視を自前 |

**理由**: 売買履歴・positions は実損益直結。PITR が効く Pro プランで開始想定。Free でも開発継続可能だが DB 500MB 制限あり。リージョンは ap-southeast-1 (Singapore) または ap-northeast-2 (Seoul) を想定 (東京未提供)。

### 4. Dashboard: Vercel (Hobby)

| 案 | 月額 | デプロイ | RSC/Server Action |
|---|---|---|---|
| **Vercel (採用)** | Hobby $0 | git push 連携 | 公式サポート |
| Cloud Run | $0〜数 USD | Dockerfile + IaC | コンテナ化要 |
| GCS + CDN (静的) | <$1 | RSC 全廃の大改修 | × |

**理由**: 現 dashboard は Server Component / Server Action (`dashboard/app/system/actions.ts`) 前提。Vercel が公式パスで最小工数。Hobby の non-commercial 条件は個人実験運用で問題なし。Supabase Cloud との連携例も豊富。

### 5. Secrets: 起動時注入 (1Password CLI / op run)

| 案 | 操作性 | rotation |
|---|---|---|
| **1Password CLI で `op run -- docker compose up` (採用)** | テンプレ `.env` を実値に展開 | 手動 |
| GCP Secret Manager + service account | API で取得 | API |
| 平文 .env を host に常駐 | 最低 | × |

**理由**: LAN host が侵害されたとき kabu パスワードまで芋づるを避けたい。1Password を既に使っていれば追加コストなし。使っていない場合は GCP Secret Manager にフォールバック (Pub/Sub 用 service account がどのみち必要なため一元化しやすい)。

### 6. CI / CD: GitHub Actions self-hosted runner on LAN host

| 案 | 推奨度 | 備考 |
|---|---|---|
| **self-hosted runner (採用)** | 高 | repo-scoped、private repo 限定 |
| GHA → SSH push | 中 | host を SSH 公開する必要 |
| 手動 `git pull && docker compose up -d` | 低 | 自動化の旨味なし |

**理由**: LAN host を外部公開せずデプロイ自動化したいので、host 側から GitHub に poll する self-hosted runner が現実的。private repo 限定運用 (workflow が任意コード実行できるため public 化禁止)。

## Consequences

**Pros**
- 月額固定費は Supabase Pro + Vercel Hobby ($25 + $0 = $25)。Pub/Sub は使用量次第で +$0〜数 USD
- kabu とのレイテンシは LAN 内 (1〜2ms 想定)、Caddy 28080 経由
- 既存 `infra/docker-compose.dev.yml` を本番にほぼ流用可能 (永続 volume と secret 注入だけ追加)

**Cons**
- LAN host が SPOF。停電・回線断・ハード故障で停止する (UPS / 監視は別途)
- 横スケール困難。共同運用化する場合は再アーキ
- Supabase 東京リージョン未提供のため DB レイテンシ +30〜50ms

## Open Items (本 ADR の範囲外、次セッション以降)

- LAN host のスペック要件 (CPU / RAM / SSD 目安)
- GCP service account の権限スコープと鍵置き場
- self-hosted runner のセキュリティ強化方式
- Supabase RLS ポリシー本番化 (現状 service_role 直叩き)
- 監視・アラート (uptime-kuma / GCP Monitoring 等の選定)
- バックアップ戦略 (Supabase Pro PITR の他、host config / Caddy 設定)
