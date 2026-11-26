"""docker-compose.yml: what keeps running without anyone watching."""

from pathlib import Path

import pytest
import yaml

COMPOSE = yaml.safe_load((Path(__file__).parents[2] / "docker-compose.yml").read_text("utf-8"))


@pytest.mark.parametrize("service", ["worker", "beat", "relay"])
def test_the_background_services_come_back_by_themselves(service: str) -> None:
    # A Celery worker exits after its last attempt to reach a dead broker. Without a restart
    # policy it stays down when the broker returns, and no ticket moves again.
    assert COMPOSE["services"][service].get("restart") == "unless-stopped"


def test_redis_runs_with_a_health_check_and_everyone_can_reach_it() -> None:
    redis = COMPOSE["services"]["redis"]
    assert redis["image"] == "redis:8.4.0-alpine"
    assert "healthcheck" in redis
    for service in ["api", "worker", "beat", "relay"]:
        assert COMPOSE["services"][service]["environment"]["REDIS_URL"] == "redis://redis:6379/0"


WATCHING = {
    "prometheus": "prom/prometheus:v3.7.3",
    "grafana": "grafana/grafana:12.2.1",
    "jaeger": "jaegertracing/jaeger:2.11.0",
}


@pytest.mark.parametrize(("service", "image"), WATCHING.items())
def test_prometheus_grafana_and_jaeger_run_pinned_images(service: str, image: str) -> None:
    assert COMPOSE["services"][service]["image"] == image


def test_prometheus_and_grafana_read_their_config_from_the_repository() -> None:
    prometheus = COMPOSE["services"]["prometheus"]["volumes"]
    assert "./observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro" in prometheus
    grafana = COMPOSE["services"]["grafana"]["volumes"]
    for part in ["provisioning/datasources", "provisioning/dashboards", "dashboards"]:
        assert any(volume.startswith(f"./observability/grafana/{part}:") for volume in grafana)


@pytest.mark.parametrize("service", ["api", "worker", "relay"])
def test_the_api_worker_and_relay_send_traces_to_jaeger(service: str) -> None:
    environment = COMPOSE["services"][service]["environment"]
    assert environment["TRACING_ENABLED"] == "true"
    assert environment["OTLP_ENDPOINT"] == "http://jaeger:4318"


@pytest.mark.parametrize("service", ["api", "worker", "beat", "relay"])
def test_nothing_waits_for_the_tools_that_watch_it(service: str) -> None:
    # A missing Jaeger or Prometheus must never keep the shop from starting.
    waits_for = COMPOSE["services"][service].get("depends_on", {})
    assert not {"jaeger", "prometheus", "grafana"} & set(waits_for)


def test_the_worker_and_relay_serve_metrics_where_prometheus_looks() -> None:
    worker = COMPOSE["services"]["worker"]["environment"]
    assert worker["WORKER_METRICS_PORT"] == "9100"
    assert worker["PROMETHEUS_MULTIPROC_DIR"] == "/tmp/prometheus"
    assert COMPOSE["services"]["relay"]["environment"]["RELAY_METRICS_PORT"] == "9101"
    # Only the worker's processes share counters through files; the relay is one process.
    for service in ["relay", "beat", "api"]:
        assert "PROMETHEUS_MULTIPROC_DIR" not in COMPOSE["services"][service]["environment"]
    # The image makes the folder, owned by the user the worker runs as.
    dockerfile = (Path(__file__).parents[2] / "Dockerfile").read_text("utf-8")
    assert "install -d -o appuser /data /tmp/prometheus" in dockerfile


def test_the_ticket_provider_succeeds_unless_told_otherwise() -> None:
    mode = COMPOSE["services"]["worker"]["environment"]["TICKET_PROVIDER_MODE"]
    assert mode == "${TICKET_PROVIDER_MODE:-succeed}"
