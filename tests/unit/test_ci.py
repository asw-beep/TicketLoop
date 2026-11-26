""".github/workflows/ci.yml: the image that ships is built and started, and known
vulnerabilities fail the build."""

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = yaml.safe_load(
    (Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml").read_text("utf-8")
)
JOBS: dict[str, Any] = WORKFLOW["jobs"]


def script(job: str) -> str:
    """Every command the job runs, as one text."""
    return "\n".join(step.get("run", "") for step in JOBS[job]["steps"])


def test_the_image_is_built_from_the_dockerfile() -> None:
    assert "docker build -t ticketloop:ci ." in script("image")  # ./Dockerfile


def test_the_image_starts_against_postgres_as_it_would_when_deployed() -> None:
    job = JOBS["image"]
    assert job["services"]["postgres"]["image"] == "postgres:17"
    commands = script("image")
    # Migrated by the image itself, as compose's migrate service does.
    assert "alembic upgrade head" in commands
    # Production settings: the built-in development secrets are refused there.
    assert "ENVIRONMENT=production" in commands
    for secret in ["JWT_SECRET_KEY", "STORAGE_SECRET_KEY", "FAKEPAY_WEBHOOK_SECRET"]:
        assert f"{secret}=" in commands, secret


def test_the_started_image_runs_as_its_own_user_and_answers_its_health_check() -> None:
    commands = script("image")
    assert 'test "$(docker exec api id -u)" = "10001"' in commands
    assert "curl -sf http://localhost:8000/api/v1/health" in commands
    assert "docker logs api" in commands  # what to read when it does not come up


def test_bandit_fails_the_build_on_a_high_severity_finding() -> None:
    commands = script("security")
    assert "bandit" in commands
    assert "--severity-level high" in commands
    assert "-r app fakepay" in commands


def test_pip_audit_checks_exactly_what_the_image_installs() -> None:
    commands = script("security")
    assert "uv export --locked --no-dev" in commands
    assert "pip-audit" in commands
    assert "--require-hashes" in commands


def test_npm_audit_fails_the_build_on_a_high_severity_advisory() -> None:
    steps = JOBS["security"]["steps"]
    npm = [step for step in steps if "npm audit" in step.get("run", "")]
    assert npm and "--audit-level=high" in npm[0]["run"]
    assert npm[0].get("working-directory") == "frontend"


def test_the_security_tools_are_pinned() -> None:
    commands = script("security")
    for tool in ["bandit", "pip-audit"]:
        assert f"{tool}==" in commands, tool
