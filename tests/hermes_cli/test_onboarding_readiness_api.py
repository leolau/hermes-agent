"""FG-15 — the dashboard-facing readiness API endpoint.

Exercises ``GET /api/onboarding/readiness`` (the shared backend the FG-17
first-run wizard will consume) against a temp ``HERMES_HOME``. Skipped where
the web server's optional deps (asyncpg/fastapi) aren't installed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("asyncpg")
pytest.importorskip("fastapi")


@pytest.fixture()
def temp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return home


def test_readiness_endpoint_returns_schema_and_gate(temp_home: Path) -> None:
    from hermes_cli import web_server
    from hermes_cli.config import save_config

    save_config({"onboarding": {"seen": {}}}, strip_defaults=False)

    payload = asyncio.run(web_server.get_onboarding_readiness())

    # Shape the dashboard wizard consumes.
    assert set(payload) >= {
        "score_pct",
        "ready_for_prod",
        "required_total",
        "required_met",
        "missing_required",
        "items",
    }
    assert payload["required_total"] == 5
    # No datastore/owner/provider/channel configured → not prod ready.
    assert payload["ready_for_prod"] is False
    # Each item carries the check + fix + rationale the wizard renders.
    for item in payload["items"]:
        assert {"key", "met", "detail", "required", "rationale", "fix_command"} <= set(item)
