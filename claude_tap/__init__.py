#!/usr/bin/env python3
"""claude-tap: Reverse proxy to trace Claude Code API requests.

A CLI tool that wraps Claude Code with a local reverse proxy to intercept
and record all API requests. Useful for studying Claude Code's Context
Engineering.
"""

from __future__ import annotations

__version__ = "0.1.3"
__all__ = [
    "__version__",
    "main_entry",
    "parse_args",
    "async_main",
    "SSEReassembler",
    "TraceWriter",
    "filter_headers",
    "MODEL_PRICING",
    "_get_model_pricing",
    "_calculate_cost",
]

import argparse
import asyncio
import gzip
import json
import logging
import os
import shutil
import signal
import sys
import time
import uuid
import webbrowser
import zlib
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web
from anthropic.lib.streaming._messages import accumulate_event
from anthropic.types import RawMessageStreamEvent
from pydantic import TypeAdapter

# Ensure print output is visible immediately (uv tool pipes stdout with full buffering)
sys.stdout.reconfigure(line_buffering=True)

log = logging.getLogger("claude-tap")

_sse_event_adapter = TypeAdapter(RawMessageStreamEvent)


# ---------------------------------------------------------------------------
# SSEReassembler – parse SSE bytes, use Anthropic SDK to rebuild Message
# ---------------------------------------------------------------------------


class SSEReassembler:
    """Parse raw SSE bytes and use the Anthropic SDK's accumulate_event()
    to reconstruct the full API response object."""

    def __init__(self):
        self.events: list[dict] = []
        self._buf = b""
        self._current_event: str | None = None
        self._current_data_lines: list[str] = []
        self._snapshot = None  # anthropic ParsedMessage

    def feed_bytes(self, chunk: bytes):
        self._buf += chunk
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            self._feed_line(line.decode("utf-8", errors="replace"))

    def _feed_line(self, line: str):
        line = line.rstrip("\r")
        if line.startswith("event:"):
            self._current_event = line[len("event:") :].strip()
            self._current_data_lines = []
        elif line.startswith("data:"):
            self._current_data_lines.append(line[len("data:") :].strip())
        elif line == "":
            if self._current_event is not None:
                raw_data = "\n".join(self._current_data_lines)
                try:
                    data = json.loads(raw_data)
                except (json.JSONDecodeError, ValueError):
                    data = raw_data
                event_record = {"event": self._current_event, "data": data}
                self.events.append(event_record)
                self._accumulate(data)
                self._current_event = None
                self._current_data_lines = []

    def _accumulate(self, data):
        if not isinstance(data, dict):
            return
        try:
            event = _sse_event_adapter.validate_python(data)
            self._snapshot = accumulate_event(
                event=event,
                current_snapshot=self._snapshot,
            )
        except Exception:
            pass

    def reconstruct(self) -> dict | None:
        if self._snapshot is None:
            return None
        return self._snapshot.to_dict()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Pricing (USD per 1M tokens) - from platform.claude.com/docs/en/about-claude/pricing
# Last updated: 2026-02
# ---------------------------------------------------------------------------

MODEL_PRICING: dict[str, dict[str, float]] = {
    # Claude Opus 4.5/4.6 (latest, cheaper than 4.0/4.1)
    "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
    "claude-opus-4-5": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
    # Claude Opus 4.0/4.1 (older, more expensive)
    "claude-opus-4-1": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-opus-4": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    # Claude Sonnet 4.x
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    # Claude Haiku 4.5 (more expensive than 3.5)
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25},
    # Claude Haiku 3.5
    "claude-haiku-3-5": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    # Claude Haiku 3
    "claude-haiku-3": {"input": 0.25, "output": 1.25, "cache_read": 0.03, "cache_write": 0.3},
}


def _get_model_pricing(model: str) -> dict[str, float]:
    """Get pricing for a model, falling back to sonnet pricing if unknown."""
    # Normalize model name (remove date suffixes like -20250514)
    base_model = model.rsplit("-", 1)[0] if model and "-20" in model else model
    for key in MODEL_PRICING:
        if key in (base_model or ""):
            return MODEL_PRICING[key]
    # Default to sonnet pricing
    return MODEL_PRICING["claude-sonnet-4"]


def _calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_create_tokens: int = 0,
) -> float:
    """Calculate cost in USD for a single API call."""
    pricing = _get_model_pricing(model)
    cost = (
        (input_tokens * pricing["input"] / 1_000_000)
        + (output_tokens * pricing["output"] / 1_000_000)
        + (cache_read_tokens * pricing["cache_read"] / 1_000_000)
        + (cache_create_tokens * pricing["cache_write"] / 1_000_000)
    )
    return cost


