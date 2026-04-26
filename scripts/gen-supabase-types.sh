#!/usr/bin/env bash
# contracts/sql/ を Supabase に適用した状態から TypeScript 型を再生成する。
#
# 前提:
#   - `supabase start` でローカル Supabase が起動済み（ポート 54321/54322）
#   - `supabase` CLI が PATH 上にある
#
# 使い方:
#   ./scripts/gen-supabase-types.sh
#
# 出力:
#   contracts/typescript/src/generated/database.types.ts
#
# スキーマ変更フロー:
#   1) contracts/sql/*.sql を編集
#   2) `supabase db reset` などで反映
#   3) 本スクリプトを実行
#   4) `dashboard` で型エラーが出なくなるまで修正
#
# 手動編集禁止。常にこのスクリプトの出力で上書きする。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUPABASE_DIR="$REPO_ROOT/infra"
OUT_DIR="$REPO_ROOT/contracts/typescript/src/generated"
OUT_FILE="$OUT_DIR/database.types.ts"

if ! command -v supabase >/dev/null 2>&1; then
  echo "error: supabase CLI not found in PATH" >&2
  echo "  install: https://supabase.com/docs/guides/cli/getting-started" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

echo "Generating $OUT_FILE from local Supabase ..." >&2
( cd "$SUPABASE_DIR" && supabase gen types typescript --local --schema public ) >"$OUT_FILE"
echo "Done." >&2
