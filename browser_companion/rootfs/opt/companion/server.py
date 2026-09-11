"""HTTP API + Ingress UI for Browser Companion."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiohttp import ClientSession, WSMsgType, web

from cdp import DEFAULT_SUCCESS_MESSAGE, CdpController
from handlers import register_schemes
from intercept import (
    CaptureCandidate,
    WaitSpecError,
    extract_result,
    matches_wait,
    parse_navigate_after,
    parse_wait,
    protocol_schemes,
)
from session import SessionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_LOGGER = logging.getLogger("companion")

STATIC_DIR = Path(__file__).resolve().parent / "static"
API_HOST = os.environ.get("COMPANION_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("COMPANION_API_PORT", "8100"))
CHROME_UPSTREAM = os.environ.get("COMPANION_CHROME_UPSTREAM", "http://127.0.0.1:5800")
MAX_TIMEOUT = 3600
DEFAULT_TIMEOUT = 600

store = SessionStore()
cdp = CdpController()
chrome_http: ClientSession | None = None


def _peer_is_local(request: web.Request) -> bool:
    peer = request.remote or ""
    return peer in {"127.0.0.1", "::1"}


async def _capture_candidate(candidate: CaptureCandidate) -> bool:
    session = store.current
    if session is None or session.status != "pending":
        return False
    if not matches_wait(candidate, session.wait_rules):
        return False
    result = extract_result(candidate)
    if store.capture(result):
        _LOGGER.info(
            "Captured %s for session %s", candidate.event, session.id
        )
        return True
    return False


async def _start_browser(session_id: str, start_url: str) -> None:
    current = store.get(session_id)
    if current is None:
        return
    rules = current.wait_rules

    def match(candidate: CaptureCandidate) -> bool:
        session = store.get(session_id)
        if session is None:
            return False
        return matches_wait(candidate, rules)

    async def capture(candidate: CaptureCandidate) -> bool:
        if store.get(session_id) is None:
            return False
        return await _capture_candidate(candidate)

    try:
        register_schemes(protocol_schemes(rules))
        await cdp.start_session(
            start_url,
            match,
            capture,
            wait_rules=rules,
            navigate_after=current.navigate_after,
            success_message=current.success_message,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Failed to start browser session")
        session = store.get(session_id)
        if session is not None and session.status == "pending":
            store.fail("browser_unavailable")


async def handle_index(_request: web.Request) -> web.Response:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


async def handle_health(_request: web.Request) -> web.Response:
    session = store.current
    return web.json_response(
        {
            "ok": True,
            "session": None if session is None else session.to_public(),
        }
    )


async def handle_create_session(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid_json"}, status=400)
    start_url = str(body.get("start_url") or "").strip()
    if not start_url:
        return web.json_response({"error": "start_url_required"}, status=400)
    try:
        wait_rules = parse_wait(body)
        navigate_after = parse_navigate_after(body)
    except WaitSpecError as err:
        return web.json_response({"error": str(err)}, status=400)
    try:
        timeout = int(body.get("timeout_seconds") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(30, min(timeout, MAX_TIMEOUT))
    client_id = str(body.get("client_id") or "unknown")[:64]
    success_message = str(body.get("success_message") or DEFAULT_SUCCESS_MESSAGE).strip()
    if not success_message:
        success_message = DEFAULT_SUCCESS_MESSAGE
    success_message = success_message[:500]
    session = store.replace(
        client_id=client_id,
        start_url=start_url,
        wait_rules=wait_rules,
        timeout_seconds=timeout,
        navigate_after=navigate_after,
        success_message=success_message,
    )
    asyncio.create_task(_start_browser(session.id, start_url), name="companion-start")
    return web.json_response(session.to_public(), status=201)


async def handle_get_session(request: web.Request) -> web.Response:
    session = store.get(request.match_info["session_id"])
    if session is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(session.to_public())


async def handle_delete_session(request: web.Request) -> web.Response:
    session = store.get(request.match_info["session_id"])
    if session is None:
        return web.json_response({"error": "not_found"}, status=404)
    store.clear()
    await cdp.stop()
    return web.json_response({"ok": True})


async def handle_internal_capture(request: web.Request) -> web.Response:
    if not _peer_is_local(request):
        return web.json_response({"error": "forbidden"}, status=403)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid_json"}, status=400)
    url = str(body.get("url") or "").strip()
    if not url:
        return web.json_response({"error": "url_required"}, status=400)
    captured = await _capture_candidate(CaptureCandidate("protocol_handler", url))
    if captured:
        cdp.schedule_close()
    return web.json_response({"ok": True})


async def handle_chrome_proxy(request: web.Request) -> web.StreamResponse:
    """Proxy Chromium's web UI (port 5800) under /chrome/ for Ingress."""
    global chrome_http
    if chrome_http is None or chrome_http.closed:
        chrome_http = ClientSession()
    suffix = request.match_info.get("path", "")
    query = request.query_string
    url = f"{CHROME_UPSTREAM}/{suffix}"
    if query:
        url = f"{url}?{query}"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await _proxy_ws(request, url)
    async with chrome_http.request(
        request.method,
        url,
        headers=headers,
        data=await request.read() if request.method not in ("GET", "HEAD") else None,
        allow_redirects=False,
    ) as upstream:
        response = web.StreamResponse(status=upstream.status, reason=upstream.reason)
        for key, value in upstream.headers.items():
            if key.lower() in {"transfer-encoding", "content-encoding", "content-length"}:
                continue
            response.headers[key] = value
        await response.prepare(request)
        async for chunk in upstream.content.iter_chunked(65536):
            await response.write(chunk)
        await response.write_eof()
        return response


async def _proxy_ws(request: web.Request, url: str) -> web.WebSocketResponse:
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)
    ws_url = url.replace("http://", "ws://").replace("https://", "wss://")
    async with ClientSession() as session:
        async with session.ws_connect(ws_url, headers={"Host": request.headers.get("Host", "")}) as ws_client:

            async def to_upstream() -> None:
                async for msg in ws_server:
                    if msg.type == WSMsgType.TEXT:
                        await ws_client.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_client.send_bytes(msg.data)
                    elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break

            async def to_client() -> None:
                async for msg in ws_client:
                    if msg.type == WSMsgType.TEXT:
                        await ws_server.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_server.send_bytes(msg.data)
                    elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break

            await asyncio.wait(
                [asyncio.create_task(to_upstream()), asyncio.create_task(to_client())],
                return_when=asyncio.FIRST_COMPLETED,
            )
    await ws_server.close()
    return ws_server


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/v1/health", handle_health)
    app.router.add_post("/v1/sessions", handle_create_session)
    app.router.add_get("/v1/sessions/{session_id}", handle_get_session)
    app.router.add_delete("/v1/sessions/{session_id}", handle_delete_session)
    app.router.add_post("/internal/capture", handle_internal_capture)
    app.router.add_route("*", "/chrome", handle_chrome_proxy)
    app.router.add_route("*", "/chrome/", handle_chrome_proxy)
    app.router.add_route("*", "/chrome/{path:.*}", handle_chrome_proxy)
    return app


def main() -> None:
    _LOGGER.info("API listening on %s:%s", API_HOST, API_PORT)
    web.run_app(build_app(), host=API_HOST, port=API_PORT, print=None)


if __name__ == "__main__":
    main()
