"""LiveViewerServer - SSE-based real-time trace viewer."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from claude_tap.dashboard import (
    dashboard_trace_snapshot,
    ensure_trace_store,
    list_trace_agents,
    list_trace_sessions,
    load_trace_session,
    read_dashboard_template,
)
from claude_tap.history import delete_trace_history, migrate_legacy_traces
from claude_tap.trace_store import get_trace_store, resolve_db_path
from claude_tap.viewer import VIEWER_SCRIPT_ANCHOR, VIEWER_TEMPLATE_PATH, _generate_html_viewer, _read_viewer_template

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _record_limit_from_request(request: web.Request) -> int | None:
    value = request.query.get("limit")
    if value is None:
        return None
    try:
        limit = int(value)
    except ValueError:
        return None
    return max(0, limit)


def _record_offset_from_request(request: web.Request) -> int:
    value = request.query.get("offset")
    if value is None:
        return 0
    try:
        offset = int(value)
    except ValueError:
        return 0
    return max(0, offset)


class LiveViewerServer:
    """HTTP server for real-time trace viewing via SSE."""

    def __init__(
        self,
        session_id: str | None = None,
        port: int = 0,
        host: str = "127.0.0.1",
        migrate_from: Path | None = None,
        dashboard_mode: bool = False,
    ):
        self.session_id = session_id
        self.port = port
        self.host = host
        self.migrate_from = migrate_from
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
        self._dashboard_snapshot: dict[str, tuple[str, int, str]] = {}

    async def start(self) -> int:
        """Start the viewer server and return the actual port."""
        if self.migrate_from is not None:
            migrate_legacy_traces(self.migrate_from)

        app = web.Application()
        if self.dashboard_mode:
            app.router.add_get("/", self._handle_dashboard_index)
        else:
            app.router.add_get("/", self._handle_index)
        app.router.add_get("/viewer", self._handle_index)
        app.router.add_get("/dashboard", self._handle_dashboard_index)
        app.router.add_get("/dashboard/session/{session_id}", self._handle_dashboard_session_detail)
        app.router.add_get("/dashboard/health", self._handle_dashboard_health)
        app.router.add_get("/dashboard/events", self._handle_dashboard_sse)
        app.router.add_get("/events", self._handle_sse)
        app.router.add_get("/records", self._handle_records)
        app.router.add_get("/api/dates", self._handle_dates)
        app.router.add_get("/api/traces/{date}", self._handle_traces_by_date)
        app.router.add_delete("/api/traces/{date}", self._handle_delete_traces_by_date)
        app.router.add_get("/api/agents", self._handle_agents)
        app.router.add_get("/api/sessions", self._handle_sessions)
        app.router.add_get("/api/sessions/{session_id}/records", self._handle_session_records)
        app.router.add_get("/api/sessions/{session_id}/html", self._handle_session_html_compat)
        app.router.add_get("/api/sessions/{session_id}/export/jsonl", self._handle_export_jsonl)
        app.router.add_get("/api/sessions/{session_id}/export/log", self._handle_export_log)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()

        try:
            self._actual_port = site._server.sockets[0].getsockname()[1]
        except (AttributeError, IndexError, OSError):
            self._actual_port = self.port

        if self.dashboard_mode:
            self._dashboard_snapshot = dashboard_trace_snapshot()
            self._dashboard_watch_task = asyncio.create_task(self._watch_dashboard_store())

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

        await self._broadcast_dashboard_event({"type": "record", "session_id": self.session_id})

    @property
    def url(self) -> str:
        """Return the viewer URL."""
        return f"http://{self.host}:{self._actual_port}"

    async def _handle_dashboard_index(self, request: web.Request) -> web.Response:
        """Serve the session-first dashboard."""
        if session_id := request.query.get("session_id"):
            raise web.HTTPFound(location=f"/dashboard/session/{quote(session_id, safe='')}")
        try:
            html = read_dashboard_template()
        except OSError:
            return web.Response(status=404, text="dashboard.html not found")
        return web.Response(text=html, content_type="text/html")

    async def _handle_dashboard_session_detail(self, request: web.Request) -> web.Response:
        """Serve a dashboard session as the standalone trace viewer page."""
        return await self._session_html_response(request.match_info["session_id"])

    async def _handle_dashboard_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "db_path": str(resolve_db_path())})

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve the viewer HTML with live mode enabled."""
        if not VIEWER_TEMPLATE_PATH.exists():
            return web.Response(status=404, text="viewer.html not found")

        html = _read_viewer_template()
        live_js = (
            "const LIVE_MODE = true;\nconst EMBEDDED_TRACE_DATA = [];\n"
            f"const __TRACE_SESSION_ID__ = {json.dumps(self.session_id or '')};\n"
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
        ensure_trace_store()
        dates, has_legacy = get_trace_store().list_dates()
        return web.json_response({"dates": dates, "has_legacy": has_legacy})

    async def _handle_traces_by_date(self, request: web.Request) -> web.Response:
        """Return combined trace records for a given date."""
        date_key = request.match_info["date"]
        if date_key != "legacy" and not _DATE_RE.match(date_key):
            return web.Response(status=400, text="Invalid date format")

        records = ensure_trace_store().load_records_for_date(date_key)
        return web.json_response(records)

    async def _handle_agents(self, request: web.Request) -> web.Response:
        """Return trace history agent buckets."""
        live_count = await self._current_live_record_count()
        return web.json_response({"agents": list_trace_agents(self.session_id, live_record_count=live_count)})

    async def _handle_sessions(self, request: web.Request) -> web.Response:
        """Return trace history sessions."""
        live_count = await self._current_live_record_count()
        return web.json_response({"sessions": list_trace_sessions(self.session_id, live_record_count=live_count)})

    async def _handle_session_records(self, request: web.Request) -> web.Response:
        """Return one session's summary and records."""
        live_count = await self._current_live_record_count()
        session = load_trace_session(
            request.match_info["session_id"],
            current_session_id=self.session_id,
            record_limit=_record_limit_from_request(request),
            record_offset=_record_offset_from_request(request),
            live_record_count=live_count,
        )
        if session is None:
            return web.json_response({"error": "Session not found"}, status=404)
        return web.json_response(session)

    async def _handle_session_html_compat(self, request: web.Request) -> web.Response:
        return await self._session_html_response(request.match_info["session_id"])

    async def _session_html_response(self, session_id: str) -> web.Response:
        store = ensure_trace_store()
        if store.load_session_row(session_id) is None:
            return web.Response(status=404, text="Session not found")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            trace_path = tmp_path / f"session-{session_id[:8]}.jsonl"
            html_path = tmp_path / f"session-{session_id[:8]}.html"
            trace_path.write_text(store.export_jsonl(session_id), encoding="utf-8")
            export_urls = {
                "jsonl": f"/api/sessions/{quote(session_id)}/export/jsonl",
                "log": f"/api/sessions/{quote(session_id)}/export/log",
            }
            _generate_html_viewer(
                trace_path,
                html_path,
                display_trace_path=export_urls["jsonl"],
                display_html_path=f"/dashboard/session/{quote(session_id)}",
            )
            if not html_path.exists():
                return web.Response(status=500, text="Failed to generate session viewer")
            html = html_path.read_text(encoding="utf-8")
            export_js = f"const __TRACE_SESSION_EXPORTS__ = {json.dumps(export_urls, separators=(',', ':'))};\n"
            html = html.replace(
                VIEWER_SCRIPT_ANCHOR,
                f"<script>\n{export_js}</script>\n{VIEWER_SCRIPT_ANCHOR}",
                1,
            )
        return web.Response(text=html, content_type="text/html")

    async def _current_live_record_count(self) -> int:
        async with self._lock:
            return len(self._records)

    async def _handle_export_jsonl(self, request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        store = ensure_trace_store()
        if store.load_session_row(session_id) is None:
            return web.Response(status=404, text="Session not found")
        body = store.export_jsonl(session_id)
        filename = f"trace_{session_id[:8]}.jsonl"
        return web.Response(
            body=body,
            content_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def _handle_export_log(self, request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        store = ensure_trace_store()
        if store.load_session_row(session_id) is None:
            return web.Response(status=404, text="Session not found")
        body = store.export_log(session_id)
        filename = f"trace_{session_id[:8]}.log"
        return web.Response(
            text=body,
            content_type="text/plain",
            charset="utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def _watch_dashboard_store(self) -> None:
        """Poll SQLite and notify dashboard clients when history changes."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
            if self._shutdown_event.is_set():
                break
            snapshot = dashboard_trace_snapshot()
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

    async def _handle_delete_traces_by_date(self, request: web.Request) -> web.Response:
        """Delete stored trace sessions for a selected history date."""
        date_key = request.match_info["date"]
        if date_key != "legacy" and not _DATE_RE.match(date_key):
            return web.json_response({"error": "Invalid date format"}, status=400)
        protected: set[str] = set()
        force = request.query.get("force", "").lower() in {"1", "true", "yes"}
        if self.session_id:
            protected.add(self.session_id)
        elif not force:
            for row in get_trace_store().list_session_rows():
                if (row["status"] or "") == "active":
                    protected.add(row["id"])
        try:
            result = delete_trace_history(date_key, protected_session_ids=protected)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)
