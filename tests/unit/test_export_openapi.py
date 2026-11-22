"""scripts/export_openapi.py: the API description the frontend's TypeScript types come from."""

import json
from pathlib import Path

from scripts.export_openapi import main


def test_it_writes_the_api_description_as_stable_json(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    main([str(target)])
    first = target.read_bytes()
    main([str(target)])

    spec = json.loads(first)
    assert "/api/v1/auth/login" in spec["paths"]
    assert first == target.read_bytes()  # the same bytes every run, so CI can diff it
    assert first.endswith(b"\n") and b"\r\n" not in first


def test_the_refresh_cookie_is_not_part_of_the_described_api(tmp_path: Path) -> None:
    # Page code never sees or sends the cookie; the browser does. Describing it would invite
    # a client that tries to.
    target = tmp_path / "openapi.json"

    main([str(target)])

    refresh = json.loads(target.read_text())["paths"]["/api/v1/auth/refresh"]["post"]
    assert "parameters" not in refresh and "requestBody" not in refresh
