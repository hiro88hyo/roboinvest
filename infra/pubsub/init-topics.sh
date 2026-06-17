#!/bin/sh
# Pub/Sub エミュレータにトピックとサブスクリプションを作成する。
# REST API を直接叩くので gcloud 認証は不要。
#   topics.json        — トピック一覧
#   subscriptions.json — サブスクリプション一覧 (name + topic のペア)

set -eu

: "${PUBSUB_EMULATOR_HOST:?must be set (e.g. pubsub:8085)}"
PROJECT_ID="${PUBSUB_PROJECT_ID:-trade-ai-dev}"
# Docker コンテナ内は /pubsub にマウント。ホスト直実行時は PUBSUB_DIR で上書き可。
PUBSUB_DIR="${PUBSUB_DIR:-/pubsub}"

echo "Waiting for Pub/Sub emulator at ${PUBSUB_EMULATOR_HOST}..."
# エミュレータは / に対して 404 を返すが、到達性は確認できる
until curl -s -o /dev/null "http://${PUBSUB_EMULATOR_HOST}/"; do
    sleep 1
done
echo "Emulator reachable."

BASE="http://${PUBSUB_EMULATOR_HOST}/v1/projects/${PROJECT_ID}"

# --- トピック作成 ---
TOPICS=$(python3 -c "import json; print('\n'.join(json.load(open('${PUBSUB_DIR}/topics.json'))['topics']))")

for topic in $TOPICS; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X PUT "${BASE}/topics/${topic}")
    case "$HTTP_CODE" in
        200) echo "Created: $topic" ;;
        409) echo "Exists:  $topic" ;;
        *)   echo "ERROR:   $topic (HTTP $HTTP_CODE)"; exit 1 ;;
    esac
done

echo "All topics ready."

# --- サブスクリプション作成 ---
python3 - <<'PYEOF'
import json, os, sys, urllib.request, urllib.error

host    = os.environ["PUBSUB_EMULATOR_HOST"]
project = os.environ.get("PUBSUB_PROJECT_ID", "trade-ai-dev")
base    = f"http://{host}/v1/projects/{project}"

with open(f"{os.environ.get('PUBSUB_DIR', '/pubsub')}/subscriptions.json") as f:
    subs = json.load(f)["subscriptions"]

ok = True
for entry in subs:
    name  = entry["name"]
    topic = entry["topic"]
    payload = {"topic": f"projects/{project}/topics/{topic}"}
    if "filter" in entry:
        payload["filter"] = entry["filter"]
    body = json.dumps(payload).encode()
    req   = urllib.request.Request(
        f"{base}/subscriptions/{name}", data=body, method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
        print(f"Created: {name} -> {topic}")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print(f"Exists:  {name} -> {topic}")
        else:
            print(f"ERROR:   {name} -> {topic} (HTTP {e.code})", file=sys.stderr)
            ok = False

sys.exit(0 if ok else 1)
PYEOF

echo "All subscriptions ready."
