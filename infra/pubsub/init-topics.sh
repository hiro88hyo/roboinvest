#!/bin/sh
# Pub/Sub エミュレータに topics.json で定義されたトピックを作成する。
# REST API を直接叩くので gcloud 認証は不要。

set -eu

: "${PUBSUB_EMULATOR_HOST:?must be set (e.g. pubsub:8085)}"
PROJECT_ID="${PUBSUB_PROJECT_ID:-trade-ai-dev}"

echo "Waiting for Pub/Sub emulator at ${PUBSUB_EMULATOR_HOST}..."
# エミュレータは / に対して 404 を返すが、到達性は確認できる
until curl -s -o /dev/null "http://${PUBSUB_EMULATOR_HOST}/"; do
    sleep 1
done
echo "Emulator reachable."

TOPICS=$(python3 -c "import json; print('\n'.join(json.load(open('/pubsub/topics.json'))['topics']))")

for topic in $TOPICS; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X PUT "http://${PUBSUB_EMULATOR_HOST}/v1/projects/${PROJECT_ID}/topics/${topic}")
    case "$HTTP_CODE" in
        200) echo "Created: $topic" ;;
        409) echo "Exists:  $topic" ;;
        *)   echo "ERROR:   $topic (HTTP $HTTP_CODE)"; exit 1 ;;
    esac
done

echo "All topics ready."
