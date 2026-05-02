from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, ClientConfig, run_client


class _DummyProc:
    def __init__(self) -> None:
        self.pid = 12345
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def test_client_config_default_proxy_mode_defaults_to_reverse() -> None:
    cfg = ClientConfig(
        cmd="x",
        label="X",
        install_url="https://example.com",
        base_url_env="X_BASE_URL",
        base_url_suffix="",
        default_target="https://example.com",
    )
    assert cfg.default_proxy_mode == "reverse"


def test_hermes_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["hermes"]
    assert cfg.cmd == "hermes"
    assert cfg.label == "Hermes Agent"
    assert cfg.default_target == "https://api.openai.com"
    assert cfg.base_url_env == "OPENAI_BASE_URL"
    assert cfg.base_url_suffix == "/v1"
    assert cfg.default_proxy_mode == "forward"


def test_claude_default_proxy_mode_unchanged() -> None:
    assert CLIENT_CONFIGS["claude"].default_proxy_mode == "reverse"


def test_codex_default_proxy_mode_unchanged() -> None:
    assert CLIENT_CONFIGS["codex"].default_proxy_mode == "reverse"
