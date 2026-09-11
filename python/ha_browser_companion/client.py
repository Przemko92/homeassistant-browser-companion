"""Discover Browser Companion and drive a login session over HTTP."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

SLUG_SUFFIX = "browser_companion"
API_PORT = 8100
DEFAULT_TIMEOUT = 600
HEALTH_TIMEOUT = 3
KNOWN_SLUGS = ("local_browser_companion", "browser_companion")
FALLBACK_SLUG = KNOWN_SLUGS[0]
FALLBACK_BASE_URL = f"http://{FALLBACK_SLUG.replace('_', '-')}:{API_PORT}"
RUNNING_STATES = frozenset({"started", "startup", "running", ""})
INGRESS_REDIRECT = "https://my.home-assistant.io/redirect/supervisor_ingress/"


class CompanionError(Exception):
    """Browser Companion API failure.

    ``str(err)`` is a short code such as ``cannot_connect``, ``expired``,
    ``timeout``, ``browser_unavailable``, or ``create_failed_400``.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CompanionEndpoint:
    """Reachable Companion API plus the Supervisor slug for Ingress links."""

    base_url: str
    slug: str

    @property
    def ingress_href(self) -> str:
        return ingress_path(self.slug)

    def client(self, session: aiohttp.ClientSession) -> BrowserCompanionClient:
        return BrowserCompanionClient(session, self.base_url)


def hostname_from_addon_slug(slug: str) -> str:
    return slug.replace("_", "-")


def is_companion_slug(slug: str) -> bool:
    return str(slug).endswith(SLUG_SUFFIX)


def base_url_from_slug(slug: str, port: int = API_PORT) -> str:
    return f"http://{hostname_from_addon_slug(slug)}:{port}"


def slug_from_api_base(base_url: str) -> str:
    """Invert hostname_from_addon_slug for a Companion API base URL."""
    host = (urlparse(base_url).hostname or "").strip()
    if not host:
        return FALLBACK_SLUG
    return host.replace("-", "_")


def ingress_path(slug: str) -> str:
    """Frontend route that opens the add-on Ingress UI (sidebar panel)."""
    return f"/hassio/ingress/{slug}"


def companion_redirect_href(slug: str) -> str:
    """my.home-assistant.io link that opens the add-on Ingress panel."""
    return f"{INGRESS_REDIRECT}?addon={slug}"


def companion_ui_url(hass: Any, slug: str) -> str:
    """Absolute Ingress URL for display; falls back to a same-origin path."""
    path = ingress_path(slug)
    if hass is None:
        return path
    try:
        from homeassistant.helpers.network import get_url

        return f"{get_url(hass).rstrip('/')}{path}"
    except Exception:  # noqa: BLE001
        internal = getattr(getattr(hass, "config", None), "internal_url", None)
        if internal:
            return f"{str(internal).rstrip('/')}{path}"
        return path


def companion_placeholders(hass: Any, slug: str) -> dict[str, str]:
    """Placeholders for config-flow translations (`companion_url`, `companion_href`)."""
    return {
        "companion_url": companion_ui_url(hass, slug),
        "companion_href": companion_redirect_href(slug),
    }


def addon_state(info: Any) -> str:
    """Normalize Supervisor add-on state (dict, model, or enum)."""
    if info is None:
        return ""
    raw = info.get("state") if isinstance(info, dict) else getattr(info, "state", None)
    if raw is None:
        return ""
    text = str(raw)
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.lower()


def addon_is_available(info: Any) -> bool:
    """True when the add-on is running or state is unknown (older caches)."""
    return addon_state(info) in RUNNING_STATES


def candidate_base_urls(
    *,
    addons: dict[str, Any] | None = None,
    addon_list: list[Any] | None = None,
    extra_slugs: tuple[str, ...] = KNOWN_SLUGS,
) -> list[str]:
    """Unique API base URLs to probe, started add-ons first."""
    found: list[str] = []

    def add_slug(slug: Any, info: Any = None) -> None:
        text = str(slug or "")
        if not is_companion_slug(text):
            return
        if info is not None and not addon_is_available(info):
            return
        url = base_url_from_slug(text)
        if url not in found:
            found.append(url)

    if isinstance(addons, dict):
        for slug, info in addons.items():
            add_slug(slug, info)
    for item in addon_list or []:
        if isinstance(item, dict):
            add_slug(item.get("slug"), item)
        else:
            add_slug(getattr(item, "slug", None), item)
    for slug in extra_slugs:
        add_slug(slug)
    return found


