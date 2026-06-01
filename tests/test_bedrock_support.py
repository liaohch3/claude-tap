"""Tests for AWS Bedrock support in Claude target detection."""

import os
from unittest.mock import patch

import pytest

from claude_tap.cli_clients import CLIENT_CONFIGS, _detect_claude_target


class TestDetectClaudeTargetBedrock:
    def test_bedrock_env_takes_priority(self):
        env = {
            "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock.example.com",
            "ANTHROPIC_BASE_URL": "https://custom.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _detect_claude_target() == "https://bedrock.example.com"

    def test_bedrock_env_used_when_no_base_url(self):
        env = {"ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock.example.com"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("ANTHROPIC_BASE_URL", None)
            assert _detect_claude_target() == "https://bedrock.example.com"

    def test_base_url_used_when_no_bedrock(self):
        env = {"ANTHROPIC_BASE_URL": "https://custom.example.com"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("ANTHROPIC_BEDROCK_BASE_URL", None)
            assert _detect_claude_target() == "https://custom.example.com"

    @patch("claude_tap.cli_clients._read_settings_env_base_url", return_value=None)
    def test_default_when_no_env(self, mock_read):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_BEDROCK_BASE_URL", None)
            os.environ.pop("ANTHROPIC_BASE_URL", None)
            target = _detect_claude_target()
            assert target == "https://api.anthropic.com"


class TestClaudeConfigBedrockEnv:
    def test_extra_base_url_envs_includes_bedrock(self):
        cfg = CLIENT_CONFIGS["claude"]
        assert "ANTHROPIC_BEDROCK_BASE_URL" in cfg.extra_base_url_envs

    def test_reverse_base_url_envs_includes_both(self):
        cfg = CLIENT_CONFIGS["claude"]
        envs = cfg.reverse_base_url_envs
        assert "ANTHROPIC_BASE_URL" in envs
        assert "ANTHROPIC_BEDROCK_BASE_URL" in envs

    def test_reverse_base_url_env_map_sets_both(self):
        cfg = CLIENT_CONFIGS["claude"]
        env_map = cfg.reverse_base_url_env_map(8080)
        assert env_map["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
        assert env_map["ANTHROPIC_BEDROCK_BASE_URL"] == "http://127.0.0.1:8080"