# ---------------------------------------------------------------------------
# TraceWriter – async JSONL writer with stats
# ---------------------------------------------------------------------------


class TraceWriter:
    """Writes trace records to a JSONL file and accumulates statistics."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self.count = 0
        # Token statistics
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_create_tokens = 0
        self.total_cost = 0.0
        self.models_used: dict[str, int] = {}
        path.parent.mkdir(parents=True, exist_ok=True)

    async def write(self, record: dict) -> None:
        """Write a record and update statistics."""
        async with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.count += 1
            self._update_stats(record)

    def _update_stats(self, record: dict) -> None:
        """Extract token usage from record and update totals."""
        # Get model from request body
        req_body = record.get("request", {}).get("body", {})
        model = req_body.get("model", "unknown")

        # Track model usage
        self.models_used[model] = self.models_used.get(model, 0) + 1

        # Get usage from response body (works for both streaming and non-streaming)
        resp_body = record.get("response", {}).get("body", {})
        usage = resp_body.get("usage", {})

        # For streaming responses, usage might be in the reconstructed message
        if not usage and isinstance(resp_body, dict):
            usage = resp_body

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cache_read_tokens += cache_read
        self.total_cache_create_tokens += cache_create
        self.total_cost += _calculate_cost(model, input_tokens, output_tokens, cache_read, cache_create)

    def get_summary(self) -> dict:
        """Return a summary of the trace statistics."""
        return {
            "api_calls": self.count,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "cache_read_tokens": self.total_cache_read_tokens,
            "cache_create_tokens": self.total_cache_create_tokens,
            "total_cost_usd": round(self.total_cost, 4),
            "models_used": self.models_used,
        }


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


def filter_headers(headers: dict[str, str], *, redact_keys: bool = False) -> dict[str, str]:
    """Filter hop-by-hop headers and optionally redact sensitive values."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in HOP_BY_HOP:
            continue
        if redact_keys and k.lower() in ("x-api-key", "authorization"):
            out[k] = v[:12] + "..." if len(v) > 12 else "***"
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Proxy handler
# ---------------------------------------------------------------------------


async def proxy_handler(request: web.Request) -> web.StreamResponse:
    ctx: dict = request.app["trace_ctx"]
    target: str = ctx["target_url"]
    writer: TraceWriter = ctx["writer"]
    session: aiohttp.ClientSession = ctx["session"]

    upstream_url = target.rstrip("/") + "/" + request.path_qs.lstrip("/")

    body = await request.read()

    fwd_headers = filter_headers(request.headers)
    fwd_headers.pop("Host", None)

    req_id = f"req_{uuid.uuid4().hex[:12]}"
    t0 = time.monotonic()

    # Parse request body
    try:
        req_body = json.loads(body) if body else None
    except (json.JSONDecodeError, ValueError):
        req_body = body.decode("utf-8", errors="replace") if body else None

    is_streaming = False
    if isinstance(req_body, dict):
        is_streaming = req_body.get("stream", False)

    ctx["turn_counter"] = ctx.get("turn_counter", 0) + 1
    turn = ctx["turn_counter"]

    model = req_body.get("model", "") if isinstance(req_body, dict) else ""
    log_prefix = f"[Turn {turn}]"
    log.info(f"{log_prefix} → {request.method} {request.path} (model={model}, stream={is_streaming})")

    # For streaming requests, ask upstream not to compress (we need to parse SSE text)
    if is_streaming:
        fwd_headers["Accept-Encoding"] = "identity"

    try:
        upstream_resp = await session.request(
            method=request.method,
            url=upstream_url,
            headers=fwd_headers,
            data=body,
            timeout=aiohttp.ClientTimeout(total=600, sock_read=300),
        )
    except Exception as exc:
        log.error(
            f"{log_prefix} upstream error while requesting {upstream_url}: {exc}  "
            f"-- Check that the target ({target}) is reachable."
        )
        return web.Response(status=502, text=str(exc))

    if is_streaming and upstream_resp.status == 200:
        resp_body = await _handle_streaming(
            request, upstream_resp, req_id, turn, t0, body, req_body, writer, log_prefix
        )
        return resp_body
    else:
        return await _handle_non_streaming(request, upstream_resp, req_id, turn, t0, body, req_body, writer, log_prefix)