def supervisor_present(hass: Any) -> bool:
    try:
        from homeassistant.helpers.hassio import is_hassio as helpers_is_hassio

        if helpers_is_hassio(hass):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from homeassistant.components.hassio import is_hassio as hassio_is_hassio

        if hassio_is_hassio(hass):
            return True
    except Exception:  # noqa: BLE001
        pass
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def captured_query(captured: dict[str, Any], key: str) -> str | None:
    """Return a query/fragment value from a captured session payload."""
    query = captured.get("query") or {}
    value = query.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def captured_cookie(captured: dict[str, Any], name: str) -> str:
    """Return a named cookie from a captured session payload (case-insensitive)."""
    cookies = captured.get("cookies") or {}
    value = cookies.get(name)
    if value is None:
        for cookie_name, cookie_value in cookies.items():
            if str(cookie_name).lower() == name.lower():
                value = cookie_value
                break
    return str(value or "").strip()


def _hassio_addon_sources(hass: Any) -> tuple[dict[str, Any] | None, list[Any] | None]:
    addons: dict[str, Any] | None = None
    addon_list: list[Any] | None = None
    try:
        from homeassistant.components.hassio import get_addons_info

        addons = get_addons_info(hass)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("get_addons_info unavailable", exc_info=True)
    try:
        from homeassistant.components.hassio import get_addons_list

        addon_list = get_addons_list(hass)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("get_addons_list unavailable", exc_info=True)
    return addons, addon_list


async def _slugs_from_supervisor_api(session: aiohttp.ClientSession) -> list[Any]:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return []
    host = os.environ.get("SUPERVISOR") or "supervisor"
    base = host if "://" in host else f"http://{host}"
    try:
        async with session.get(
            f"{base.rstrip('/')}/addons",
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status >= 400:
                return []
            body = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return []
    data = body.get("data") if isinstance(body, dict) else None
    addons = (data or body or {}).get("addons") if isinstance(data or body, dict) else None
    return addons if isinstance(addons, list) else []


async def _read_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError):
        return None


def _error_code(body: Any, fallback: str) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if error:
            return str(error)
    return fallback


async def _session_for_hass(hass: Any) -> tuple[aiohttp.ClientSession, bool]:
    try:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        return async_get_clientsession(hass), False
    except Exception:  # noqa: BLE001
        return aiohttp.ClientSession(), True


async def async_health_ok(session: aiohttp.ClientSession, base_url: str) -> bool:
    try:
        async with session.get(
            f"{base_url.rstrip('/')}/v1/health",
            timeout=aiohttp.ClientTimeout(total=HEALTH_TIMEOUT),
        ) as resp:
            if resp.status >= 400:
                return False
            body = await resp.json(content_type=None)
            return bool(isinstance(body, dict) and body.get("ok"))
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return False


async def async_discover_base_url(hass: Any) -> str | None:
    """Return http://{addon-hostname}:8100 if the add-on API answers /v1/health."""
    if hass is not None and not supervisor_present(hass):
        return None

    session, close = await _session_for_hass(hass)
    try:
        addons, addon_list = _hassio_addon_sources(hass) if hass is not None else (None, None)
        extra_list = await _slugs_from_supervisor_api(session)
        merged_list = [*(addon_list or []), *extra_list]
        extra_slugs = KNOWN_SLUGS if os.environ.get("SUPERVISOR_TOKEN") else ()
        urls = candidate_base_urls(
            addons=addons,
            addon_list=merged_list,
            extra_slugs=extra_slugs or KNOWN_SLUGS,
        )
        for url in urls:
            if await async_health_ok(session, url):
                _LOGGER.debug("Browser Companion at %s", url)
                return url
        _LOGGER.warning(
            "Browser Companion add-on not reachable on %s", urls or KNOWN_SLUGS
        )
        return None
    finally:
        if close:
            await session.close()


