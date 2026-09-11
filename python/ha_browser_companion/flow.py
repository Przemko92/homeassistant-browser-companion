"""Home Assistant config-flow mixin for Browser Companion login."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

try:
    import voluptuous as vol

    RETRY_SCHEMA: Any = vol.Schema({})
except ImportError:  # pragma: no cover - Home Assistant always ships voluptuous
    RETRY_SCHEMA = {}

from .client import (
    DEFAULT_TIMEOUT,
    FALLBACK_BASE_URL,
    FALLBACK_SLUG,
    CompanionError,
    async_create_session,
    async_delete_session,
    async_discover_companion,
    async_wait_captured,
    companion_placeholders,
    supervisor_present,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class CompanionStart:
    """Arguments for ``POST /v1/sessions``.

    Build this in ``async_companion_start`` right before Chromium opens.
    """

    start_url: str
    wait: dict[str, Any] | list[dict[str, Any]]
    client_id: str | None = None
    timeout_seconds: int | None = None
    navigate_after: dict[str, Any] | None = None
    success_message: str | None = None


class CompanionLoginFlow:
    """Mixin for ``homeassistant.config_entries.ConfigFlow``.

    Subclass implements ``async_companion_start`` and
    ``async_companion_finish``. Call ``await self.async_step_companion()``
    from ``async_step_user`` (or after the user picks this sign-in method).

    Required translation keys: ``step.companion``, ``step.companion_failed``,
    ``progress.companion_wait``. Placeholders: ``companion_url``,
    ``companion_href``.
    """

    companion_client_id: str = "homeassistant"
    companion_timeout_seconds: int = DEFAULT_TIMEOUT
    companion_fallback_slug: str = FALLBACK_SLUG
    companion_fallback_base_url: str = FALLBACK_BASE_URL

    def _companion_init(self) -> None:
        if getattr(self, "_companion_ready", False):
            return
        self._companion_ready = True
        self._companion_base: str | None = None
        self._companion_slug: str = self.companion_fallback_slug
        self._companion_session_id: str | None = None
        self._companion_task: asyncio.Task[dict[str, Any]] | None = None
        self._companion_captured: dict[str, Any] | None = None

    def _reset_companion_wait(self) -> None:
        self._companion_init()
        self._companion_task = None
        self._companion_session_id = None
        self._companion_captured = None

    def _companion_placeholders(self) -> dict[str, str]:
        self._companion_init()
        slug = self._companion_slug or self.companion_fallback_slug
        return companion_placeholders(getattr(self, "hass", None), slug)

    def _companion_http(self) -> Any:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        return async_get_clientsession(self.hass)

    async def async_companion_start(self) -> CompanionStart:
        """Return the login URL and wait rules for a new Chromium session."""
        raise NotImplementedError

    async def async_companion_finish(self, captured: dict[str, Any]) -> Any:
        """Turn a captured payload into a config entry (or a failed step)."""
        raise NotImplementedError

    async def async_companion_on_close(self) -> None:
        """Extra cleanup after the Companion session is deleted."""

    def companion_supervisor_present(self) -> bool:
        return supervisor_present(getattr(self, "hass", None))

    async def _refresh_companion_endpoint(self) -> None:
        self._companion_init()
        found = await async_discover_companion(self.hass)
        if found is None:
            self._companion_base = None
            return
        self._companion_base = found.base_url
        self._companion_slug = found.slug

    def _show_companion_progress(self) -> Any:
        self._companion_init()
        return self.async_show_progress(
            step_id="companion",
            progress_action="companion_wait",
            progress_task=self._companion_task,
            description_placeholders=self._companion_placeholders(),
        )

    async def _wait_companion(self) -> dict[str, Any]:
        self._companion_init()
        assert self._companion_base is not None
        assert self._companion_session_id is not None
        return await async_wait_captured(
            self._companion_http(),
            self._companion_base,
            self._companion_session_id,
            timeout_seconds=self.companion_timeout_seconds,
        )

    async def async_companion_close(self) -> None:
        self._companion_init()
        session_id = self._companion_session_id
        base = self._companion_base
        self._companion_session_id = None
        if session_id and base:
            try:
                await async_delete_session(self._companion_http(), base, session_id)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to delete Companion session", exc_info=True)
        await self.async_companion_on_close()

    async def async_step_companion(self, user_input: dict[str, Any] | None = None) -> Any:
        """Open Chromium via the add-on and wait until a wait-rule matches."""
        self._companion_init()
        if self._companion_base is None:
            await self._refresh_companion_endpoint()
        if not self._companion_base:
            self._companion_base = self.companion_fallback_base_url
            self._companion_slug = self.companion_fallback_slug

        if self._companion_task is None:
            try:
                spec = await self.async_companion_start()
                created = await async_create_session(
                    self._companion_http(),
                    self._companion_base,
                    start_url=spec.start_url,
                    wait=spec.wait,
                    client_id=spec.client_id or self.companion_client_id,
                    timeout_seconds=spec.timeout_seconds or self.companion_timeout_seconds,
                    navigate_after=spec.navigate_after,
                    success_message=spec.success_message,
                )
            except (CompanionError, TimeoutError) as err:
                _LOGGER.warning("Browser Companion unavailable: %s", err)
                self._reset_companion_wait()
                await self.async_companion_close()
                return await self.async_step_companion_failed()
            self._companion_session_id = str(created["id"])
            create_task = getattr(self.hass, "async_create_task", asyncio.create_task)
            self._companion_task = create_task(self._wait_companion())
            return self._show_companion_progress()

        if not self._companion_task.done():
            return self._show_companion_progress()
        if self._companion_task.cancelled():
            self._reset_companion_wait()
            return self.async_show_progress_done(next_step_id="companion_failed")
        try:
            self._companion_captured = self._companion_task.result()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Browser Companion wait failed: %s", err)
            return self.async_show_progress_done(next_step_id="companion_failed")
        return self.async_show_progress_done(next_step_id="companion_done")

    async def async_step_companion_done(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        self._companion_init()
        captured = self._companion_captured or {}
        try:
            return await self.async_companion_finish(captured)
        finally:
            await self.async_companion_close()

    async def async_step_companion_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        self._reset_companion_wait()
        await self.async_companion_close()
        if user_input is None:
            return self.async_show_form(
                step_id="companion_failed",
                data_schema=RETRY_SCHEMA,
                description_placeholders=self._companion_placeholders(),
            )
        self._companion_base = None
        return await self.async_step_companion()

    async def async_remove(self) -> None:
        await self.async_companion_close()
        parent = getattr(super(), "async_remove", None)
        if parent is not None:
            result = parent()
            if asyncio.iscoroutine(result):
                await result
