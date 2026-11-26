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
