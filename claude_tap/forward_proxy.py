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
import gzip
import json
import logging
import time
import uuid
import zlib

import aiohttp

from claude_tap.certs import CertificateAuthority
from claude_tap.proxy import HOP_BY_HOP, _build_record, filter_headers
from claude_tap.sse import SSEReassembler
from claude_tap.trace import TraceWriter

log = logging.getLogger("claude-tap")


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
