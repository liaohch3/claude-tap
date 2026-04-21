"""Forward proxy server with CONNECT/TLS termination.

Implements an HTTP forward proxy that handles CONNECT tunneling with
man-in-the-middle TLS termination. This allows claude-tap to intercept
HTTPS traffic while Claude Code uses the real api.anthropic.com endpoint
(preserving OAuth authentication).

Flow:
  1. Client sends CONNECT api.anthropic.com:443
  2. Proxy responds 200 Connection Established
  3. Client starts TLS handshake; proxy presents a cert signed by our CA
  4. Client sends plaintext HTTP request inside the TLS tunnel
  5. Proxy reads the request, records the trace, forwards to real upstream via HTTPS
  6. Proxy returns the upstream response through the tunnel
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import logging
import time
import uuid
import zlib

import aiohttp
from aiohttp import WSMessage, WSMsgType
from aiohttp._websocket.reader import WebSocketDataQueue, WebSocketReader
from aiohttp.http_websocket import WS_KEY, WebSocketWriter

from claude_tap.certs import CertificateAuthority
from claude_tap.proxy import (
    HOP_BY_HOP,
    _build_record,
    _get_ws_proxy_settings,
    filter_headers,
    reconstruct_ws_request_body,
    reconstruct_ws_response_body,
)
from claude_tap.sse import SSEReassembler
from claude_tap.trace import TraceWriter

log = logging.getLogger("claude-tap")


class _RawWSProtocol:
    """Minimal protocol shim for aiohttp's raw WebSocket helpers."""

    def __init__(self) -> None:
        self._reading_paused = False
        self._paused = False

    def pause_reading(self) -> None:
        self._reading_paused = True

    def resume_reading(self) -> None:
        self._reading_paused = False

    async def _drain_helper(self) -> None:
        return


def _is_websocket_upgrade(headers: dict[str, str]) -> bool:
    upgrade = headers.get("Upgrade", headers.get("upgrade", "")).lower()
    if upgrade != "websocket":
        return False
    connection = headers.get("Connection", headers.get("connection", "")).lower()
    return "upgrade" in connection


def _build_ws_accept(sec_key: str) -> str:
    digest = hashlib.sha1(sec_key.encode("utf-8") + WS_KEY).digest()
    return base64.b64encode(digest).decode("ascii")


