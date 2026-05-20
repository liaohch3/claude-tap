"""LiveViewerServer - SSE-based real-time trace viewer."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from pathlib import Path

from aiohttp import web

from claude_tap.dashboard import (
    dashboard_trace_snapshot,
    list_trace_agents,
    list_trace_sessions,
    load_trace_session,
    read_dashboard_template,
    session_id_for_rel_path,
)
from claude_tap.history import delete_trace_history
from claude_tap.viewer import VIEWER_SCRIPT_ANCHOR, VIEWER_TEMPLATE_PATH, _read_viewer_template

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class LiveViewerServer:
    """HTTP server for real-time trace viewing via SSE."""

    def __init__(
        self,
        trace_path: Path,
        port: int = 0,
        host: str = "127.0.0.1",
        output_dir: Path | None = None,
        dashboard_mode: bool = False,
    ):
        self.trace_path = trace_path
        self.port = port
        self.host = host
        self.output_dir = output_dir
        self.dashboard_mode = dashboard_mode
        self._sse_clients: list[web.StreamResponse] = []
        self._dashboard_clients: list[web.StreamResponse] = []
        self._records: list[dict] = []
        self._current_date: str = date.today().isoformat()
        self._lock = asyncio.Lock()
        self._runner: web.AppRunner | None = None
        self._actual_port: int = 0
        self._shutdown_event = asyncio.Event()
        self._dashboard_watch_task: asyncio.Task | None = None
        self._dashboard_snapshot: dict[str, tuple[int, int]] = {}

    async def start(self) -> int:
        """Start the viewer server and return the actual port."""
        app = web.Application()
        if self.dashboard_mode:
            app.router.add_get("/", self._handle_dashboard_index)
        else:
            app.router.add_get("/", self._handle_index)
        app.router.add_get("/viewer", self._handle_index)
        app.router.add_get("/dashboard", self._handle_dashboard_index)
        app.router.add_get("/dashboard/events", self._handle_dashboard_sse)
        app.router.add_get("/events", self._handle_sse)
        app.router.add_get("/records", self._handle_records)
        app.router.add_get("/api/dates", self._handle_dates)
        app.router.add_get("/api/traces/{date}", self._handle_traces_by_date)
        app.router.add_delete("/api/traces/{date}", self._handle_delete_traces_by_date)
        app.router.add_get("/api/agents", self._handle_agents)
        app.router.add_get("/api/sessions", self._handle_sessions)
        app.router.add_get("/api/sessions/{session_id}/records", self._handle_session_records)
        app.router.add_get("/api/sessions/{session_id}/html", self._handle_session_html)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()

        try:
            self._actual_port = site._server.sockets[0].getsockname()[1]
        except (AttributeError, IndexError, OSError):
            self._actual_port = self.port

        if self.dashboard_mode and self.output_dir:
            self._dashboard_snapshot = dashboard_trace_snapshot(self.output_dir)
            self._dashboard_watch_task = asyncio.create_task(self._watch_dashboard_files())

        return self._actual_port

    async def stop(self) -> None:
        """Stop the viewer server."""
        self._shutdown_event.set()
        if self._dashboard_watch_task:
            self._dashboard_watch_task.cancel()
            try:
                await self._dashboard_watch_task
            except asyncio.CancelledError:
                pass
        for client in self._sse_clients:
            try:
                await client.write_eof()
            except Exception:
                pass
        self._sse_clients.clear()
        for client in self._dashboard_clients:
            try:
                await client.write_eof()
            except Exception:
                pass
        self._dashboard_clients.clear()

        if self._runner:
            await self._runner.cleanup()

    async def broadcast(self, record: dict) -> None:
        """Broadcast a new record to all connected SSE clients."""
        async with self._lock:
            # Cross-midnight: clear in-memory records when the date changes.
            # Previous records are already persisted in the JSONL file and
            # accessible via the date picker.
            today = date.today().isoformat()
            if today != self._current_date:
                self._records.clear()
                self._current_date = today
            self._records.append(record)

        data = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        message = f"data: {data}\n\n"

        disconnected = []
        for client in self._sse_clients:
            try:
                await client.write(message.encode("utf-8"))
            except (ConnectionError, ConnectionResetError, Exception):
                disconnected.append(client)

        for client in disconnected:
            self._sse_clients.remove(client)

        await self._broadcast_dashboard_event({"type": "record", "session_id": self._current_session_id()})

    @property
    def url(self) -> str:
        """Return the viewer URL."""
        return f"http://{self.host}:{self._actual_port}"

    async def _handle_dashboard_index(self, request: web.Request) -> web.Response:
        """Serve the session-first dashboard."""
        try:
            html = read_dashboard_template()
        except OSError:
            return web.Response(status=404, text="dashboard.html not found")
        return web.Response(text=html, content_type="text/html")

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve the viewer HTML with live mode enabled."""
        if not VIEWER_TEMPLATE_PATH.exists():
            return web.Response(status=404, text="viewer.html not found")

        html = _read_viewer_template()
        jsonl_path_js = json.dumps(str(self.trace_path.absolute()))
        html_path = self.trace_path.with_suffix(".html")
        html_path_js = json.dumps(str(html_path.absolute()))
        live_js = (
            "const LIVE_MODE = true;\nconst EMBEDDED_TRACE_DATA = [];\n"
            f"const __TRACE_JSONL_PATH__ = {jsonl_path_js};\n"
            f"const __TRACE_HTML_PATH__ = {html_path_js};\n"
        )
        html = html.replace(
            VIEWER_SCRIPT_ANCHOR,
            f"<script>\n{live_js}</script>\n{VIEWER_SCRIPT_ANCHOR}",
            1,
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_sse(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for live trace updates."""
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await resp.prepare(request)

        async with self._lock:
            for record in self._records:
                data = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                await resp.write(f"data: {data}\n\n".encode("utf-8"))

        self._sse_clients.append(resp)

        try:
            while not self._shutdown_event.is_set():
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
                if self._shutdown_event.is_set():
                    break
                try:
                    await resp.write(b": keepalive\n\n")
                except (ConnectionError, ConnectionResetError, RuntimeError):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            if resp in self._sse_clients:
                self._sse_clients.remove(resp)

        return resp

    async def _handle_dashboard_sse(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for dashboard-level session updates."""
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await resp.prepare(request)
        self._dashboard_clients.append(resp)
        await self._write_dashboard_event(resp, {"type": "ready"})

        try:
            while not self._shutdown_event.is_set():
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
                if self._shutdown_event.is_set():
                    break
                try:
                    await resp.write(b": keepalive\n\n")
                except (ConnectionError, ConnectionResetError, RuntimeError):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            if resp in self._dashboard_clients:
                self._dashboard_clients.remove(resp)

        return resp

    async def _handle_records(self, request: web.Request) -> web.Response:
        """Return all records as JSON array."""
        async with self._lock:
            return web.json_response(self._records)

    async def _handle_dates(self, request: web.Request) -> web.Response:
        """Return available trace dates (descending)."""
        if not self.output_dir or not self.output_dir.is_dir():
            return web.json_response({"dates": [], "has_legacy": False})
        dates_set: set[str] = set()
        has_legacy = False
        for item in sorted(self.output_dir.iterdir(), reverse=True):
            if item.is_dir() and _DATE_RE.match(item.name):
                if any(item.glob("trace_*.jsonl")):
                    dates_set.add(item.name)
            elif item.is_file() and item.name.startswith("trace_") and item.suffix == ".jsonl":
                has_legacy = True
        # Always include today so cross-midnight sessions are visible
        dates_set.add(date.today().isoformat())
        dates = sorted(dates_set, reverse=True)
        return web.json_response({"dates": dates, "has_legacy": has_legacy})

    async def _handle_traces_by_date(self, request: web.Request) -> web.Response:
        """Return combined trace records for a given date."""
        date = request.match_info["date"]
        if not self.output_dir or not self.output_dir.is_dir():
            return web.json_response([])

        if date == "legacy":
            trace_dir = self.output_dir
            pattern = "trace_*.jsonl"
        elif _DATE_RE.match(date):
            trace_dir = self.output_dir / date
            pattern = "trace_*.jsonl"
        else:
            return web.Response(status=400, text="Invalid date format")

        if not trace_dir.is_dir():
            return web.json_response([])

        records = []
        for jsonl in sorted(trace_dir.glob(pattern)):
            try:
                for line in jsonl.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            except (OSError, json.JSONDecodeError):
                continue
        return web.json_response(records)

    async def _handle_agents(self, request: web.Request) -> web.Response:
        """Return trace history agent buckets."""
        if not self.output_dir:
            return web.json_response({"agents": []})
        return web.json_response({"agents": list_trace_agents(self.output_dir, current_trace_path=self.trace_path)})

    async def _handle_sessions(self, request: web.Request) -> web.Response:
        """Return trace history sessions."""
        if not self.output_dir:
            return web.json_response({"sessions": []})
        return web.json_response({"sessions": list_trace_sessions(self.output_dir, current_trace_path=self.trace_path)})

    async def _handle_session_records(self, request: web.Request) -> web.Response:
        """Return one session's summary and records."""
        if not self.output_dir:
            return web.json_response({"error": "No output directory configured"}, status=404)
        session = load_trace_session(
            self.output_dir,
            request.match_info["session_id"],
            current_trace_path=self.trace_path,
        )
        if session is None:
            return web.json_response({"error": "Session not found"}, status=404)
        return web.json_response(session)

    async def _handle_session_html(self, request: web.Request) -> web.Response:
        """Serve a generated static HTML trace viewer for a session."""
        if not self.output_dir:
            return web.Response(status=404, text="No output directory configured")
        session = load_trace_session(
            self.output_dir,
            request.match_info["session_id"],
            current_trace_path=self.trace_path,
        )
        if session is None:
            return web.Response(status=404, text="Session not found")
        html_path = session["session"].get("html_path")
        if not html_path:
            return web.Response(status=404, text="HTML viewer not generated yet")
        path = Path(html_path)
        if not path.is_file():
            return web.Response(status=404, text="HTML viewer not found")
        return web.Response(text=path.read_text(encoding="utf-8"), content_type="text/html")

    async def _watch_dashboard_files(self) -> None:
        """Poll trace files and notify dashboard clients when history changes."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
            if self._shutdown_event.is_set() or not self.output_dir:
                break
            snapshot = dashboard_trace_snapshot(self.output_dir)
            if snapshot != self._dashboard_snapshot:
                self._dashboard_snapshot = snapshot
                await self._broadcast_dashboard_event({"type": "refresh"})

    async def _broadcast_dashboard_event(self, payload: dict) -> None:
        if not self._dashboard_clients:
            return
        disconnected = []
        for client in self._dashboard_clients:
            try:
                await self._write_dashboard_event(client, payload)
            except (ConnectionError, ConnectionResetError, RuntimeError, Exception):
                disconnected.append(client)
        for client in disconnected:
            if client in self._dashboard_clients:
                self._dashboard_clients.remove(client)

    async def _write_dashboard_event(self, client: web.StreamResponse, payload: dict) -> None:
        event_name = payload.get("type", "message")
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        await client.write(f"event: {event_name}\ndata: {data}\n\n".encode("utf-8"))

    def _current_session_id(self) -> str | None:
        if not self.output_dir:
            return None
        try:
            rel_path = self.trace_path.resolve().relative_to(self.output_dir.resolve()).as_posix()
        except ValueError:
            return None
        return session_id_for_rel_path(rel_path)

    async def _handle_delete_traces_by_date(self, request: web.Request) -> web.Response:
        """Delete stored trace files for a selected history date."""
        date = request.match_info["date"]
        if not self.output_dir or not self.output_dir.is_dir():
            return web.json_response({"date": date, "deleted_files": 0, "deleted_traces": 0, "skipped_files": 0})
        if date != "legacy" and not _DATE_RE.match(date):
            return web.json_response({"error": "Invalid date format"}, status=400)
        try:
            result = delete_trace_history(self.output_dir, date, protected_paths=[self.trace_path])
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)