async def _handle_streaming(
    request: web.Request,
    upstream_resp: aiohttp.ClientResponse,
    req_id: str,
    turn: int,
    t0: float,
    raw_body: bytes,
    req_body,
    writer: TraceWriter,
    log_prefix: str,
) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=upstream_resp.status,
        headers={k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP},
    )
    await resp.prepare(request)

    reassembler = SSEReassembler()

    try:
        async for chunk in upstream_resp.content.iter_any():
            await resp.write(chunk)
            reassembler.feed_bytes(chunk)
    except (ConnectionError, asyncio.CancelledError):
        pass

    try:
        await resp.write_eof()
    except (ConnectionError, ConnectionResetError, Exception):
        pass

    duration_ms = int((time.monotonic() - t0) * 1000)
    reconstructed = reassembler.reconstruct()

    usage = reconstructed.get("usage", {}) if reconstructed else {}
    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_create = usage.get("cache_creation_input_tokens", 0)
    log.info(
        f"{log_prefix} ← 200 stream done ({duration_ms}ms, in={in_tok} out={out_tok} cache_read={cache_read} cache_create={cache_create})"
    )

    record = _build_record(
        req_id,
        turn,
        duration_ms,
        request.method,
        request.path_qs,
        request.headers,
        req_body,
        upstream_resp.status,
        upstream_resp.headers,
        reconstructed,
        sse_events=reassembler.events,
    )
    await writer.write(record)

    return resp


async def _handle_non_streaming(
    request: web.Request,
    upstream_resp: aiohttp.ClientResponse,
    req_id: str,
    turn: int,
    t0: float,
    raw_body: bytes,
    req_body,
    writer: TraceWriter,
    log_prefix: str,
) -> web.Response:
    resp_bytes = await upstream_resp.read()
    duration_ms = int((time.monotonic() - t0) * 1000)

    # Decompress for JSON parsing (raw bytes are forwarded as-is to client)
    content_encoding = upstream_resp.headers.get("Content-Encoding", "").lower()
    decode_bytes = resp_bytes
    if resp_bytes and content_encoding in ("gzip", "deflate"):
        try:
            if content_encoding == "gzip":
                decode_bytes = gzip.decompress(resp_bytes)
            else:
                decode_bytes = zlib.decompress(resp_bytes)
        except Exception:
            pass

    try:
        resp_body = json.loads(decode_bytes) if decode_bytes else None
    except (json.JSONDecodeError, ValueError):
        resp_body = decode_bytes.decode("utf-8", errors="replace") if decode_bytes else None

    log.info(f"{log_prefix} ← {upstream_resp.status} ({duration_ms}ms, {len(resp_bytes)} bytes)")

    record = _build_record(
        req_id,
        turn,
        duration_ms,
        request.method,
        request.path_qs,
        request.headers,
        req_body,
        upstream_resp.status,
        upstream_resp.headers,
        resp_body,
    )
    await writer.write(record)

    return web.Response(
        status=upstream_resp.status,
        headers={k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP},
        body=resp_bytes,
    )


def _build_record(
    req_id: str,
    turn: int,
    duration_ms: int,
    method: str,
    path_qs: str,
    req_headers: dict,
    req_body: dict | None,
    status: int,
    resp_headers: dict,
    resp_body: dict | None,
    sse_events: list[dict] | None = None,
) -> dict:
    """Build a trace record for a single API call."""
    record: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": req_id,
        "turn": turn,
        "duration_ms": duration_ms,
        "request": {
            "method": method,
            "path": path_qs,
            "headers": filter_headers(req_headers, redact_keys=True),
            "body": req_body,
        },
        "response": {
            "status": status,
            "headers": filter_headers(resp_headers),
            "body": resp_body,
        },
    }
    if sse_events is not None:
        record["response"]["sse_events"] = sse_events
    return record


# ---------------------------------------------------------------------------
# Claude launcher
# ---------------------------------------------------------------------------


