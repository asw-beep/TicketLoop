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


# ---------------------------------------------------------------- the load-test stack (M12)

LOAD = yaml.safe_load((Path(__file__).parents[2] / "docker-compose.load.yml").read_text("utf-8"))[
    "services"
]
APP_SERVICES = ["api", "worker", "beat", "relay", "migrate"]
RAISED = {
    "RATE_LIMIT_LOGIN_PER_MINUTE": "${LOAD_LOGIN_PER_MINUTE:-1000000}",
    "RATE_LIMIT_RESERVE_PER_MINUTE": "${LOAD_RESERVE_PER_MINUTE:-1000000}",
    "RATE_LIMIT_PAYMENT_PER_MINUTE": "${LOAD_PAYMENT_PER_MINUTE:-1000000}",
    "RATE_LIMIT_SEARCH_PER_MINUTE": "${LOAD_SEARCH_PER_MINUTE:-1000000}",
}


@pytest.mark.parametrize("service", APP_SERVICES)
def test_the_load_stack_runs_on_the_seeded_database(service: str) -> None:
    assert LOAD[service]["environment"]["DATABASE_URL"].endswith("@db:5432/ticketloop_perf")


def test_the_load_stack_runs_four_api_containers_with_the_limits_out_of_the_way() -> None:
    api = LOAD["api"]
    assert api["deploy"]["replicas"] == 4
    for name, value in RAISED.items():
        assert api["environment"][name] == value, name
    # Logged in once in setup, the tokens must outlive the longest run.
    assert int(api["environment"]["ACCESS_TOKEN_MINUTES"]) >= 60


@pytest.mark.parametrize("service", ["api", "worker", "relay"])
def test_the_load_stack_samples_one_trace_in_a_hundred(service: str) -> None:
    environment = LOAD[service]["environment"]
    assert environment["OTEL_TRACES_SAMPLER"] == "parentbased_traceidratio"
    assert environment["OTEL_TRACES_SAMPLER_ARG"] == "0.01"


def test_the_load_stack_spreads_webhooks_and_work_and_has_the_connections_for_it() -> None:
    webhook = LOAD["fakepay"]["environment"]["FAKEPAY_WEBHOOK_URL"]
    assert webhook == "http://api:8000/api/v1/payments/webhooks/fakepay"  # any API container
    worker = LOAD["worker"]["command"]
    assert worker[worker.index("--concurrency") + 1] == "8"
    assert "max_connections=200" in LOAD["db"]["command"]


def test_the_load_stack_scrapes_each_api_container() -> None:
    mounts = LOAD["prometheus"]["volumes"]
    assert "./observability/prometheus-load.yml:/etc/prometheus/prometheus.yml:ro" in mounts


def test_k6_runs_a_pinned_image_only_when_asked() -> None:
    k6 = LOAD["k6"]
    assert k6["image"] == "grafana/k6:1.4.2"
    assert k6["profiles"] == ["load"]
    assert "./tests/load:/scripts:ro" in k6["volumes"]
    assert "./tests/load/results:/results" in k6["volumes"]
    assert k6["environment"]["BASE_URL"] == "http://api:8000"


def test_the_everyday_stack_keeps_one_api_on_8000_and_the_real_limits() -> None:
    assert COMPOSE["services"]["api"]["ports"] == ["${API_PORTS:-8000}:8000"]
    for name, service in COMPOSE["services"].items():
        environment = service.get("environment") or {}
        assert not [key for key in environment if key.startswith("RATE_LIMIT_")], name
        assert "OTEL_TRACES_SAMPLER" not in environment, name


def test_the_browser_app_is_served_on_5173_and_starts_after_the_api() -> None:
    web = COMPOSE["services"]["web"]
    assert web["build"] == "./frontend"
    # 5173, where the reset and verification emails link, as with the dev server.
    assert web["ports"] == ["5173:80"]
    assert web["depends_on"] == ["api"]  # the order only: nginx finds the API per request