async def async_discover_companion(hass: Any) -> CompanionEndpoint | None:
    """Return API base + slug if the add-on answers /v1/health."""
    base = await async_discover_base_url(hass)
    if not base:
        return None
    return CompanionEndpoint(base_url=base, slug=slug_from_api_base(base))


async def async_create_session(
    session: aiohttp.ClientSession,
    base_url: str,
    *,
    start_url: str,
    wait: dict[str, Any] | list[dict[str, Any]],
    client_id: str = "homeassistant",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    navigate_after: dict[str, Any] | None = None,
    success_message: str | None = None,
) -> dict[str, Any]:
    return await BrowserCompanionClient(session, base_url).create_session(
        start_url=start_url,
        wait=wait,
        client_id=client_id,
        timeout_seconds=timeout_seconds,
        navigate_after=navigate_after,
        success_message=success_message,
    )


async def async_get_session(
    session: aiohttp.ClientSession, base_url: str, session_id: str
) -> dict[str, Any]:
    return await BrowserCompanionClient(session, base_url).get_session(session_id)


async def async_delete_session(
    session: aiohttp.ClientSession, base_url: str, session_id: str
) -> None:
    await BrowserCompanionClient(session, base_url).delete_session(session_id)


async def async_wait_captured(
    session: aiohttp.ClientSession,
    base_url: str,
    session_id: str,
    *,
    poll_interval: float = 2.0,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Poll until status is captured, or raise CompanionError."""
    return await BrowserCompanionClient(session, base_url).wait_captured(
        session_id,
        poll_interval=poll_interval,
        timeout_seconds=timeout_seconds,
    )


class BrowserCompanionClient:
    """Thin async wrapper around ``/v1/sessions``."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")

    async def create_session(
        self,
        *,
        start_url: str,
        wait: dict[str, Any] | list[dict[str, Any]],
        client_id: str = "homeassistant",
        timeout_seconds: int = DEFAULT_TIMEOUT,
        navigate_after: dict[str, Any] | None = None,
        success_message: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "client_id": client_id,
            "start_url": start_url,
            "wait": wait,
            "timeout_seconds": timeout_seconds,
        }
        if navigate_after:
            payload["navigate_after"] = navigate_after
        if success_message:
            payload["success_message"] = success_message
        try:
            async with self.session.post(f"{self.base_url}/v1/sessions", json=payload) as resp:
                body = await _read_json(resp)
                if resp.status >= 400:
                    raise CompanionError(_error_code(body, f"create_failed_{resp.status}"))
                if not isinstance(body, dict):
                    raise CompanionError("create_failed_invalid_json")
                return body
        except aiohttp.ClientError as err:
            raise CompanionError("cannot_connect") from err

    async def get_session(self, session_id: str) -> dict[str, Any]:
        try:
            async with self.session.get(
                f"{self.base_url}/v1/sessions/{session_id}"
            ) as resp:
                body = await _read_json(resp)
                if resp.status >= 400:
                    raise CompanionError(_error_code(body, f"get_failed_{resp.status}"))
                if not isinstance(body, dict):
                    raise CompanionError("get_failed_invalid_json")
                return body
        except aiohttp.ClientError as err:
            raise CompanionError("cannot_connect") from err

    async def delete_session(self, session_id: str) -> None:
        try:
            async with self.session.delete(
                f"{self.base_url}/v1/sessions/{session_id}"
            ) as resp:
                if resp.status >= 500:
                    body = await _read_json(resp)
                    raise CompanionError(_error_code(body, f"delete_failed_{resp.status}"))
        except aiohttp.ClientError as err:
            raise CompanionError("cannot_connect") from err

    async def wait_captured(
        self,
        session_id: str,
        *,
        poll_interval: float = 2.0,
        timeout_seconds: int = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            data = await self.get_session(session_id)
            status = data.get("status")
            if status == "captured":
                return data
            if status == "expired":
                raise CompanionError("expired")
            if status == "error":
                raise CompanionError(str(data.get("error") or "error"))
            await asyncio.sleep(poll_interval)
        raise CompanionError("timeout")
