"""Tests for the GitHub-native star-history chart updater."""

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from scripts.check_screenshots import analyze_file
from scripts.update_star_history import fetch_stargazer_timestamps, render_charts


def test_fetch_stargazer_timestamps_paginates_and_authenticates():
    requests = []
    first_page = [{"starred_at": f"2026-01-01T00:{minute:02d}:00Z"} for minute in range(60)] + [
        {"starred_at": f"2026-01-02T00:{minute:02d}:00Z"} for minute in range(40)
    ]
    second_page = [{"starred_at": "2026-01-03T00:00:00Z"}]

    def request_json(request):
        requests.append(request)
        page = parse_qs(urlparse(request.full_url).query)["page"][0]
        return first_page if page == "1" else second_page

    timestamps = fetch_stargazer_timestamps(
        "liaohch3/claude-tap",
        token="test-token",
        request_json=request_json,
    )

    assert len(timestamps) == 101
    assert timestamps[0] == datetime(2026, 1, 1, tzinfo=UTC)
    assert timestamps[-1] == datetime(2026, 1, 3, tzinfo=UTC)
    assert [request.full_url.rsplit("=", 1)[-1] for request in requests] == ["1", "2"]
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[0].headers["Accept"] == "application/vnd.github.star+json"


def test_fetch_stargazer_timestamps_rejects_malformed_payload():
    with pytest.raises(RuntimeError, match="omitted starred_at"):
        fetch_stargazer_timestamps(
            "liaohch3/claude-tap",
            request_json=lambda _request: [{"user": {"login": "example"}}],
        )


@pytest.mark.parametrize("repo", ["claude-tap", "owner/repo/extra", "../repo"])
def test_fetch_stargazer_timestamps_rejects_invalid_repo(repo):
    with pytest.raises(ValueError, match="OWNER/REPO"):
        fetch_stargazer_timestamps(repo, request_json=lambda _request: [])


def test_render_charts_persists_valid_light_and_dark_pngs(tmp_path):
    timestamps = [
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 6, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
    ]

    render_charts("liaohch3/claude-tap", timestamps, tmp_path)

    light = tmp_path / "star-history-light.png"
    dark = tmp_path / "star-history-dark.png"
    for chart in (light, dark):
        result = analyze_file(chart)
        assert result.status == "PASS", result.failures + result.warnings
        assert result.info is not None
        assert (result.info.width, result.info.height) == (1600, 900)
        assert chart.stat().st_size > 10_000
    assert light.read_bytes() != dark.read_bytes()


def test_star_history_workflow_publishes_to_asset_branch():
    workflow = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "star-history.yml").read_text()

    assert 'cron: "17 3 * * *"' in workflow
    assert "ref: star-history-assets" in workflow
    assert "scripts/update_star_history.py" in workflow
    assert "scripts/check_screenshots.py" in workflow
    assert "matplotlib==3.11.1" in workflow
    assert "timeout-minutes: 10" in workflow
    assert "git diff --cached --quiet" in workflow
    assert workflow.index("git add star-history-light.png") < workflow.index("git diff --cached --quiet")
    assert "git push" in workflow
