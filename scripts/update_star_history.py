#!/usr/bin/env python3
"""Generate light and dark star-history charts from GitHub stargazer data."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from claude_tap.models import ProviderPayload

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
STARGAZERS_QUERY = """
query StarHistory($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    stargazers(first: 100, after: $cursor, orderBy: {field: STARRED_AT, direction: ASC}) {
      edges {
        starredAt
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _request_json(request: Request) -> ProviderPayload:
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_stargazer_timestamps(
    repo: str,
    token: str | None,
    request_json: Callable[[Request], ProviderPayload] = _request_json,
) -> list[datetime]:
    """Fetch every stargazer timestamp in chronological order."""
    if not REPO_PATTERN.fullmatch(repo) or any(part in {".", ".."} for part in repo.split("/")):
        raise ValueError("repo must use the OWNER/REPO format")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required to fetch stargazer timestamps")

    owner, name = repo.split("/")
    timestamps: list[datetime] = []
    cursor = None
    page = 0
    while True:
        page += 1
        if page > 1000:
            raise RuntimeError("GitHub stargazers pagination exceeded 1000 pages")
        request = Request(
            GITHUB_GRAPHQL_URL,
            data=json.dumps(
                {
                    "query": STARGAZERS_QUERY,
                    "variables": {"owner": owner, "name": name, "cursor": cursor},
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "claude-tap-star-history",
            },
            method="POST",
        )
        payload = request_json(request)
        if not isinstance(payload, dict) or payload.get("errors"):
            raise RuntimeError("GitHub GraphQL stargazers request failed")
        try:
            stargazers = payload["data"]["repository"]["stargazers"]
            edges = stargazers["edges"]
            page_info = stargazers["pageInfo"]
        except (KeyError, TypeError) as error:
            raise RuntimeError("GitHub GraphQL response omitted stargazer data") from error
        if not isinstance(edges, list):
            raise RuntimeError("GitHub GraphQL stargazer edges were not a list")

        for edge in edges:
            if not isinstance(edge, dict) or not isinstance(edge.get("starredAt"), str):
                raise RuntimeError("GitHub GraphQL response omitted starredAt")
            timestamps.append(datetime.fromisoformat(edge["starredAt"].replace("Z", "+00:00")))

        if not isinstance(page_info, dict) or not isinstance(page_info.get("hasNextPage"), bool):
            raise RuntimeError("GitHub GraphQL response omitted pagination data")
        if not page_info["hasNextPage"]:
            break
        cursor = page_info.get("endCursor")
        if not isinstance(cursor, str) or not cursor:
            raise RuntimeError("GitHub GraphQL response omitted the next cursor")

    if not timestamps:
        raise RuntimeError(f"{repo} has no stargazer timestamps")
    return sorted(timestamps)


def render_charts(repo: str, timestamps: list[datetime], output_dir: Path) -> None:
    """Render deterministic 1600x900 PNG charts for light and dark themes."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import StrMethodFormatter

    output_dir.mkdir(parents=True, exist_ok=True)
    counts = list(range(1, len(timestamps) + 1))
    latest = timestamps[-1].strftime("%b %d, %Y").replace(" 0", " ")

    themes = {
        "light": {
            "background": "#ffffff",
            "foreground": "#18212f",
            "muted": "#5c6675",
            "grid": "#d7dce3",
            "line": "#1677ff",
            "fill": "#d9eaff",
        },
        "dark": {
            "background": "#0d1117",
            "foreground": "#f0f3f6",
            "muted": "#9da7b3",
            "grid": "#30363d",
            "line": "#58a6ff",
            "fill": "#173b66",
        },
    }

    for name, colors in themes.items():
        fig, ax = plt.subplots(figsize=(16, 9), dpi=100)
        fig.patch.set_facecolor(colors["background"])
        ax.set_facecolor(colors["background"])
        ax.step(timestamps, counts, where="post", color=colors["line"], linewidth=3)
        ax.fill_between(
            timestamps,
            counts,
            step="post",
            color=colors["fill"],
            alpha=0.7,
        )
        ax.scatter(
            timestamps[-1],
            counts[-1],
            color=colors["line"],
            edgecolor=colors["background"],
            linewidth=2,
            s=90,
            zorder=3,
        )

        ax.text(
            0,
            1.02,
            f"{len(timestamps):,} stars | Through {latest}",
            transform=ax.transAxes,
            color=colors["muted"],
            fontsize=16,
            va="bottom",
        )
        ax.set_ylabel("GitHub stars", color=colors["muted"], fontsize=14, labelpad=14)
        ax.grid(axis="both", color=colors["grid"], linewidth=1, alpha=0.75)
        ax.set_axisbelow(True)
        ax.margins(x=0.02, y=0.08)

        locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
        ax.tick_params(axis="both", colors=colors["muted"], labelsize=13)
        for spine in ax.spines.values():
            spine.set_color(colors["grid"])

        fig.subplots_adjust(left=0.09, right=0.96, top=0.9, bottom=0.11)
        fig.savefig(
            output_dir / f"star-history-{name}.png",
            dpi=100,
            facecolor=colors["background"],
            metadata={"Software": "claude-tap"},
        )
        plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="liaohch3/claude-tap")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    timestamps = fetch_stargazer_timestamps(args.repo, os.environ.get("GITHUB_TOKEN"))
    render_charts(args.repo, timestamps, args.output_dir)
    print(f"Generated star history for {len(timestamps):,} stargazers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
