"""Tests for AWS Bedrock support in Claude target detection."""

import os
from unittest.mock import patch

from claude_tap.cli_clients import (
    CLIENT_CONFIGS,
    _detect_claude_target,
    _is_aws_native_bedrock_url,
)


class TestIsAwsNativeBedrockUrl:
    def test_aws_native_endpoint(self):
        assert _is_aws_native_bedrock_url("https://bedrock-runtime.us-east-1.amazonaws.com") is True

    def test_aws_fips_endpoint(self):
        assert _is_aws_native_bedrock_url("https://bedrock-runtime-fips.us-west-2.amazonaws.com") is True

    def test_aws_vpce_endpoint(self):
        assert _is_aws_native_bedrock_url("https://vpce-xxx.bedrock-runtime.us-east-1.vpce.amazonaws.com") is True

    def test_aws_china_endpoint(self):
        assert _is_aws_native_bedrock_url("https://bedrock-runtime.cn-north-1.amazonaws.com.cn") is True

    def test_aws_api_aws_endpoint(self):
        assert _is_aws_native_bedrock_url("https://bedrock-mantle.us-east-1.api.aws") is True

    def test_api_gateway_not_native(self):
        assert _is_aws_native_bedrock_url("https://abc123.execute-api.us-east-1.amazonaws.com/bedrock") is False

    def test_custom_gateway(self):
        assert _is_aws_native_bedrock_url("https://ai-gateway.internal.example.com/bedrock") is False

    def test_custom_company_proxy(self):
        assert _is_aws_native_bedrock_url("https://ai-gateway.internal.company.com/bedrock") is False

    def test_empty_string(self):
        assert _is_aws_native_bedrock_url("") is False


class TestDetectClaudeTargetBedrock:
    def test_custom_bedrock_gateway_takes_priority(self):
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://ai-gateway.internal.example.com/bedrock",
            "ANTHROPIC_BASE_URL": "https://custom.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _detect_claude_target() == "https://ai-gateway.internal.example.com/bedrock"

    def test_bedrock_gateway_ignored_when_bedrock_mode_is_off(self):
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "0",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://ai-gateway.internal.example.com/bedrock",
            "ANTHROPIC_BASE_URL": "https://custom.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _detect_claude_target() == "https://custom.example.com"

    def test_aws_native_bedrock_skipped_falls_to_base_url(self):
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock-runtime.us-east-1.amazonaws.com",
            "ANTHROPIC_BASE_URL": "https://custom.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _detect_claude_target() == "https://custom.example.com"

    def test_aws_native_bedrock_skipped_falls_to_default(self):
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock-runtime.us-east-1.amazonaws.com",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("ANTHROPIC_BASE_URL", None)
            with patch("claude_tap.cli_clients._read_settings_env_base_url", return_value=None):
                assert _detect_claude_target() == "https://api.anthropic.com"

    def test_api_aws_bedrock_skipped_falls_to_base_url(self):
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock-mantle.us-east-1.api.aws",
            "ANTHROPIC_BASE_URL": "https://custom.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _detect_claude_target() == "https://custom.example.com"

    def test_custom_bedrock_gateway_from_settings_takes_priority(self):
        values = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://ai-gateway.internal.example.com/bedrock",
            "ANTHROPIC_BASE_URL": "https://custom.example.com",
        }

        def fake_read(_path, env_key):
            return values.get(env_key)

        with patch.dict(os.environ, {}, clear=False):
            for key in values:
                os.environ.pop(key, None)
            with patch("claude_tap.cli_clients._read_settings_env_base_url", side_effect=fake_read):
                assert _detect_claude_target() == "https://ai-gateway.internal.example.com/bedrock"

    def test_base_url_used_when_no_bedrock(self):
        env = {"ANTHROPIC_BASE_URL": "https://custom.example.com"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)
            os.environ.pop("ANTHROPIC_BEDROCK_BASE_URL", None)
            assert _detect_claude_target() == "https://custom.example.com"

    @patch("claude_tap.cli_clients._read_settings_env_base_url", return_value=None)
    def test_default_when_no_env(self, mock_read):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)
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

    def test_env_map_rewrites_custom_gateway(self):
        """Custom gateways (non-AWS) should be rewritten to localhost."""
        cfg = CLIENT_CONFIGS["claude"]
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://ai-gateway.internal.example.com/bedrock",
        }
        with patch.dict(os.environ, env, clear=False):
            env_map = cfg.reverse_base_url_env_map(8080)
            assert env_map["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
            assert env_map["ANTHROPIC_BEDROCK_BASE_URL"] == "http://127.0.0.1:8080"

    def test_env_map_ignores_bedrock_when_bedrock_mode_is_off(self):
        cfg = CLIENT_CONFIGS["claude"]
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "0",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://ai-gateway.internal.example.com/bedrock",
        }
        with patch.dict(os.environ, env, clear=False):
            env_map = cfg.reverse_base_url_env_map(8080)
            assert env_map["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
            assert "ANTHROPIC_BEDROCK_BASE_URL" not in env_map

    def test_env_map_skips_aws_native(self):
        """AWS native endpoints must NOT be rewritten (SigV4 would fail)."""
        cfg = CLIENT_CONFIGS["claude"]
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock-runtime.us-east-1.amazonaws.com",
        }
        with patch.dict(os.environ, env, clear=False):
            env_map = cfg.reverse_base_url_env_map(8080)
            assert env_map["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
            assert "ANTHROPIC_BEDROCK_BASE_URL" not in env_map

    def test_env_map_skips_api_aws_native(self):
        cfg = CLIENT_CONFIGS["claude"]
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock-mantle.us-east-1.api.aws",
        }
        with patch.dict(os.environ, env, clear=False):
            env_map = cfg.reverse_base_url_env_map(8080)
            assert env_map["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
            assert "ANTHROPIC_BEDROCK_BASE_URL" not in env_map

    def test_env_map_skips_aws_native_from_settings(self):
        cfg = CLIENT_CONFIGS["claude"]
        values = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock-runtime.us-east-1.amazonaws.com",
        }

        def fake_read(_path, env_key):
            return values.get(env_key)

        with patch.dict(os.environ, {}, clear=False):
            for key in values:
                os.environ.pop(key, None)
            with patch("claude_tap.cli_clients._read_settings_env_base_url", side_effect=fake_read):
                env_map = cfg.reverse_base_url_env_map(8080)
                assert env_map["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
                assert "ANTHROPIC_BEDROCK_BASE_URL" not in env_map
