"""Verify launched clients bypass network proxies using real HTTP sockets."""

from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack

import aiohttp
import pytest
from aiohttp import web

from claude_tap.cli_clients import run_client
from claude_tap.proxy import proxy_handler
from claude_tap.trace import TraceWriter
from claude_tap.trace_store import TraceStore


@pytest.mark.skipif(os.name == "nt", reason="The subprocess probe uses a POSIX executable shebang")
@pytest.mark.parametrize("client", ["claude", "codex", "pi"])
@pytest.mark.asyncio
async def test_reverse_client_reaches_tap_despite_inherited_lowercase_proxy(monkeypatch, tmp_path, client):
    rejected_requests = []
    upstream_requests = []

    async def reject(request):
        rejected_requests.append(request.raw_path)
        return web.Response(status=502, text="Loopback request incorrectly reached the network proxy")

    async def upstream(request):
        upstream_requests.append(await request.json())
        return web.json_response({"model": "test-model", "content": [{"type": "text", "text": "direct"}]})

    async with AsyncExitStack() as stack:

        async def serve(app):
            runner = web.AppRunner(app)
            await runner.setup()
            stack.push_async_callback(runner.cleanup)
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            return site._server.sockets[0].getsockname()[1]

        reject_app = web.Application()
        reject_app.router.add_route("*", "/{path:.*}", reject)
        rejected_port = await serve(reject_app)
        upstream_app = web.Application()
        upstream_app.router.add_post("/v1/messages", upstream)
        upstream_port = await serve(upstream_app)

        store = TraceStore(tmp_path / "trace.sqlite3")
        session_id = store.create_session(client=client, proxy_mode="reverse")
        writer = TraceWriter(session_id, store=store)
        session = await stack.enter_async_context(aiohttp.ClientSession(trust_env=False))
        tap = web.Application()
        tap["trace_ctx"] = {
            "target_url": f"http://127.0.0.1:{upstream_port}",
            "writer": writer,
            "session": session,
        }
        tap.router.add_route("*", "/{path:.*}", proxy_handler)
        tap_port = await serve(tap)

        probe = tmp_path / "client-probe"
        result_path = tmp_path / "client-result.json"
        probe.write_text(
            f"#!{sys.executable}\n"
            "import json, os, urllib.request, urllib.error\n"
            "from pathlib import Path\n"
            "port = os.environ['TEST_TAP_PORT']\n"
            "body = json.dumps({'model': 'test-model', 'messages': [{'role': 'user', 'content': 'hello'}]}).encode()\n"
            "request = urllib.request.Request('http://127.0.0.1:' + port + '/v1/messages', data=body, "
            "headers={'Content-Type': 'application/json'})\n"
            "try:\n"
            "    with urllib.request.urlopen(request, timeout=10) as response:\n"
            "        status = response.status\n"
            "        response.read()\n"
            "except urllib.error.HTTPError as error:\n"
            "    status = error.code\n"
            "Path(os.environ['TEST_RESULT']).write_text(json.dumps({'status': status, "
            "'NO_PROXY': os.environ.get('NO_PROXY'), 'no_proxy': os.environ.get('no_proxy'), "
            "'http_proxy': os.environ.get('http_proxy')}))\n"
        )
        probe.chmod(0o755)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{rejected_port}")
        monkeypatch.setenv("NO_PROXY", "upper.internal")
        monkeypatch.setenv("no_proxy", "172.31.*,127.*,localhost,lower.internal")
        monkeypatch.setenv("TEST_TAP_PORT", str(tap_port))
        monkeypatch.setenv("TEST_RESULT", str(result_path))
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        monkeypatch.setattr("claude_tap.cli_clients.shutil.which", lambda _cmd: str(probe))
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        assert await run_client(tap_port, [], client=client, proxy_mode="reverse") == 0
        result = json.loads(result_path.read_text())
        assert result["status"] == 200, result
        assert rejected_requests == []
        assert len(upstream_requests) == 1
        assert result["NO_PROXY"] == result["no_proxy"]
        assert set(result["no_proxy"].split(",")) == {
            "upper.internal",
            "172.31.*",
            "127.*",
            "localhost",
            "lower.internal",
            "127.0.0.1",
            "::1",
        }
        assert result["http_proxy"] == f"http://127.0.0.1:{rejected_port}"
        writer.close()
        records = store.load_records(session_id)
        assert len(records) == 1
        assert records[0]["request"]["body"] == upstream_requests[0]
        assert records[0]["response"]["status"] == 200
