"""claude-tap: Proxy to trace Claude Code API requests.

A CLI tool that wraps Claude Code with a local proxy (reverse or forward)
to intercept and record all API requests. Useful for studying Claude Code's
Context Engineering.
"""

from __future__ import annotations

from claude_tap.certs import CertificateAuthority, ensure_ca
from claude_tap.cli import (
    __version__,
    _build_update_command,
    _detect_installer,
    _version_tuple,
    async_main,
    dashboard_main,
    main_entry,
    parse_args,
    parse_dashboard_args,
    parse_trust_ca_args,
    parse_update_args,
    trust_ca_main,
    update_main,
)
from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.history import cleanup_trace_sessions, delete_trace_history, migrate_legacy_traces
from claude_tap.live import LiveViewerServer
from claude_tap.proxy import filter_headers
from claude_tap.sse import SSEReassembler
from claude_tap.trace import TraceWriter
from claude_tap.trace_store import get_trace_store, reset_trace_store, resolve_db_path
from claude_tap.viewer import _generate_html_viewer

__all__ = [
    "__version__",
    "_build_update_command",
    "_detect_installer",
    "_version_tuple",
    "main_entry",
    "parse_args",
    "parse_dashboard_args",
    "parse_trust_ca_args",
    "parse_update_args",
    "trust_ca_main",
    "update_main",
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
    "cleanup_trace_sessions",
    "delete_trace_history",
    "migrate_legacy_traces",
    "get_trace_store",
    "reset_trace_store",
    "resolve_db_path",
]
