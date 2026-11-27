"""scripts/load_test.py: the commands it builds and how it reads k6's results, without Docker."""

from typing import Any

from app.core.config import Settings
from scripts import load_test


def test_every_command_runs_on_both_compose_files() -> None:
    command = load_test.compose("up", "-d")
    assert command[:2] == ["docker", "compose"]
    files = [command[i + 1] for i, word in enumerate(command) if word == "-f"]
    assert [f.replace("\\", "/").rsplit("/", 1)[-1] for f in files] == [
        "docker-compose.yml",
        "docker-compose.load.yml",
    ]
    assert command[-2:] == ["up", "-d"]


def test_the_four_api_containers_get_a_port_each() -> None:
    env = load_test.compose_env({"LOAD_SEARCH_PER_MINUTE": "60"})
    assert env["API_PORTS"] == "8000-8003"
    assert env["LOAD_SEARCH_PER_MINUTE"] == "60"
    assert "PATH" in env  # the rest of the environment, so docker is found


def test_one_run_can_scale_the_api_and_every_container_gets_a_port() -> None:
    assert load_test.api_ports(8) == "8000-8007"
    assert load_test.compose_env(apis=8)["API_PORTS"] == "8000-8007"
    assert load_test.api_urls(2) == [
        "http://localhost:8000/api/v1/health",
        "http://localhost:8001/api/v1/health",
    ]
    # --no-deps: scaling the API must leave Postgres, the broker and the worker running.
    assert load_test.scale_command(8)[-6:] == ["up", "-d", "--no-deps", "--scale", "api=8", "api"]
    assert load_test.run_name(500, 4) == "load-500"
    assert load_test.run_name(500, 8) == "load-500-8-apis"


def test_the_real_limits_are_the_ones_the_api_ships_with() -> None:
    # The defaults in config.py, not whatever a local .env says.
    shipped = {name: str(field.default) for name, field in Settings.model_fields.items()}
    expected = {
        "LOAD_LOGIN_PER_MINUTE": shipped["rate_limit_login_per_minute"],
        "LOAD_RESERVE_PER_MINUTE": shipped["rate_limit_reserve_per_minute"],
        "LOAD_PAYMENT_PER_MINUTE": shipped["rate_limit_payment_per_minute"],
        "LOAD_SEARCH_PER_MINUTE": shipped["rate_limit_search_per_minute"],
    }
    assert expected == load_test.REAL_LIMITS


def test_k6_runs_the_script_in_its_container_and_names_the_results() -> None:
    command = load_test.k6_command("load", "load-500", {"VUS": "500"})
    # --no-deps: starting k6 must never recreate the API containers it is about to test.
    assert command[-11:] == [
        "run", "--rm", "--no-deps", "k6", "run", "--quiet",
        "-e", "NAME=load-500",
        "-e", "VUS=500",
        "/scripts/load.js",
    ]  # fmt: skip


def summary(**thresholds: dict[str, bool]) -> dict[str, Any]:
    return {
        "metrics": {
            metric: {"thresholds": {expr: {"ok": ok} for expr, ok in exprs.items()}}
            for metric, exprs in thresholds.items()
        }
        | {"iterations": {"values": {"count": 3}}}
    }


def test_the_thresholds_k6_crossed_are_named() -> None:
    crossed = load_test.failed_thresholds(
        summary(reserved={"count==1": True}, server_errors={"count==0": False})
    )
    assert crossed == ["server_errors count==0"]
    assert load_test.failed_thresholds(summary(reserved={"count==1": True})) == []


def stat(name: str, cpu: str, memory: str) -> dict[str, str]:
    """One line of `docker stats --format '{{json .}}'`, the fields the runner reads."""
    return {"Name": name, "CPUPerc": cpu, "MemUsage": f"{memory} / 11.54GiB"}


def test_cpu_and_memory_peaks_add_up_the_api_containers_and_name_each_service() -> None:
    samples = [
        [
            stat("ticketloop-api-1", "50.00%", "100MiB"),
            stat("ticketloop-api-2", "70.00%", "120MiB"),
            stat("ticketloop-db-1", "200.50%", "1.5GiB"),
            stat("ticketloop-k6-run-76690da695fe", "90.00%", "512KiB"),
        ],
        [
            stat("ticketloop-api-1", "150.00%", "110MiB"),
            stat("ticketloop-api-2", "10.00%", "100MiB"),
            stat("ticketloop-db-1", "100.00%", "1GiB"),
        ],
    ]
    peaks = load_test.peaks(samples)
    assert peaks["api"] == {"cpu_percent": 160.0, "memory_mib": 220.0}  # summed, then the max
    assert peaks["db"] == {"cpu_percent": 200.5, "memory_mib": 1536.0}
    assert peaks["k6"] == {"cpu_percent": 90.0, "memory_mib": 0.5}


def test_prometheus_is_asked_about_the_run_s_own_window() -> None:
    queries = load_test.prometheus_queries(225)
    assert queries["cache hit ratio"] == (
        'sum(increase(cache_requests_total{outcome="hit"}[225s]))'
        " / sum(increase(cache_requests_total[225s]))"
    )
    assert queries["connections in use, most"] == (
        "max_over_time(sum(db_pool_checked_out_connections)[225s:5s])"
    )
    assert "outbox_oldest_unpublished_seconds" in queries["oldest unpublished event, s"]
    assert "rabbitmq_queue_messages_ready" in queries["messages waiting, most"]
