"""Chrome DevTools Protocol client: navigate and intercept redirects."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from urllib.parse import urlparse

from intercept import (
    CaptureCandidate,
    cookies_complete,
    cookies_from_cdp,
    is_http_url,
    location_from_headers,
    matching_rule,
    should_abort_fetch,
)

_LOGGER = logging.getLogger("companion.cdp")

CDP_VERSION = "http://127.0.0.1:9222/json/version"
MatchFn = Callable[[CaptureCandidate], bool]
CaptureFn = Callable[[CaptureCandidate], Awaitable[bool]]
SendFn = Callable[..., Awaitable[dict[str, Any]]]
DEFAULT_SUCCESS_MESSAGE = "Sign-in succeeded.\nClose this window, or keep it open."
SUCCESS_PROMPT_JS = """
(function (message) {
  const host = document.body || document.documentElement;
  if (!host) return Promise.resolve(true);
  const old = document.getElementById("companion-success");
  if (old) old.remove();
  const wrap = document.createElement("div");
  wrap.id = "companion-success";
  wrap.setAttribute(
    "style",
    "position:fixed;inset:0;z-index:2147483647;background:rgba(15,23,42,.55);" +
      "display:flex;align-items:center;justify-content:center;" +
      "font-family:system-ui,sans-serif"
  );
  const box = document.createElement("div");
  box.setAttribute(
    "style",
    "background:#fff;color:#0f172a;padding:24px 28px;border-radius:14px;" +
      "max-width:380px;width:calc(100% - 48px);text-align:center;" +
      "box-shadow:0 16px 48px rgba(0,0,0,.28)"
  );
  const text = document.createElement("p");
  text.textContent = message;
  text.setAttribute(
    "style",
    "margin:0 0 18px;font-size:16px;line-height:1.45;white-space:pre-wrap"
  );
  const actions = document.createElement("div");
  actions.setAttribute(
    "style",
    "display:flex;gap:10px;justify-content:center;flex-wrap:wrap"
  );
  const btnStyle =
    "padding:8px 22px;font-size:15px;cursor:pointer;border:0;border-radius:8px;";
  const stayBtn = document.createElement("button");
  stayBtn.type = "button";
  stayBtn.textContent = "Keep open";
  stayBtn.setAttribute("style", btnStyle + "background:#e2e8f0;color:#0f172a");
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.textContent = "Close";
  closeBtn.setAttribute("style", btnStyle + "background:#2563eb;color:#fff");
  actions.appendChild(stayBtn);
  actions.appendChild(closeBtn);
  box.appendChild(text);
  box.appendChild(actions);
  wrap.appendChild(box);
  host.appendChild(wrap);
  closeBtn.focus();
  return new Promise((resolve) => {
    const finish = (shouldClose) => {
      wrap.remove();
      resolve(shouldClose);
    };
    closeBtn.addEventListener("click", () => finish(true), { once: true });
    stayBtn.addEventListener("click", () => finish(false), { once: true });
  });
})
""".strip()


def prompt_chose_close(resp: dict[str, Any] | None) -> bool:
    """True unless the overlay returned an explicit false (Keep open)."""
    if not resp:
        return True
    value = ((resp.get("result") or {}).get("result") or {}).get("value")
    return value is not False


class CdpController:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._cmd_id = 0
        self._match: MatchFn | None = None
        self._capture: CaptureFn | None = None
        self._send: SendFn | None = None
        self._target_id: str | None = None
        self._browser_context_id: str | None = None
        self._page_session_id: str | None = None
        self._finished = False
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._wait_rules: list[Any] | None = None
        self._navigate_after: dict[str, Any] | None = None
        self._did_navigate_after = False
        self._current_url = ""
        self._success_message = DEFAULT_SUCCESS_MESSAGE
        self._overlay_done = False

    async def start_session(
        self,
        start_url: str,
        match: MatchFn,
        capture: CaptureFn,
        *,
        wait_rules: list[Any] | None = None,
        navigate_after: dict[str, Any] | None = None,
        success_message: str | None = None,
    ) -> None:
        await self.stop()
        self._stop = asyncio.Event()
        self._match = match
        self._capture = capture
        self._send = None
        self._target_id = None
        self._browser_context_id = None
        self._page_session_id = None
        self._finished = False
        self._close_task = None
        self._wait_rules = wait_rules
        self._navigate_after = navigate_after
        self._did_navigate_after = False
        self._overlay_done = False
        self._current_url = ""
        self._success_message = (
            (success_message or "").strip() or DEFAULT_SUCCESS_MESSAGE
        )[:500]
        self._task = asyncio.create_task(
            self._run(start_url), name="companion-cdp"
        )

    async def stop(self) -> None:
        await self.wipe_and_close()
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    def schedule_close(self) -> None:
        """Show the success prompt, then close the login tab after Close."""
        if self._overlay_done:
            return
        if self._close_task is not None and not self._close_task.done():
            return
        self._close_task = asyncio.create_task(
            self._prompt_then_close(), name="companion-close-tab"
        )

    async def _prompt_then_close(self) -> None:
        should_close = True
        try:
            should_close = await self._show_success_prompt()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("success prompt failed", exc_info=True)
        if should_close:
            await self.wipe_and_close()
        else:
            self._overlay_done = True
            _LOGGER.info("Success overlay dismissed; leaving the tab open")

    async def _show_success_prompt(self) -> bool:
        send = self._send
        if send is None or self._finished:
            return True
        expression = f"{SUCCESS_PROMPT_JS}({json.dumps(self._success_message)})"
        try:
            resp = await send(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "awaitPromise": True,
                    "returnByValue": True,
                },
                session_id=self._page_session_id,
                timeout=600,
            )
            return prompt_chose_close(resp)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not show success prompt", exc_info=True)
            return True

    async def wipe_and_close(self) -> None:
        """Close the login tab and drop the ephemeral (incognito) context."""
        async with self._close_lock:
            if self._finished:
                return
            send = self._send
            if send is None:
                self._finished = True
                self._stop.set()
                return
            try:
                if self._page_session_id:
                    await send(
                        "Network.clearBrowserCookies",
                        session_id=self._page_session_id,
                    )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("clearBrowserCookies failed", exc_info=True)
            try:
                if self._target_id:
                    await send("Target.closeTarget", {"targetId": self._target_id})
            except Exception:  # noqa: BLE001
                _LOGGER.debug("closeTarget failed", exc_info=True)
            try:
                if self._browser_context_id:
                    await send(
                        "Target.disposeBrowserContext",
                        {"browserContextId": self._browser_context_id},
                    )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("disposeBrowserContext failed", exc_info=True)
            self._target_id = None
            self._browser_context_id = None
            self._page_session_id = None
            self._send = None
            self._finished = True
            self._stop.set()

    async def _maybe_capture_candidate(
        self, candidate: CaptureCandidate | None
    ) -> bool:
        if (
            candidate is None
            or not candidate.url
            or not self._match
            or not self._capture
            or self._finished
        ):
            return False
        if not self._match(candidate):
            return False
        rule = matching_rule(candidate, self._wait_rules or [])
        names = tuple(rule.cookies) if rule else ()
        if names:
            cookies = await self._fetch_named_cookies(names)
            if not cookies_complete(cookies, names):
                _LOGGER.debug(
                    "Matched %s; waiting for cookies %s",
                    candidate.url,
                    ",".join(names),
                )
                return False
            candidate = CaptureCandidate(
                candidate.event,
                candidate.url,
                candidate.status_code,
                cookies,
            )
        if not await self._capture(candidate):
            return False
        _LOGGER.info("Login captured; waiting for Close or Keep open")
        self.schedule_close()
        return True

    async def _try_capture_navigation(self) -> bool:
        return await self._maybe_capture_candidate(
            CaptureCandidate("navigation", self._current_url)
        )

    async def _refresh_current_url(self) -> None:
        send = self._send
        if send is None:
            return
        try:
            resp = await send(
                "Runtime.evaluate",
                {"expression": "String(location.href)", "returnByValue": True},
                session_id=self._page_session_id,
            )
            value = ((resp.get("result") or {}).get("result") or {}).get("value")
            if value:
                self._current_url = str(value)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("location.href failed", exc_info=True)

    def _cookie_urls(self) -> list[str]:
        urls: list[str] = []
        current = (self._current_url or "").split("#", 1)[0]
        if current and is_http_url(current):
            parsed = urlparse(current)
            urls.append(f"{parsed.scheme}://{parsed.netloc}/")
            urls.append(current)
        for rule in self._wait_rules or []:
            for prefix in getattr(rule, "prefixes", ()):
                if not is_http_url(prefix):
                    continue
                parsed = urlparse(prefix)
                origin = f"{parsed.scheme}://{parsed.netloc}/"
                if origin not in urls:
                    urls.append(origin)
                if prefix not in urls:
                    urls.append(prefix)
        return urls

    async def _fetch_named_cookies(self, names: tuple[str, ...]) -> dict[str, str]:
        send = self._send
        if send is None or not names:
            return {}
        attempts: list[tuple[str, dict[str, Any] | None, str | None]] = []
        if self._browser_context_id:
            attempts.append(
                (
                    "Storage.getCookies",
                    {"browserContextId": self._browser_context_id},
                    None,
                )
            )
        cookie_urls = self._cookie_urls()
        attempts.append(
            (
                "Network.getCookies",
                {"urls": cookie_urls} if cookie_urls else {},
                self._page_session_id,
            )
        )
        attempts.append(("Network.getAllCookies", None, self._page_session_id))
        attempts.append(("Network.getAllCookies", None, None))
        best: dict[str, str] = {}
        for method, params, session_id in attempts:
            try:
                resp = await send(method, params, session_id=session_id)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("%s failed", method, exc_info=True)
                continue
            if resp.get("error"):
                continue
            raw = (resp.get("result") or {}).get("cookies") or []
            found = cookies_from_cdp(raw, names)
            if cookies_complete(found, names):
                return found
            if len(found) > len(best):
                best = found
        return best

    async def _maybe_navigate_after(self) -> None:
        spec = self._navigate_after
        send = self._send
        if (
            spec is None
            or send is None
            or self._did_navigate_after
            or self._finished
            or self._stop.is_set()
        ):
            return
        target = str(spec.get("url") or "")
        names = tuple(spec.get("cookies") or ())
        if not target or not names:
            return
        current = self._current_url or ""
        if current.startswith(target):
            return
        cookies = await self._fetch_named_cookies(names)
        if not cookies_complete(cookies, names):
            return
        self._did_navigate_after = True
        _LOGGER.info("Login cookies present; navigating to %s", target)
        try:
            await send(
                "Page.navigate",
                {"url": target},
                session_id=self._page_session_id,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("navigate_after failed", exc_info=True)
            self._did_navigate_after = False

    async def _cookie_watch(self) -> None:
        while not self._stop.is_set() and not self._finished:
            await asyncio.sleep(1.0)
            if self._finished:
                return
            try:
                await self._refresh_current_url()
                await self._maybe_navigate_after()
                await self._try_capture_navigation()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("cookie watch failed", exc_info=True)

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
            timeout: float = 15,
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
            return await asyncio.wait_for(fut, timeout=timeout)

        async def send(
            method: str,
            params: dict[str, Any] | None = None,
            session_id: str | None = None,
            timeout: float = 15,
        ) -> dict[str, Any]:
            return await send_raw(method, params, session_id, wait=True, timeout=timeout)

        self._send = send

        async def maybe_capture(candidate: CaptureCandidate | None) -> bool:
            return await self._maybe_capture_candidate(candidate)

        reader = asyncio.create_task(
            self._read_events(ws, pending, maybe_capture, send_raw)
        )
        cookie_task: asyncio.Task[None] | None = None
        try:
            await send("Target.setDiscoverTargets", {"discover": True})
            await send("Target.setAutoAttach", {
                "autoAttach": True,
                "waitForDebuggerOnStart": False,
                "flatten": True,
            })
            session_id = await self._open_incognito_page(send)
            self._page_session_id = session_id
            await send("Page.enable", session_id=session_id)
            await send("Runtime.enable", session_id=session_id)
            await send(
                "Page.setLifecycleEventsEnabled",
                {"enabled": True},
                session_id=session_id,
            )
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
            cookie_task = asyncio.create_task(
                self._cookie_watch(), name="companion-cookies"
            )
            await send("Page.navigate", {"url": start_url}, session_id=session_id)
            await self._stop.wait()
        finally:
            if cookie_task is not None:
                cookie_task.cancel()
                try:
                    await cookie_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._send = None

    async def _open_incognito_page(self, send: SendFn) -> str | None:
        """New tab in an ephemeral BrowserContext (Chromium incognito profile)."""
        try:
            ctx = await send("Target.createBrowserContext", {"disposeOnDetach": True})
            error = ctx.get("error")
            if error:
                raise RuntimeError(str(error))
            self._browser_context_id = (ctx.get("result") or {}).get("browserContextId")
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Incognito browser context unavailable", exc_info=True)
            self._browser_context_id = None
        params: dict[str, Any] = {"url": "about:blank"}
        if self._browser_context_id:
            params["browserContextId"] = self._browser_context_id
        created = await send("Target.createTarget", params)
        self._target_id = (created.get("result") or {}).get("targetId")
        if not self._target_id:
            return await self._attach_existing_page(send)
        await send("Target.activateTarget", {"targetId": self._target_id})
        attached = await send(
            "Target.attachToTarget",
            {"targetId": self._target_id, "flatten": True},
        )
        _LOGGER.info("Opened incognito login tab %s", self._target_id)
        return (attached.get("result") or {}).get("sessionId")

    async def _attach_existing_page(self, send: SendFn) -> str | None:
        info = await send("Target.getTargets")
        targets = (info.get("result") or {}).get("targetInfos") or []
        page = next((t for t in targets if t.get("type") == "page"), None)
        if page is None:
            created = await send("Target.createTarget", {"url": "about:blank"})
            target_id = (created.get("result") or {}).get("targetId")
        else:
            target_id = page.get("targetId")
        self._target_id = target_id
        attached = await send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        return (attached.get("result") or {}).get("sessionId")

    async def _read_events(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        pending: dict[int, asyncio.Future[dict[str, Any]]],
        maybe_capture: Callable[[CaptureCandidate | None], Awaitable[bool]],
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
            captured = False
            abort_fetch = False
            if method == "Page.frameRequestedNavigation":
                url = str(params.get("url") or "")
                if url and not is_http_url(url):
                    captured = await maybe_capture(
                        CaptureCandidate("protocol_handler", url)
                    )
            elif method == "Page.frameNavigated":
                frame = params.get("frame") or {}
                url = str(frame.get("url") or "")
                if not frame.get("parentId"):
                    self._current_url = url
                    await self._maybe_navigate_after()
            elif method == "Page.navigatedWithinDocument":
                url = str(params.get("url") or "")
                if url:
                    self._current_url = url
            elif method == "Page.loadEventFired":
                captured = await maybe_capture(
                    CaptureCandidate("navigation", self._current_url)
                )
            elif method == "Page.lifecycleEvent":
                if params.get("name") in {"load", "networkIdle"}:
                    captured = await maybe_capture(
                        CaptureCandidate("navigation", self._current_url)
                    )
            elif method == "Network.requestWillBeSent":
                request = params.get("request") or {}
                req_url = str(request.get("url") or "")
                if req_url and not is_http_url(req_url):
                    captured = await maybe_capture(
                        CaptureCandidate("protocol_handler", req_url)
                    )
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
                        captured = await maybe_capture(
                            CaptureCandidate(
                                "http_redirect",
                                str(location),
                                status_code,
                            )
                        ) or captured
            elif method == "Fetch.requestPaused":
                request = params.get("request") or {}
                req_url = str(request.get("url") or "")
                loc = location_from_headers(params.get("responseHeaders"))
                status = params.get("responseStatusCode")
                try:
                    status_code = int(status) if status is not None else None
                except (TypeError, ValueError):
                    status_code = None
                if req_url and not is_http_url(req_url):
                    captured = await maybe_capture(
                        CaptureCandidate("protocol_handler", req_url)
                    )
                    abort_fetch = captured and should_abort_fetch(
                        "protocol_handler", req_url
                    )
                if loc and status_code is not None:
                    redirect_candidate = CaptureCandidate(
                        "http_redirect", loc, status_code
                    )
                    hit = await maybe_capture(redirect_candidate)
                    captured = hit or captured
                    abort_fetch = abort_fetch or (
                        hit and should_abort_fetch("http_redirect", loc)
                    )
                request_id = params.get("requestId")
                if request_id:
                    try:
                        if abort_fetch:
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
                if target_url and not is_http_url(target_url):
                    captured = await maybe_capture(
                        CaptureCandidate("protocol_handler", target_url)
                    )
            if captured:
                _LOGGER.info("Login captured; waiting for Close or Keep open")
                self.schedule_close()
