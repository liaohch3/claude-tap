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
from urllib.request import Request, urlopen

GITHUB_API_VERSION = "2022-11-28"
STARGAZER_ACCEPT = "application/vnd.github.star+json"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _request_json(request: Request) -> object:
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_stargazer_timestamps(
    repo: str,
    token: str | None = None,
    request_json: Callable[[Request], object] = _request_json,
) -> list[datetime]:
    """Fetch every stargazer timestamp in chronological order."""
    if not REPO_PATTERN.fullmatch(repo) or any(part in {".", ".."} for part in repo.split("/")):
        raise ValueError("repo must use the OWNER/REPO format")

    timestamps: list[datetime] = []
    page = 1
    while True:
        if page > 1000:
            raise RuntimeError("GitHub stargazers pagination exceeded 1000 pages")
        request = Request(
            f"https://api.github.com/repos/{repo}/stargazers?per_page=100&page={page}",
            headers={
                "Accept": STARGAZER_ACCEPT,
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "claude-tap-star-history",
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
        )
        payload = request_json(request)
        if not isinstance(payload, list):
            raise RuntimeError("GitHub stargazers response was not a list")

        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("starred_at"), str):
                raise RuntimeError("GitHub stargazers response omitted starred_at")
            timestamps.append(datetime.fromisoformat(item["starred_at"].replace("Z", "+00:00")))

        if len(payload) < 100:
            break
        page += 1

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
    repo_name = repo.split("/", 1)[1]

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

        ax.set_title(
            f"{repo_name} Star History",
            color=colors["foreground"],
            fontsize=30,
            fontweight="bold",
            loc="left",
            pad=28,
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

        fig.subplots_adjust(left=0.09, right=0.96, top=0.84, bottom=0.11)
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
