"""frontend/: the image `docker compose up` serves the browser app from."""

import re
from pathlib import Path

FRONTEND = Path(__file__).parents[2] / "frontend"


def test_the_image_builds_on_pinned_node_and_serves_from_pinned_nginx() -> None:
    dockerfile = (FRONTEND / "Dockerfile").read_text("utf-8")
    stages = re.findall(r"^FROM (\S+)", dockerfile, re.MULTILINE)
    assert stages == ["node:24.11.1-alpine3.22", "nginx:1.29.3-alpine"]
    assert "npm ci" in dockerfile  # the lockfile's versions, nothing newer


def test_nginx_forwards_the_api_and_answers_every_other_path_with_the_app() -> None:
    conf = (FRONTEND / "nginx.conf").read_text("utf-8")
    # Docker's DNS, asked per request through a variable: nginx starts whether or not the API
    # is up yet, and finds it again after it is recreated with a new address.
    assert re.search(r"^\s*resolver 127\.0\.0\.11\b", conf, re.MULTILINE)
    assert "set $api http://api:8000;" in conf
    assert re.search(r"location /api/ \{[^}]*proxy_pass \$api;", conf)
    # /orders/123 is a page of the app, not a file: the app's router draws it.
    assert "try_files $uri /index.html;" in conf
    # Above the API's own 6 MiB, so a buyer sees the API's problem+json 413, not nginx's page.
    assert "client_max_body_size 8m;" in conf


def test_the_build_context_leaves_out_what_npm_ci_rebuilds() -> None:
    ignored = (FRONTEND / ".dockerignore").read_text("utf-8").split()
    assert {"node_modules", "dist"} <= set(ignored)
