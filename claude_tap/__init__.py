"""claude-tap: Proxy to trace Claude Code API requests.

A CLI tool that wraps Claude Code with a local proxy (reverse or forward)
to intercept and record all API requests. Useful for studying Claude Code's
Context Engineering.
"""

from __future__ import annotations

from claude_tap.certs import CertificateAuthority, ensure_ca
from claude_tap.cli import (
    __version__,
    _cleanup_traces,
    _detect_installer,
    _load_manifest,
    _register_trace,
    _save_manifest,
    _version_tuple,
    async_main,
    dashboard_main,
    main_entry,
    parse_args,
    parse_dashboard_args,
)
from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.live import LiveViewerServer
from claude_tap.proxy import filter_headers
from claude_tap.sse import SSEReassembler
from claude_tap.trace import TraceWriter
from claude_tap.viewer import _generate_html_viewer

__all__ = [
    "__version__",
    "_cleanup_traces",
    "_detect_installer",
    "_load_manifest",
    "_register_trace",
    "_save_manifest",
    "_version_tuple",
    "main_entry",
    "parse_args",
    "parse_dashboard_args",
    "async_main",
    "dashboard_main",
    "CertificateAuthority",
    "ensure_ca",
    "ForwardProxyServer",
    "SSEReassembler",
    "TraceWriter",
    "LiveViewerServer",
    "filter_headers",
    "_generate_html_viewer",
]
