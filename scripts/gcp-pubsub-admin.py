#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "google-cloud-pubsub>=2.38",
# ]
# ///
"""Create and verify managed GCP Pub/Sub resources for ADR-0001.

The source of truth is:

- infra/pubsub/topics.json
- infra/pubsub/subscriptions.json

Default mode is check-only. Use --apply to create missing resources, and
--smoke-test to publish / pull / ack one message through dedicated smoke-test
resources.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from google.api_core.exceptions import AlreadyExists, GoogleAPICallError, NotFound
from google.cloud import pubsub_v1
from google.oauth2 import service_account

REPO_ROOT = Path(__file__).resolve().parent.parent
TOPICS_JSON = REPO_ROOT / "infra" / "pubsub" / "topics.json"
SUBSCRIPTIONS_JSON = REPO_ROOT / "infra" / "pubsub" / "subscriptions.json"
SMOKE_TOPIC = "adr-0001-smoke-test"
SMOKE_SUBSCRIPTION = "adr-0001-smoke-test-sub"


@dataclass(frozen=True, slots=True)
class SubscriptionSpec:
    name: str
    topic: str
    filter: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-id",
        required=True,
        help="GCP project id that owns the Pub/Sub resources.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create missing topics/subscriptions. Default is check-only.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Publish, pull, and ack one message through dedicated smoke resources.",
    )
    parser.add_argument(
        "--cleanup-smoke",
        action="store_true",
        help="Delete smoke resources only when this run created them.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Publish/pull API timeout seconds. Default: 30.",
    )
    return parser.parse_args()


def load_topics() -> list[str]:
    payload = json.loads(TOPICS_JSON.read_text(encoding="utf-8"))
    topics = payload.get("topics")
    if not isinstance(topics, list):
        raise RuntimeError(f"unexpected topics.json shape: {type(topics).__name__}")
    return [str(topic) for topic in topics]


def load_subscriptions() -> list[SubscriptionSpec]:
    payload = json.loads(SUBSCRIPTIONS_JSON.read_text(encoding="utf-8"))
    subscriptions = payload.get("subscriptions")
    if not isinstance(subscriptions, list):
        raise RuntimeError(f"unexpected subscriptions.json shape: {type(subscriptions).__name__}")
    return [
        SubscriptionSpec(
            name=str(sub["name"]),
            topic=str(sub["topic"]),
            filter=str(sub["filter"]) if "filter" in sub else None,
        )
        for sub in subscriptions
    ]


def topic_path(project_id: str, topic: str) -> str:
    return pubsub_v1.PublisherClient.topic_path(project_id, topic)


def subscription_path(project_id: str, subscription: str) -> str:
    return pubsub_v1.SubscriberClient.subscription_path(project_id, subscription)


def client_credentials() -> service_account.Credentials | None:
    raw = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    if not raw:
        return None
    info = json.loads(raw)
    return service_account.Credentials.from_service_account_info(info)


def ensure_smoke_topic(
    publisher: pubsub_v1.PublisherClient,
    *,
    project_id: str,
) -> tuple[str, bool]:
    path = topic_path(project_id, SMOKE_TOPIC)
    try:
        publisher.get_topic(request={"topic": path})
        print(f"OK   smoke-topic:{SMOKE_TOPIC}")
        return path, False
    except NotFound:
        publisher.create_topic(request={"name": path})
        print(f"ADD  smoke-topic:{SMOKE_TOPIC}")
        return path, True


def ensure_smoke_subscription(
    subscriber: pubsub_v1.SubscriberClient,
    *,
    project_id: str,
    topic: str,
) -> tuple[str, bool]:
    path = subscription_path(project_id, SMOKE_SUBSCRIPTION)
    try:
        subscriber.get_subscription(request={"subscription": path})
        print(f"OK   smoke-sub:{SMOKE_SUBSCRIPTION}")
        return path, False
    except NotFound:
        subscriber.create_subscription(
            request={
                "name": path,
                "topic": topic,
                "ack_deadline_seconds": 30,
            }
        )
        print(f"ADD  smoke-sub:{SMOKE_SUBSCRIPTION}")
        return path, True


def ensure_topics(project_id: str, topics: Iterable[str], *, apply: bool) -> bool:
    publisher = pubsub_v1.PublisherClient(credentials=client_credentials())
    ok = True
    try:
        for topic in topics:
            path = publisher.topic_path(project_id, topic)
            try:
                publisher.get_topic(request={"topic": path})
                print(f"OK   topic:{topic}")
            except NotFound:
                ok = False
                if not apply:
                    print(f"MISS topic:{topic}")
                    continue
                try:
                    publisher.create_topic(request={"name": path})
                    print(f"ADD  topic:{topic}")
                    ok = True
                except AlreadyExists:
                    print(f"OK   topic:{topic}")
                except GoogleAPICallError as exc:
                    print(f"NG   topic:{topic} {exc!r}")
                    ok = False
            except GoogleAPICallError as exc:
                print(f"NG   topic:{topic} {exc!r}")
                ok = False
    finally:
        publisher.transport.close()
    return ok


def ensure_subscriptions(
    project_id: str,
    subscriptions: Iterable[SubscriptionSpec],
    *,
    apply: bool,
) -> bool:
    subscriber = pubsub_v1.SubscriberClient(credentials=client_credentials())
    ok = True
    try:
        for spec in subscriptions:
            path = subscriber.subscription_path(project_id, spec.name)
            topic = topic_path(project_id, spec.topic)
            try:
                subscription = subscriber.get_subscription(request={"subscription": path})
                actual_topic = subscription.topic.rsplit("/", 1)[-1]
                actual_filter = subscription.filter or None
                if actual_topic != spec.topic:
                    print(f"NG   sub:{spec.name} topic={actual_topic}, expected={spec.topic}")
                    ok = False
                elif actual_filter != spec.filter:
                    print(
                        f"NG   sub:{spec.name} filter={actual_filter!r}, "
                        f"expected={spec.filter!r}"
                    )
                    ok = False
                else:
                    suffix = f" filter={spec.filter!r}" if spec.filter else ""
                    print(f"OK   sub:{spec.name} -> {spec.topic}{suffix}")
            except NotFound:
                ok = False
                if not apply:
                    print(f"MISS sub:{spec.name} -> {spec.topic}")
                    continue
                try:
                    request = {
                        "name": path,
                        "topic": topic,
                        "ack_deadline_seconds": 30,
                    }
                    if spec.filter:
                        request["filter"] = spec.filter
                    subscriber.create_subscription(
                        request=request
                    )
                    suffix = f" filter={spec.filter!r}" if spec.filter else ""
                    print(f"ADD  sub:{spec.name} -> {spec.topic}{suffix}")
                    ok = True
                except AlreadyExists:
                    print(f"OK   sub:{spec.name} -> {spec.topic}")
                except GoogleAPICallError as exc:
                    print(f"NG   sub:{spec.name} {exc!r}")
                    ok = False
            except GoogleAPICallError as exc:
                print(f"NG   sub:{spec.name} {exc!r}")
                ok = False
    finally:
        subscriber.close()
    return ok


def smoke_test(project_id: str, *, timeout: float, cleanup: bool) -> bool:
    credentials = client_credentials()
    publisher = pubsub_v1.PublisherClient(credentials=credentials)
    subscriber = pubsub_v1.SubscriberClient(credentials=credentials)
    payload = f"adr-0001-smoke:{int(time.time())}".encode()
    ok = False
    created_topic = False
    created_subscription = False
    topic = ""
    subscription = ""
    try:
        topic, created_topic = ensure_smoke_topic(publisher, project_id=project_id)
        subscription, created_subscription = ensure_smoke_subscription(
            subscriber,
            project_id=project_id,
            topic=topic,
        )

        future = publisher.publish(topic, payload, purpose="adr-0001-smoke-test")
        message_id = future.result(timeout=timeout)
        print(f"OK   smoke-publish message_id={message_id}")

        response = subscriber.pull(
            request={"subscription": subscription, "max_messages": 1},
            timeout=timeout,
        )
        if not response.received_messages:
            print("NG   smoke-pull no messages")
            return False
        received = response.received_messages[0]
        if bytes(received.message.data) != payload:
            print("NG   smoke-pull payload mismatch")
            return False
        subscriber.acknowledge(
            request={"subscription": subscription, "ack_ids": [received.ack_id]},
            timeout=timeout,
        )
        print(f"OK   smoke-pull-ack message_id={received.message.message_id}")
        ok = True
        return True
    except GoogleAPICallError as exc:
        print(f"NG   smoke-test {exc!r}")
        return False
    finally:
        if cleanup:
            if created_subscription:
                try:
                    subscriber.delete_subscription(request={"subscription": subscription})
                    print(f"DEL  smoke-sub:{SMOKE_SUBSCRIPTION}")
                except NotFound:
                    pass
                except GoogleAPICallError as exc:
                    print(f"WARN smoke-sub cleanup failed: {exc!r}")
            elif ok:
                print(f"KEEP smoke-sub:{SMOKE_SUBSCRIPTION}")
            if created_topic:
                try:
                    publisher.delete_topic(request={"topic": topic})
                    print(f"DEL  smoke-topic:{SMOKE_TOPIC}")
                except NotFound:
                    pass
                except GoogleAPICallError as exc:
                    print(f"WARN smoke-topic cleanup failed: {exc!r}")
            elif ok:
                print(f"KEEP smoke-topic:{SMOKE_TOPIC}")
        subscriber.close()
        publisher.transport.close()
        if not ok:
            print(
                "HINT Check GOOGLE_APPLICATION_CREDENTIALS and Pub/Sub IAM roles. "
                "If runtime SA cannot create smoke resources, pre-create them with --apply."
            )


def main() -> int:
    args = parse_args()
    topics = load_topics()
    subscriptions = load_subscriptions()

    print(f"project={args.project_id}")

    topics_ok = ensure_topics(args.project_id, topics, apply=args.apply)
    subs_ok = ensure_subscriptions(args.project_id, subscriptions, apply=args.apply)
    smoke_ok = True
    if args.smoke_test:
        smoke_ok = smoke_test(
            args.project_id,
            timeout=args.timeout,
            cleanup=args.cleanup_smoke,
        )

    if topics_ok and subs_ok and smoke_ok:
        print("RESULT OK")
        return 0
    print("RESULT NG")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