async def run_claude(port: int, extra_args: list[str]) -> int:
    if shutil.which("claude") is None:
        print(
            "\nError: 'claude' command not found in PATH.\n"
            "Please install Claude Code first: "
            "https://docs.anthropic.com/en/docs/claude-code\n"
        )
        return 1

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["NO_PROXY"] = "127.0.0.1"
    # Bypass Claude Code nesting detection
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_SSE_PORT", None)

    cmd = ["claude"] + extra_args
    print(f"\n🚀 Starting Claude Code: {' '.join(cmd)}")
    print(f"   ANTHROPIC_BASE_URL=http://127.0.0.1:{port}\n")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdin=None,
        stdout=None,
        stderr=None,
    )

    # Forward SIGINT to child
    loop = asyncio.get_running_loop()

    def _fwd_signal():
        if proc.returncode is None:
            proc.send_signal(signal.SIGINT)

    try:
        loop.add_signal_handler(signal.SIGINT, _fwd_signal)
    except NotImplementedError:
        pass

    code = await proc.wait()
    print(f"\n📋 Claude Code exited with code {code}")
    return code


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def async_main(args: argparse.Namespace):
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    trace_path = output_dir / f"trace_{ts}.jsonl"
    log_path = output_dir / f"trace_{ts}.log"
    writer = TraceWriter(trace_path)

    # Proxy logs go to file, not terminal (avoids polluting Claude TUI)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(file_handler)
    log.setLevel(logging.DEBUG)
    # Suppress aiohttp access logs
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    session = aiohttp.ClientSession(auto_decompress=False)

    app = web.Application()
    app["trace_ctx"] = {
        "target_url": args.target,
        "writer": writer,
        "session": session,
        "turn_counter": 0,
    }
    app.router.add_route("*", "/{path_info:.*}", proxy_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", args.port)
    await site.start()

    # Resolve actual port (site._server is a private API; fall back to args.port)
    try:
        actual_port = site._server.sockets[0].getsockname()[1]
    except (AttributeError, IndexError, OSError):
        actual_port = args.port
    print(f"🔍 Trace proxy listening on http://127.0.0.1:{actual_port}")
    print(f"📁 Trace file: {trace_path}")

    exit_code = 0
    if not args.no_launch:
        try:
            exit_code = await run_claude(actual_port, args.claude_args)
        except asyncio.CancelledError:
            pass
    else:
        print("\n--no-launch mode: proxy running. Press Ctrl+C to stop.")
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    await session.close()
    await runner.cleanup()

    # Generate self-contained HTML viewer
    html_path = trace_path.with_suffix(".html")
    _generate_html_viewer(trace_path, html_path)

    # Print summary with cost estimation
    stats = writer.get_summary()
    print("\n📊 Trace summary:")
    print(f"   API calls: {stats['api_calls']}")

    # Token breakdown
    total_tokens = stats["input_tokens"] + stats["output_tokens"]
    if total_tokens > 0:
        print(f"   Tokens: {stats['input_tokens']:,} in / {stats['output_tokens']:,} out", end="")
        if stats["cache_read_tokens"] > 0:
            print(f" / {stats['cache_read_tokens']:,} cache_read", end="")
        if stats["cache_create_tokens"] > 0:
            print(f" / {stats['cache_create_tokens']:,} cache_write", end="")
        print()

        # Cost estimation
        if stats["total_cost_usd"] > 0:
            print(f"   Est. cost: ${stats['total_cost_usd']:.4f}")

    # Output files
    print(f"   Trace: {trace_path}")
    print(f"   Log:   {log_path}")
    print(f"   View:  {html_path}")

    # Open viewer in browser if requested
    if args.open_viewer and html_path.exists():
        print("\n🌐 Opening viewer in browser...")
        webbrowser.open(f"file://{html_path.absolute()}")

    return exit_code


def _generate_html_viewer(trace_path: Path, html_path: Path) -> None:
    """Read viewer.html template, embed JSONL data, write self-contained HTML."""
    template = Path(__file__).parent / "viewer.html"
    if not template.exists():
        return

    # Read JSONL records
    records = []
    if trace_path.exists():
        with open(trace_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(line)

    # Build embedded data script — each line is already valid JSON
    data_js = "const EMBEDDED_TRACE_DATA = [\n" + ",\n".join(records) + "\n];\n"

    html = template.read_text(encoding="utf-8")
    # Inject data script before the main <script> tag
    html = html.replace(
        "<script>\nconst $ = s =>",
        f"<script>\n{data_js}</script>\n<script>\nconst $ = s =>",
        1,
    )
    html_path.write_text(html, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse argv, extracting ``--tap-*`` flags for ourselves and forwarding
    everything else to ``claude``.
    """
    if argv is None:
        argv = sys.argv[1:]

    tap_parser = argparse.ArgumentParser(
        prog="claude-tap",
        description="Trace Claude Code API requests via a local reverse proxy. "
        "All flags not listed below are forwarded to claude.",
    )
    tap_parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    tap_parser.add_argument(
        "--tap-output-dir", default="./.traces", dest="output_dir", help="Trace output directory (default: ./.traces)"
    )
    tap_parser.add_argument("--tap-port", type=int, default=0, dest="port", help="Proxy port (default: 0 = auto)")
    tap_parser.add_argument(
        "--tap-target",
        default="https://api.anthropic.com",
        dest="target",
        help="Upstream API URL (default: https://api.anthropic.com)",
    )
    tap_parser.add_argument(
        "--tap-no-launch", action="store_true", dest="no_launch", help="Only start the proxy, don't launch Claude"
    )
    tap_parser.add_argument(
        "--tap-open", action="store_true", dest="open_viewer", help="Open HTML viewer in browser after exit"
    )
    args, claude_args = tap_parser.parse_known_args(argv)
    args.claude_args = claude_args
    return args


def main_entry() -> None:
    """Entry point for the claude-tap CLI."""
    args = parse_args()
    try:
        code = asyncio.run(async_main(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    args = parse_args()
    try:
        code = asyncio.run(async_main(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)
