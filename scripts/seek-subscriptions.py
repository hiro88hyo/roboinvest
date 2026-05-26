#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "google-cloud-pubsub>=2.38",
# ]
# ///
import json
import os
import time
from pathlib import Path

from google.cloud import pubsub_v1
from google.protobuf.timestamp_pb2 import Timestamp

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBSCRIPTIONS_JSON = REPO_ROOT / "infra" / "pubsub" / "subscriptions.json"


def load_subscriptions():
    payload = json.loads(SUBSCRIPTIONS_JSON.read_text(encoding="utf-8"))
    return [sub["name"] for sub in payload["subscriptions"]]


def main():
    project_id = os.environ.get("PUBSUB_PROJECT_ID")
    if not project_id:
        print("PUBSUB_PROJECT_ID environment variable not set.")
        return 1

    subs = load_subscriptions()
    subscriber = pubsub_v1.SubscriberClient()

    now = time.time()
    seconds = int(now)
    nanos = int((now - seconds) * 10**9)
    timestamp = Timestamp(seconds=seconds, nanos=nanos)

    print(f"Seeking subscriptions in project {project_id} to current time (seconds={seconds})...")
    for sub in subs:
        sub_path = subscriber.subscription_path(project_id, sub)
        try:
            subscriber.seek(request={"subscription": sub_path, "time": timestamp})
            print(f"SUCCESS: {sub}")
        except Exception as e:
            print(f"FAILED : {sub} ({e!r})")

    subscriber.close()


if __name__ == "__main__":
    import sys

    sys.exit(main())
