"""The Prometheus, Grafana and dashboard files compose mounts: what is scraped, and what the
dashboard shows."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

OBSERVABILITY = Path(__file__).parents[2] / "observability"
PROMETHEUS = yaml.safe_load((OBSERVABILITY / "prometheus.yml").read_text("utf-8"))
GRAFANA = OBSERVABILITY / "grafana"
DASHBOARD = json.loads((GRAFANA / "dashboards" / "ticketloop.json").read_text("utf-8"))

# Every metric Task 1 added, by the name Prometheus stores it under.
CUSTOM_METRICS = [
    "reservation_attempts_total",
    "payment_events_total",
    "payment_webhook_seconds_bucket",
    "worker_tasks_total",
    "outbox_published_total",
    "outbox_publish_failures_total",
    "outbox_backlog",
    "outbox_oldest_unpublished_seconds",
    "db_pool_checked_out_connections",
    "cache_requests_total",
    "cache_bumps_owed_total",
    "rate_limit_decisions_total",
]


def jobs() -> dict[str, dict[str, Any]]:
    return {job["job_name"]: job for job in PROMETHEUS["scrape_configs"]}


@pytest.mark.parametrize(
    ("job", "target"),
    [
        ("api", "host.docker.internal:8000"),
        ("worker", "worker:9100"),
        ("relay", "relay:9101"),
        ("rabbitmq", "rabbitmq:15692"),
    ],
)
def test_prometheus_scrapes_every_process_that_has_metrics(job: str, target: str) -> None:
    (static,) = jobs()[job]["static_configs"]
    assert static["targets"] == [target]


def test_rabbitmq_is_scraped_per_queue() -> None:
    assert jobs()["rabbitmq"]["metrics_path"] == "/metrics/per-object"
    assert jobs()["api"].get("metrics_path", "/metrics") == "/metrics"


def test_grafana_finds_prometheus_jaeger_and_the_dashboard() -> None:
    sources = yaml.safe_load(
        (GRAFANA / "provisioning" / "datasources" / "datasources.yml").read_text("utf-8")
    )
    by_uid = {source["uid"]: source for source in sources["datasources"]}
    assert by_uid["prometheus"]["url"] == "http://prometheus:9090"
    assert by_uid["prometheus"]["isDefault"] is True
    assert by_uid["jaeger"]["url"] == "http://jaeger:16686"
    providers = yaml.safe_load(
        (GRAFANA / "provisioning" / "dashboards" / "dashboards.yml").read_text("utf-8")
    )
    (provider,) = providers["providers"]
    assert provider["options"]["path"] == "/var/lib/grafana/dashboards"


def panels() -> list[dict[str, Any]]:
    return [panel for panel in DASHBOARD["panels"] if panel["type"] != "row"]


def expressions() -> list[str]:
    return [target["expr"] for panel in panels() for target in panel.get("targets", [])]


def test_every_panel_asks_prometheus_something() -> None:
    assert panels()
    for panel in panels():
        assert panel["datasource"] == {"type": "prometheus", "uid": "prometheus"}, panel["title"]
        assert panel["targets"] and all(t["expr"].strip() for t in panel["targets"])


@pytest.mark.parametrize("metric", CUSTOM_METRICS)
def test_every_metric_the_purchase_flow_counts_is_on_the_dashboard(metric: str) -> None:
    assert any(re.search(rf"\b{metric}\b", expr) for expr in expressions()), metric


@pytest.mark.parametrize("quantile", ["0.5", "0.95", "0.99"])
def test_the_dashboard_shows_latency_percentiles_per_route(quantile: str) -> None:
    wanted = re.compile(
        rf"histogram_quantile\(\s*{re.escape(quantile)}\s*,\s*sum by \(le, handler\) "
        r"\(rate\(http_request_duration_seconds_bucket"
    )
    assert any(wanted.search(expr) for expr in expressions()), quantile


def test_the_dashboard_shows_requests_errors_and_queue_depth() -> None:
    joined = "\n".join(expressions())
    assert "sum by (handler) (rate(http_requests_total" in joined
    assert 'status="5xx"' in joined
    assert "rabbitmq_queue_messages_ready" in joined


def test_no_two_panels_overlap() -> None:
    taken: set[tuple[int, int]] = set()
    for panel in DASHBOARD["panels"]:
        grid = panel["gridPos"]
        cells = {
            (x, y)
            for x in range(grid["x"], grid["x"] + grid["w"])
            for y in range(grid["y"], grid["y"] + grid["h"])
        }
        assert not cells & taken, panel["title"]
        assert grid["x"] + grid["w"] <= 24, panel["title"]
        taken |= cells


LOAD_PROMETHEUS = yaml.safe_load((OBSERVABILITY / "prometheus-load.yml").read_text("utf-8"))


def test_the_load_config_finds_every_api_container_by_dns() -> None:
    load = {job["job_name"]: job for job in LOAD_PROMETHEUS["scrape_configs"]}
    (found,) = load["api"]["dns_sd_configs"]
    assert found["names"] == ["api"]
    assert (found["type"], found["port"]) == ("A", 8000)
    assert "static_configs" not in load["api"]


def test_the_load_config_scrapes_everything_else_the_same_way() -> None:
    load = {job["job_name"]: job for job in LOAD_PROMETHEUS["scrape_configs"]}
    assert load.keys() == jobs().keys()
    for name, job in jobs().items():
        if name != "api":
            assert load[name] == job, name
    assert LOAD_PROMETHEUS["global"] == PROMETHEUS["global"]


def test_connections_in_use_add_up_across_api_containers() -> None:
    assert "sum(db_pool_checked_out_connections)" in expressions()
