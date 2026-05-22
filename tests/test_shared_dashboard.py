from __future__ import annotations

import pytest

from claude_tap.shared_dashboard import DEFAULT_DASHBOARD_PORT, resolve_dashboard_port


def test_resolve_dashboard_port_defaults_to_shared_port() -> None:
    assert resolve_dashboard_port(0) == DEFAULT_DASHBOARD_PORT
    assert resolve_dashboard_port(None) == DEFAULT_DASHBOARD_PORT


def test_resolve_dashboard_port_honors_explicit_port() -> None:
    assert resolve_dashboard_port(3000) == 3000


def test_resolve_dashboard_port_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDTAP_DASHBOARD_PORT", "8765")
    assert resolve_dashboard_port(0) == 8765
