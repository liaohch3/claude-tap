"""LiveViewerServer - SSE-based real-time trace viewer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import web


class LiveViewerServer:
    """HTTP server for real-time trace viewing via SSE."""

    def __init__(self, trace_path: Path, port: int = 0):
        self.trace_path = trace_path
        self.port = port
        self._sse_clients: list[web.StreamResponse] = []
        self._records: list[dict] = []
        self._lock = asyncio.Lock()
        self._runner: web.AppRunner | None = None
        self._actual_port: int = 0
        self._shutdown_event = asyncio.Event()

    async def start(self) -> int:
        """Start the viewer server and return the actual port."""
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/events", self._handle_sse)
        app.router.add_get("/records", self._handle_records)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await site.start()

        try:
            self._actual_port = site._server.sockets[0].getsockname()[1]
        except (AttributeError, IndexError, OSError):
            self._actual_port = self.port

        return self._actual_port

    async def stop(self) -> None:
        """Stop the viewer server."""
        self._shutdown_event.set()
        for client in self._sse_clients:
            try:
                await client.write_eof()
            except Exception:
                pass
        self._sse_clients.clear()

        if self._runner:
            await self._runner.cleanup()

    async def broadcast(self, record: dict) -> None:
        """Broadcast a new record to all connected SSE clients."""
        async with self._lock:
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

    @property
    def url(self) -> str:
        """Return the viewer URL."""
        return f"http://127.0.0.1:{self._actual_port}"

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve the viewer HTML with live mode enabled."""
        template = Path(__file__).parent / "viewer.html"
        if not template.exists():
            return web.Response(status=404, text="viewer.html not found")

        html = template.read_text(encoding="utf-8")
        jsonl_path_js = json.dumps(str(self.trace_path.absolute()))
        html_path = self.trace_path.with_suffix(".html")
        html_path_js = json.dumps(str(html_path.absolute()))
        live_js = (
            "const LIVE_MODE = true;\nconst EMBEDDED_TRACE_DATA = [];\n"
            f"const __TRACE_JSONL_PATH__ = {jsonl_path_js};\n"
            f"const __TRACE_HTML_PATH__ = {html_path_js};\n"
        )
        html = html.replace(
            "<script>\nconst $ = s =>",
            f"<script>\n{live_js}</script>\n<script>\nconst $ = s =>",
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

    async def _handle_records(self, request: web.Request) -> web.Response:
        """Return all records as JSON array."""
        async with self._lock:
            return web.json_response(self._records)
