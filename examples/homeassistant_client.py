"""Home Assistant helper: discover Browser Companion and drive a login session.

Copy this module into a custom component (no PyPI package). Requires aiohttp,
which Home Assistant already provides.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

SLUG_SUFFIX = "browser_companion"
API_PORT = 8100
DEFAULT_TIMEOUT = 600


def hostname_from_addon_slug(slug: str) -> str:
    return slug.replace("_", "-")


def is_companion_slug(slug: str) -> bool:
    return str(slug).endswith(SLUG_SUFFIX)


def base_url_from_slug(slug: str, port: int = API_PORT) -> str:
    return f"http://{hostname_from_addon_slug(slug)}:{port}"


async def async_discover_base_url(hass: Any) -> str | None:
    """Return http://{addon-hostname}:8100 if the add-on is running."""
    try:
        from homeassistant.components.hassio import (
            get_addons_info,
            hostname_from_addon_slug as hass_hostname,
            is_hassio,
        )
    except ImportError:
        return None
    try:
        if not is_hassio(hass):
            return None
    except Exception:  # noqa: BLE001
        return None
    addons = get_addons_info(hass)
    if not addons:
        return None
    for slug, info in addons.items():
        if not is_companion_slug(str(slug)):
            continue
        state = (info or {}).get("state")
        if state not in (None, "started"):
            continue
        host = hass_hostname(str(slug))
        return f"http://{host}:{API_PORT}"
    return None


async def async_create_session(
    session: aiohttp.ClientSession,
    base_url: str,
    *,
    start_url: str,
    wait: dict[str, Any] | list[dict[str, Any]],
    client_id: str = "homeassistant",
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    payload = {
        "client_id": client_id,
        "start_url": start_url,
        "wait": wait,
        "timeout_seconds": timeout_seconds,
    }
    try:
        async with session.post(f"{base_url.rstrip('/')}/v1/sessions", json=payload) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                raise CompanionError(f"create_failed_{resp.status}")
            return body
    except aiohttp.ClientError as err:
        raise CompanionError("cannot_connect") from err


async def async_get_session(
    session: aiohttp.ClientSession, base_url: str, session_id: str
) -> dict[str, Any]:
    try:
        async with session.get(
            f"{base_url.rstrip('/')}/v1/sessions/{session_id}"
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                raise CompanionError(f"get_failed_{resp.status}")
            return body
    except aiohttp.ClientError as err:
        raise CompanionError("cannot_connect") from err


async def async_delete_session(
    session: aiohttp.ClientSession, base_url: str, session_id: str
) -> None:
    try:
        async with session.delete(
            f"{base_url.rstrip('/')}/v1/sessions/{session_id}"
        ) as resp:
            if resp.status >= 500:
                raise CompanionError(f"delete_failed_{resp.status}")
    except aiohttp.ClientError as err:
        raise CompanionError("cannot_connect") from err


async def async_wait_captured(
    session: aiohttp.ClientSession,
    base_url: str,
    session_id: str,
    *,
    poll_interval: float = 2.0,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Poll until status is captured, or raise CompanionError."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        data = await async_get_session(session, base_url, session_id)
        status = data.get("status")
        if status == "captured":
            return data
        if status == "expired":
            raise CompanionError("expired")
        if status == "error":
            raise CompanionError(str(data.get("error") or "error"))
        await asyncio.sleep(poll_interval)
    raise CompanionError("timeout")


class CompanionError(Exception):
    """Browser Companion API failure."""
