from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "sync-log-based-metrics.py"
SPEC = importlib.util.spec_from_file_location("sync_log_based_metrics", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def write_config(tmp_path: Path, metrics: list[dict[str, object]]) -> Path:
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps({"schema_version": 1, "metrics": metrics}), encoding="utf-8")
    return path


def test_load_metrics_substitutes_project_and_preserves_filters(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        [
            {
                "name": "roboinvest_error_count",
                "description": "Errors",
                "filter": 'logName="projects/${PROJECT_ID}/logs/roboinvest" AND severity>=ERROR',
            }
        ],
    )

    metrics = module.load_metrics(path, "trade-ai-prod")

    assert metrics[0].name == "roboinvest_error_count"
    assert "projects/trade-ai-prod/logs/roboinvest" in metrics[0].filter


@pytest.mark.parametrize(
    "metrics, message",
    [
        ([], "non-empty metrics list"),
        (
            [
                {
                    "name": "Bad-Name",
                    "description": "bad",
                    "filter": "projects/${PROJECT_ID}/logs/roboinvest",
                }
            ],
            "name is invalid",
        ),
        (
            [
                {
                    "name": "duplicate",
                    "description": "one",
                    "filter": "projects/${PROJECT_ID}/logs/roboinvest",
                },
                {
                    "name": "duplicate",
                    "description": "two",
                    "filter": "projects/${PROJECT_ID}/logs/roboinvest",
                },
            ],
            "duplicate metric name",
        ),
    ],
)
def test_load_metrics_rejects_invalid_config(
    tmp_path: Path, metrics: list[dict[str, str]], message: str
) -> None:
    path = write_config(tmp_path, metrics)

    with pytest.raises(ValueError, match=message):
        module.load_metrics(path, "trade-ai-prod")


def test_gcloud_command_uses_create_or_update() -> None:
    metric = module.LogMetric("metric_name", "description", "severity>=ERROR")

    create = module.gcloud_command(metric, "project", exists=False)
    update = module.gcloud_command(metric, "project", exists=True)

    assert create[3] == "create"
    assert update[3] == "update"
    assert "--project=project" in create
    assert "--log-filter=severity>=ERROR" in create


def test_load_distribution_metric_and_render_gcloud_config(tmp_path: Path) -> None:
    bucket_options = {"exponentialBuckets": {"numFiniteBuckets": 20, "growthFactor": 2, "scale": 1}}
    path = write_config(
        tmp_path,
        [
            {
                "name": "roboinvest_received_per_window",
                "description": "Received messages",
                "filter": (
                    'logName="projects/${PROJECT_ID}/logs/roboinvest" AND jsonPayload.received:*'
                ),
                "metric_type": "distribution",
                "value_extractor": "EXTRACT(jsonPayload.received)",
                "unit": "1",
                "bucket_options": bucket_options,
            }
        ],
    )

    metric = module.load_metrics(path, "trade-ai-prod")[0]
    config = module.metric_config(metric)
    command = module.gcloud_command(
        metric,
        "trade-ai-prod",
        exists=False,
        config_path=Path("metric.json"),
    )

    assert metric.is_distribution
    assert config["valueExtractor"] == "EXTRACT(jsonPayload.received)"
    assert config["metricDescriptor"] == {
        "metricKind": "DELTA",
        "valueType": "DISTRIBUTION",
        "unit": "1",
    }
    assert config["bucketOptions"] == bucket_options
    assert "--config-from-file=metric.json" in command
    assert not any(part.startswith("--log-filter=") for part in command)


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"metric_type": "gauge"}, "metric_type must be counter or distribution"),
        ({"metric_type": "distribution"}, "value_extractor is required"),
        (
            {
                "metric_type": "distribution",
                "value_extractor": "EXTRACT(jsonPayload.received)",
            },
            "bucket_options is required",
        ),
        (
            {"value_extractor": "EXTRACT(jsonPayload.received)"},
            "counter must not define value_extractor",
        ),
    ],
)
def test_load_metrics_rejects_invalid_metric_type_options(
    tmp_path: Path, extra: dict[str, object], message: str
) -> None:
    metric: dict[str, object] = {
        "name": "roboinvest_metric",
        "description": "Metric",
        "filter": 'logName="projects/${PROJECT_ID}/logs/roboinvest"',
        **extra,
    }
    path = write_config(tmp_path, [metric])

    with pytest.raises(ValueError, match=message):
        module.load_metrics(path, "trade-ai-prod")