class ForwardProxyServer:
    """Async TCP server that acts as an HTTP forward proxy with CONNECT support."""

    def __init__(
        self,
        host: str,
        port: int,
        ca: CertificateAuthority,
        writer: TraceWriter,
        session: aiohttp.ClientSession,
    ) -> None:
        self.host = host
        self.port = port
        self._ca = ca
        self._writer = writer
        self._session = session
        self._server: asyncio.Server | None = None
        self._turn_counter = 0
        self.actual_port: int = port

    async def start(self) -> int:
        """Start the forward proxy server. Returns the actual port."""
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        sock = self._server.sockets[0]
        self.actual_port = sock.getsockname()[1]
        return self.actual_port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle an incoming client connection."""
        try:
            # Read the initial HTTP request line
            request_line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not request_line:
                writer.close()
                return

            request_str = request_line.decode("utf-8", errors="replace").strip()
            parts = request_str.split(" ")
            if len(parts) < 3:
                writer.close()
                return

            method = parts[0].upper()

            if method == "CONNECT":
                await self._handle_connect(parts[1], reader, writer)
            else:
                # Non-CONNECT request (plain HTTP proxy) — not expected for HTTPS
                # but handle gracefully
                await self._handle_plain_proxy(method, parts[1], parts[2], reader, writer)
        except (ConnectionError, asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("Error handling forward proxy connection")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_connect(
        self,
        authority: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle CONNECT method: TLS termination + request interception."""
        # Parse host:port
        if ":" in authority:
            hostname, port_str = authority.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 443
        else:
            hostname = authority
            port = 443

        # Read and discard remaining headers until blank line
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if line in (b"\r\n", b"\n", b""):
                break

        # Send 200 Connection Established
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        # TLS termination via a local loopback bounce:
        # 1. Start a temporary TLS server on localhost:0
        # 2. Redirect the client to connect there (via raw socket relay)
        # 3. Accept the TLS connection on the temp server
        # This avoids loop.start_tls() which is unreliable on macOS Python 3.11.
        ssl_ctx = self._ca.make_ssl_context(hostname)

        tls_reader_holder: list[asyncio.StreamReader] = []
        tls_writer_holder: list[asyncio.StreamWriter] = []
        connected = asyncio.Event()

        async def _accept_tls(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            tls_reader_holder.append(r)
            tls_writer_holder.append(w)
            connected.set()

        tls_server = await asyncio.start_server(_accept_tls, "127.0.0.1", 0, ssl=ssl_ctx)
        tls_port = tls_server.sockets[0].getsockname()[1]

        # Relay bytes between the original client transport and the TLS server
        raw_sock = writer.transport.get_extra_info("socket")
        if raw_sock is None:
            tls_server.close()
            log.warning(f"Cannot get raw socket for {hostname}")
            return

        try:
            relay_r, relay_w = await asyncio.open_connection("127.0.0.1", tls_port)
        except (ConnectionError, OSError) as e:
            tls_server.close()
            log.warning(f"Cannot connect to TLS relay for {hostname}: {e}")
            return

        async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        # Start relaying between original client and TLS relay in background
        relay_task = asyncio.create_task(_pipe(relay_r, writer))

        # Also relay from original client to TLS relay
        client_to_relay_task = asyncio.create_task(_pipe(reader, relay_w))

        # Wait for TLS server to accept
        try:
            await asyncio.wait_for(connected.wait(), timeout=15)
        except asyncio.TimeoutError:
            log.warning(f"TLS handshake timed out for {hostname}")
            relay_task.cancel()
            client_to_relay_task.cancel()
            tls_server.close()
            return

        tls_server.close()
        tls_reader = tls_reader_holder[0]
        tls_writer = tls_writer_holder[0]

        # Now read HTTP requests from the TLS tunnel
        try:
            await self._handle_tunneled_requests(hostname, port, tls_reader, tls_writer)
        finally:
            relay_task.cancel()
            client_to_relay_task.cancel()
            try:
                tls_writer.close()
                await tls_writer.wait_closed()
            except Exception:
                pass

    async def _handle_tunneled_requests(
        self,
        hostname: str,
        port: int,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Read HTTP requests from inside the TLS tunnel and proxy them."""
        while True:
            # Read request line
            try:
                request_line = await asyncio.wait_for(reader.readline(), timeout=600)
            except (asyncio.TimeoutError, ConnectionError):
                break
            if not request_line:
                break

            request_str = request_line.decode("utf-8", errors="replace").strip()
            if not request_str:
                break

            parts = request_str.split(" ", 2)
            if len(parts) < 3:
                break

            method, path, _http_version = parts

            # Read headers
            headers: dict[str, str] = {}
            while True:
                header_line = await asyncio.wait_for(reader.readline(), timeout=30)
                if header_line in (b"\r\n", b"\n", b""):
                    break
                decoded = header_line.decode("utf-8", errors="replace").strip()
                if ":" in decoded:
                    key, value = decoded.split(":", 1)
                    headers[key.strip()] = value.strip()

            # Read body if Content-Length is present
            body = b""
            content_length = headers.get("Content-Length") or headers.get("content-length")
            if content_length:
                try:
                    length = int(content_length)
                    body = await asyncio.wait_for(reader.readexactly(length), timeout=60)
                except (ValueError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                    pass

            if _is_websocket_upgrade(headers):
                await self._forward_websocket(
                    hostname=hostname,
                    port=port,
                    path=path,
                    headers=headers,
                    reader=reader,
                    writer=writer,
                )
                break

            # Forward the request to the real upstream
            upstream_url = f"https://{hostname}:{port}{path}"
            await self._forward_and_record(method, path, headers, body, upstream_url, writer)

    async def _forward_and_record(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        upstream_url: str,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Forward request to upstream, record trace, send response back."""
        self._turn_counter += 1
        turn = self._turn_counter
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        t0 = time.monotonic()
        log_prefix = f"[Turn {turn}]"

        # Parse request body for logging
        try:
            req_body = json.loads(body) if body else None
        except (json.JSONDecodeError, ValueError):
            req_body = body.decode("utf-8", errors="replace") if body else None

        is_streaming = False
        if isinstance(req_body, dict):
            is_streaming = req_body.get("stream", False)

        model = req_body.get("model", "") if isinstance(req_body, dict) else ""
        log.info(f"{log_prefix} -> {method} {path} (model={model}, stream={is_streaming})")

        # Prepare forwarding headers
        fwd_headers = filter_headers(headers)
        fwd_headers.pop("Host", None)
        fwd_headers.pop("host", None)
        # Request identity encoding from upstream to avoid client-side zstd decode issues
        # and to simplify SSE/text reconstruction.
        fwd_headers["Accept-Encoding"] = "identity"

        try:
            upstream_resp = await self._session.request(
                method=method,
                url=upstream_url,
                headers=fwd_headers,
                data=body,
                timeout=aiohttp.ClientTimeout(total=600, sock_read=300),
            )
        except Exception as exc:
            log.error(f"{log_prefix} upstream error: {exc}")
            error_body = str(exc).encode()
            response_line = b"HTTP/1.1 502 Bad Gateway\r\n"
            resp_headers = f"Content-Length: {len(error_body)}\r\nContent-Type: text/plain\r\n\r\n"
            client_writer.write(response_line + resp_headers.encode() + error_body)
            await client_writer.drain()
            return

        if is_streaming and upstream_resp.status == 200:
            await self._handle_streaming(
                upstream_resp,
                client_writer,
                req_id,
                turn,
                t0,
                method,
                path,
                headers,
                req_body,
                log_prefix,
            )
        else:
            await self._handle_non_streaming(
                upstream_resp,
                client_writer,
                req_id,
                turn,
                t0,
                method,
                path,
                headers,
                req_body,
                log_prefix,
            )

    async def _handle_streaming(
        self,
        upstream_resp: aiohttp.ClientResponse,
        client_writer: asyncio.StreamWriter,
        req_id: str,
        turn: int,
        t0: float,
        method: str,
        path: str,
        req_headers: dict[str, str],
        req_body: dict | None,
        log_prefix: str,
    ) -> None:
        """Handle a streaming response: forward chunks while recording SSE."""
        # Send response status line
        status_line = f"HTTP/1.1 {upstream_resp.status} {upstream_resp.reason}\r\n"
        client_writer.write(status_line.encode())

        # Send response headers (filter hop-by-hop, use chunked transfer)
        for key, value in upstream_resp.headers.items():
            if key.lower() not in HOP_BY_HOP:
                client_writer.write(f"{key}: {value}\r\n".encode())
        client_writer.write(b"Transfer-Encoding: chunked\r\n")
        client_writer.write(b"\r\n")
        await client_writer.drain()

        reassembler = SSEReassembler()

        try:
            async for chunk in upstream_resp.content.iter_any():
                # Send as HTTP chunked encoding
                chunk_header = f"{len(chunk):x}\r\n".encode()
                client_writer.write(chunk_header + chunk + b"\r\n")
                await client_writer.drain()
                reassembler.feed_bytes(chunk)
        except (ConnectionError, asyncio.CancelledError):
            pass

        # Send final chunk
        try:
            client_writer.write(b"0\r\n\r\n")
            await client_writer.drain()
        except (ConnectionError, Exception):
            pass

        duration_ms = int((time.monotonic() - t0) * 1000)
        reconstructed = reassembler.reconstruct()

        usage = reconstructed.get("usage", {}) if reconstructed else {}
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        log.info(
            f"{log_prefix} <- 200 stream done ({duration_ms}ms, in={in_tok} out={out_tok}"
            f" cache_read={cache_read} cache_create={cache_create})"
        )

        record = _build_record(
            req_id,
            turn,
            duration_ms,
            method,
            path,
            req_headers,
            req_body,
            upstream_resp.status,
            dict(upstream_resp.headers),
            reconstructed,
            sse_events=reassembler.events,
        )
        await self._writer.write(record)

    async def _handle_non_streaming(
        self,
        upstream_resp: aiohttp.ClientResponse,
        client_writer: asyncio.StreamWriter,
        req_id: str,
        turn: int,
        t0: float,
        method: str,
        path: str,
        req_headers: dict[str, str],
        req_body: dict | None,
        log_prefix: str,
    ) -> None:
        """Handle a non-streaming response."""
        resp_bytes = await upstream_resp.read()
        duration_ms = int((time.monotonic() - t0) * 1000)

        # Decompress for JSON parsing
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

        log.info(f"{log_prefix} <- {upstream_resp.status} ({duration_ms}ms, {len(resp_bytes)} bytes)")

        record = _build_record(
            req_id,
            turn,
            duration_ms,
            method,
            path,
            req_headers,
            req_body,
            upstream_resp.status,
            dict(upstream_resp.headers),
            resp_body,
        )
        await self._writer.write(record)

        # Send response to client
        status_line = f"HTTP/1.1 {upstream_resp.status} {upstream_resp.reason}\r\n"
        client_writer.write(status_line.encode())
        skip_headers = HOP_BY_HOP | {"content-length"}  # We set Content-Length ourselves
        for key, value in upstream_resp.headers.items():
            if key.lower() not in skip_headers:
                client_writer.write(f"{key}: {value}\r\n".encode())
        client_writer.write(f"Content-Length: {len(resp_bytes)}\r\n".encode())
        client_writer.write(b"\r\n")
        client_writer.write(resp_bytes)
        await client_writer.drain()

    async def _forward_websocket(
        self,
        hostname: str,
        port: int,
        path: str,
        headers: dict[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Relay a WebSocket upgrade received inside the CONNECT tunnel."""
        self._turn_counter += 1
        turn = self._turn_counter
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        t0 = time.monotonic()
        log_prefix = f"[Turn {turn}]"
        upstream_base_url = f"https://{hostname}:{port}"
        upstream_ws_url = f"wss://{hostname}:{port}{path}"

        fwd_headers = filter_headers(headers)
        fwd_headers.pop("Host", None)
        fwd_headers.pop("host", None)
        for h in list(fwd_headers.keys()):
            if h.lower() in HOP_BY_HOP or h.lower().startswith("sec-websocket-"):
                del fwd_headers[h]

        protocols: tuple[str, ...] = ()
        ws_protocol = headers.get("Sec-WebSocket-Protocol") or headers.get("sec-websocket-protocol")
        if ws_protocol:
            protocols = tuple(p.strip() for p in ws_protocol.split(",") if p.strip())

        log.info(f"{log_prefix} -> WS UPGRADE {path} (upstream={upstream_ws_url})")

        ws_connect_kwargs: dict[str, object] = {}
        proxy_settings = _get_ws_proxy_settings(upstream_ws_url) if self._session.trust_env else None
        if proxy_settings:
            proxy_url, proxy_auth = proxy_settings
            ws_connect_kwargs["proxy"] = proxy_url
            if proxy_auth is not None:
                ws_connect_kwargs["proxy_auth"] = proxy_auth
            log.info(f"{log_prefix} -> WS upstream via proxy {proxy_url}")

        try:
            upstream_ws = await self._session.ws_connect(
                upstream_ws_url,
                headers=fwd_headers,
                protocols=protocols,
                **ws_connect_kwargs,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            log.error(f"{log_prefix} upstream WS connect failed: {exc}")
            error_body = str(exc).encode("utf-8", errors="replace")
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\n"
                + f"Content-Length: {len(error_body)}\r\n".encode()
                + b"Content-Type: text/plain\r\n\r\n"
                + error_body
            )
            await writer.drain()
            record = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "request_id": req_id,
                "turn": turn,
                "duration_ms": duration_ms,
                "transport": "websocket",
                "request": {
                    "method": "WEBSOCKET",
                    "path": path,
                    "headers": filter_headers(headers, redact_keys=True),
                    "body": None,
                },
                "response": {"status": 502, "headers": {}, "body": None, "error": str(exc)},
                "upstream_base_url": upstream_base_url,
            }
            await self._writer.write(record)
            return

        sec_key = headers.get("Sec-WebSocket-Key") or headers.get("sec-websocket-key")
        if not sec_key:
            await upstream_ws.close()
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return

        response_lines = [
            "HTTP/1.1 101 Switching Protocols",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Accept: {_build_ws_accept(sec_key)}",
        ]
        if upstream_ws.protocol:
            response_lines.append(f"Sec-WebSocket-Protocol: {upstream_ws.protocol}")
        writer.write(("\r\n".join(response_lines) + "\r\n\r\n").encode("utf-8"))
        await writer.drain()

        raw_protocol = _RawWSProtocol()
        queue = WebSocketDataQueue(raw_protocol, 2**16, loop=asyncio.get_running_loop())
        ws_reader = WebSocketReader(queue, max_msg_size=0)
        ws_writer = WebSocketWriter(raw_protocol, writer.transport, use_mask=False)

        client_messages: list[str] = []
        server_messages: list[str] = []

        async def _pump_client_bytes() -> None:
            try:
                while True:
                    chunk = await reader.read(65536)
                    if not chunk:
                        break
                    ws_reader.feed_data(chunk)
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                ws_reader.feed_eof()

        async def _relay_client_to_upstream() -> None:
            while True:
                try:
                    msg: WSMessage = await queue.read()
                except (asyncio.CancelledError, Exception):
                    break

                if msg.type == WSMsgType.TEXT:
                    client_messages.append(msg.data)
                    await upstream_ws.send_str(msg.data)
                elif msg.type == WSMsgType.BINARY:
                    await upstream_ws.send_bytes(msg.data)
                elif msg.type == WSMsgType.PING:
                    await upstream_ws.ping(msg.data)
                elif msg.type == WSMsgType.PONG:
                    await upstream_ws.pong(msg.data)
                elif msg.type == WSMsgType.CLOSE:
                    await upstream_ws.close(code=msg.data or 1000, message=msg.extra.encode("utf-8"))
                    break
                else:
                    break

        async def _relay_upstream_to_client() -> None:
            async for msg in upstream_ws:
                if msg.type == WSMsgType.TEXT:
                    server_messages.append(msg.data)
                    await ws_writer.send_frame(msg.data.encode("utf-8"), WSMsgType.TEXT)
                elif msg.type == WSMsgType.BINARY:
                    await ws_writer.send_frame(msg.data, WSMsgType.BINARY)
                elif msg.type == WSMsgType.PING:
                    payload = msg.data if isinstance(msg.data, (bytes, bytearray)) else b""
                    await ws_writer.send_frame(bytes(payload), WSMsgType.PING)
                elif msg.type == WSMsgType.PONG:
                    payload = msg.data if isinstance(msg.data, (bytes, bytearray)) else b""
                    await ws_writer.send_frame(bytes(payload), WSMsgType.PONG)
                elif msg.type == WSMsgType.CLOSE:
                    await ws_writer.close(code=msg.data or 1000, message=msg.extra)
                    break
                elif msg.type in (WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
                    break

        tasks = [
            asyncio.create_task(_pump_client_bytes()),
            asyncio.create_task(_relay_client_to_upstream()),
            asyncio.create_task(_relay_upstream_to_client()),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        for task in done:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        if not upstream_ws.closed:
            await upstream_ws.close()
        try:
            await ws_writer.close()
        except Exception:
            pass

        duration_ms = int((time.monotonic() - t0) * 1000)
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "request_id": req_id,
            "turn": turn,
            "duration_ms": duration_ms,
            "transport": "websocket",
            "request": {
                "method": "WEBSOCKET",
                "path": path,
                "headers": filter_headers(headers, redact_keys=True),
                "body": reconstruct_ws_request_body(client_messages),
            },
            "response": {
                "status": 101,
                "headers": {},
                "body": None,
                "ws_events": [json.loads(msg) if msg.startswith("{") else {"raw": msg} for msg in server_messages],
            },
            "upstream_base_url": upstream_base_url,
        }
        record["response"]["body"] = reconstruct_ws_response_body(record["response"]["ws_events"])
        await self._writer.write(record)
        log.info(
            f"{log_prefix} <- WS closed ({duration_ms}ms, "
            f"{len(client_messages)} client→upstream, {len(server_messages)} upstream→client)"
        )

    async def _handle_plain_proxy(
        self,
        method: str,
        url: str,
        http_version: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle plain HTTP proxy requests (non-CONNECT).

        For absolute URL requests like GET http://example.com/path HTTP/1.1
        """
        # Read headers
        headers: dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if line in (b"\r\n", b"\n", b""):
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if ":" in decoded:
                key, value = decoded.split(":", 1)
                headers[key.strip()] = value.strip()

        # Read body
        body = b""
        content_length = headers.get("Content-Length") or headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
                body = await asyncio.wait_for(reader.readexactly(length), timeout=60)
            except (ValueError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                pass

        # Extract path from absolute URL
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"

        await self._forward_and_record(method, path, headers, body, url, writer)
