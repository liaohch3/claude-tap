import re

from claude_tap.proxy import _normalize_request_body_for_upstream


def test_deepseek_metadata_user_id_is_normalized_for_anthropic_target() -> None:
    claude_code_user_id = '{"device_id":"abc123","account_uuid":"","session_id":"ea0aec68-952f-485c-b39a-c6c982d2386d"}'
    body = {
        "model": "deepseek-v4-pro",
        "metadata": {
            "user_id": claude_code_user_id,
            "other": "kept",
        },
    }

    normalized = _normalize_request_body_for_upstream(body, "https://api.deepseek.com/anthropic")

    assert normalized is not body
    assert normalized["metadata"]["other"] == "kept"
    assert normalized["metadata"]["user_id"] != body["metadata"]["user_id"]
    assert re.fullmatch(r"^[a-zA-Z0-9_-]+$", normalized["metadata"]["user_id"])
    assert body["metadata"]["user_id"].startswith('{"device_id"')


def test_deepseek_metadata_valid_user_id_is_left_unchanged() -> None:
    body = {
        "model": "deepseek-v4-pro",
        "metadata": {"user_id": "valid-user_123"},
    }

    normalized = _normalize_request_body_for_upstream(body, "https://api.deepseek.com/anthropic")

    assert normalized is body


def test_metadata_user_id_is_not_normalized_for_default_anthropic_target() -> None:
    body = {
        "model": "claude-sonnet-4-6",
        "metadata": {"user_id": '{"device_id":"abc123"}'},
    }

    normalized = _normalize_request_body_for_upstream(body, "https://api.anthropic.com")

    assert normalized is body
