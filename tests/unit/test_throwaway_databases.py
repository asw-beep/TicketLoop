"""Scripts that drop or flood a database refuse any name that does not say it is throwaway."""

import pytest

from scripts import explain_discovery, seed_discovery
from scripts.databases import is_throwaway, recreate_database

DEV = "postgresql+psycopg://u:p@localhost:5432/ticketloop"


@pytest.mark.parametrize("name", ["ticketloop_test", "ticketloop_e2e", "ticketloop_perf"])
def test_throwaway_names(name: str) -> None:
    assert is_throwaway(name)


@pytest.mark.parametrize("name", ["ticketloop", "perf", "ticketloop_perf2", "", None])
def test_other_names_are_kept(name: str | None) -> None:
    assert not is_throwaway(name)


def test_recreate_refuses_the_development_database() -> None:
    with pytest.raises(ValueError, match="refusing to drop"):
        recreate_database(DEV)


@pytest.mark.parametrize("script", [seed_discovery, explain_discovery])
@pytest.mark.parametrize("name", ["ticketloop", "ticketloop_test"])
def test_the_performance_scripts_refuse_any_other_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], script, name: str
) -> None:
    settings = seed_discovery.get_settings().model_copy(
        update={"database_url": DEV.replace("ticketloop", name)}
    )
    monkeypatch.setattr(script, "get_settings", lambda: settings)

    assert script.main([]) == 2
    assert "Refusing" in capsys.readouterr().err
