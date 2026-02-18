"""claude-tap: Reverse proxy to trace Claude Code API requests.

A CLI tool that wraps Claude Code with a local reverse proxy to intercept
and record all API requests. Useful for studying Claude Code's Context
Engineering.
"""

from __future__ import annotations

from claude_tap.cli import __version__, async_main, main_entry, parse_args
from claude_tap.live import LiveViewerServer
from claude_tap.proxy import filter_headers
from claude_tap.sse import SSEReassembler
from claude_tap.trace import TraceWriter
from claude_tap.viewer import _generate_html_viewer

__all__ = [
    "__version__",
    "main_entry",
    "parse_args",
    "async_main",
    "SSEReassembler",
    "TraceWriter",
    "LiveViewerServer",
    "filter_headers",
    "_generate_html_viewer",
]
