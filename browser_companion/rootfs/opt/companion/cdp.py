"""Chrome DevTools Protocol client: navigate and intercept redirects."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from intercept import CaptureCandidate, location_from_headers

_LOGGER = logging.getLogger("companion.cdp")

CDP_VERSION = "http://127.0.0.1:9222/json/version"
MatchFn = Callable[[CaptureCandidate], bool]
CaptureFn = Callable[[CaptureCandidate], Awaitable[None]]


class CdpController:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._cmd_id = 0
        self._match: MatchFn | None = None
        self._capture: CaptureFn | None = None

    async def start_session(
        self,
        start_url: str,
        match: MatchFn,
        capture: CaptureFn,
    ) -> None:
        await self.stop()
        self._stop = asyncio.Event()
        self._match = match
        self._capture = capture
        self._task = asyncio.create_task(
            self._run(start_url), name="companion-cdp"
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self, start_url: str) -> None:
        try:
            async with aiohttp.ClientSession() as http:
                ws_url = await self._wait_for_browser(http)
                async with http.ws_connect(ws_url, heartbeat=20) as ws:
                    await self._session_loop(ws, start_url)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("CDP session failed")

    async def _wait_for_browser(self, http: aiohttp.ClientSession) -> str:
        last_error = "cdp_unavailable"
        for _ in range(60):
            if self._stop.is_set():
                raise RuntimeError("stopped")
            try:
                async with http.get(CDP_VERSION, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        payload = await resp.json()
                        ws_url = payload.get("webSocketDebuggerUrl")
                        if ws_url:
                            return str(ws_url)
            except (TimeoutError, aiohttp.ClientError) as err:
                last_error = str(err)
            await asyncio.sleep(1)
        raise RuntimeError(last_error)

    async def _session_loop(self, ws: aiohttp.ClientWebSocketResponse, start_url: str) -> None:
        self._cmd_id = 0
        pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

        async def send_raw(
            method: str,
            params: dict[str, Any] | None = None,
            session_id: str | None = None,
            wait: bool = True,
        ) -> dict[str, Any]:
            self._cmd_id += 1
            cmd_id = self._cmd_id
            message: dict[str, Any] = {"id": cmd_id, "method": method}
            if params:
                message["params"] = params
            if session_id:
                message["sessionId"] = session_id
            fut: asyncio.Future[dict[str, Any]] | None = None
            if wait:
                fut = asyncio.get_running_loop().create_future()
                pending[cmd_id] = fut
            await ws.send_str(json.dumps(message))
            if fut is None:
                return {}
            return await asyncio.wait_for(fut, timeout=15)

        async def send(method: str, params: dict[str, Any] | None = None, session_id: str | None = None) -> dict[str, Any]:
            return await send_raw(method, params, session_id, wait=True)

        async def maybe_capture(candidate: CaptureCandidate | None) -> None:
            if candidate is None or not candidate.url or not self._match or not self._capture:
                return
            if self._match(candidate):
                await self._capture(candidate)

        reader = asyncio.create_task(self._read_events(ws, pending, maybe_capture, send_raw))
        try:
            await send("Target.setDiscoverTargets", {"discover": True})
            await send("Target.setAutoAttach", {
                "autoAttach": True,
                "waitForDebuggerOnStart": False,
                "flatten": True,
            })
            session_id = await self._attach_page(send)
            await send("Page.enable", session_id=session_id)
            await send("Network.enable", session_id=session_id)
            await send(
                "Fetch.enable",
                {
                    "patterns": [
                        {"urlPattern": "*", "requestStage": "Request"},
                        {"urlPattern": "*", "requestStage": "Response"},
                    ]
                },
                session_id=session_id,
            )
            await send("Page.navigate", {"url": start_url}, session_id=session_id)
            await self._stop.wait()
        finally:
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _attach_page(self, send: Callable[..., Awaitable[dict[str, Any]]]) -> str | None:
        info = await send("Target.getTargets")
        targets = (info.get("result") or {}).get("targetInfos") or []
        page = next((t for t in targets if t.get("type") == "page"), None)
        if page is None:
            created = await send("Target.createTarget", {"url": "about:blank"})
            target_id = (created.get("result") or {}).get("targetId")
        else:
            target_id = page.get("targetId")
        attached = await send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        return (attached.get("result") or {}).get("sessionId")

    async def _read_events(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        pending: dict[int, asyncio.Future[dict[str, Any]]],
        maybe_capture: Callable[[CaptureCandidate | None], Awaitable[None]],
        send_raw: Callable[..., Awaitable[dict[str, Any]]],
    ) -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
                continue
            data = json.loads(msg.data)
            if "id" in data and data["id"] in pending:
                fut = pending.pop(data["id"])
                if not fut.done():
                    fut.set_result(data)
                continue
            method = data.get("method")
            params = data.get("params") or {}
            session_id = data.get("sessionId")
            if method == "Page.frameRequestedNavigation":
                await maybe_capture(
                    CaptureCandidate("navigation", str(params.get("url") or ""))
                )
            elif method == "Page.frameNavigated":
                frame = params.get("frame") or {}
                await maybe_capture(
                    CaptureCandidate("navigation", str(frame.get("url") or ""))
                )
            elif method == "Network.requestWillBeSent":
                request = params.get("request") or {}
                req_url = str(request.get("url") or "")
                await maybe_capture(CaptureCandidate("navigation", req_url))
                if "://" in req_url and not req_url.startswith(("http://", "https://")):
                    await maybe_capture(CaptureCandidate("protocol_handler", req_url))
                redirect = params.get("redirectResponse") or {}
                if redirect:
                    headers = redirect.get("headers") or {}
                    location = headers.get("location") or headers.get("Location")
                    status = redirect.get("status")
                    try:
                        status_code = int(status) if status is not None else None
                    except (TypeError, ValueError):
                        status_code = None
                    if location:
                        await maybe_capture(
                            CaptureCandidate(
                                "http_redirect",
                                str(location),
                                status_code,
                            )
                        )
            elif method == "Fetch.requestPaused":
                request = params.get("request") or {}
                req_url = str(request.get("url") or "")
                loc = location_from_headers(params.get("responseHeaders"))
                status = params.get("responseStatusCode")
                try:
                    status_code = int(status) if status is not None else None
                except (TypeError, ValueError):
                    status_code = None
                await maybe_capture(CaptureCandidate("navigation", req_url))
                if "://" in req_url and not req_url.startswith(("http://", "https://")):
                    await maybe_capture(CaptureCandidate("protocol_handler", req_url))
                redirect_candidate = None
                if loc and status_code is not None:
                    redirect_candidate = CaptureCandidate(
                        "http_redirect", loc, status_code
                    )
                    await maybe_capture(redirect_candidate)
                request_id = params.get("requestId")
                if request_id:
                    abort = bool(
                        self._match
                        and (
                            self._match(CaptureCandidate("navigation", req_url))
                            or (
                                "://" in req_url
                                and not req_url.startswith(("http://", "https://"))
                                and self._match(
                                    CaptureCandidate("protocol_handler", req_url)
                                )
                            )
                            or (redirect_candidate and self._match(redirect_candidate))
                        )
                    )
                    try:
                        if abort:
                            await send_raw(
                                "Fetch.failRequest",
                                {"requestId": request_id, "errorReason": "Aborted"},
                                session_id=session_id,
                                wait=False,
                            )
                        else:
                            await send_raw(
                                "Fetch.continueRequest",
                                {"requestId": request_id},
                                session_id=session_id,
                                wait=False,
                            )
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Fetch continue/fail failed", exc_info=True)
            elif method == "Target.targetCreated":
                info = params.get("targetInfo") or {}
                target_url = str(info.get("url") or "")
                await maybe_capture(CaptureCandidate("navigation", target_url))
                if "://" in target_url and not target_url.startswith(("http://", "https://")):
                    await maybe_capture(CaptureCandidate("protocol_handler", target_url))
