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
