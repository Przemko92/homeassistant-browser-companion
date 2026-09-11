"""Minimal Home Assistant config flow using ha-browser-companion.

Not a runnable integration — copy the pattern into custom_components/<domain>/.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from ha_browser_companion import (
    CompanionLoginFlow,
    CompanionStart,
    captured_cookie,
    captured_query,
)

DOMAIN = "example"
WAIT = {
    "event": "http_redirect",
    "status_codes": [302],
    "location_prefixes": ["app://example"],
}


class ExampleConfigFlow(CompanionLoginFlow, ConfigFlow, domain=DOMAIN):
    VERSION = 1
    companion_client_id = DOMAIN

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self.companion_supervisor_present():
            return self.async_abort(reason="companion_requires_supervisor")
        return await self.async_step_companion()

    async def async_companion_start(self) -> CompanionStart:
        return CompanionStart(
            start_url="https://example.com/login",
            wait=WAIT,
        )

    async def async_companion_finish(
        self, captured: dict[str, Any]
    ) -> ConfigFlowResult:
        code = captured_query(captured, "code")
        cookie = captured_cookie(captured, "SESSION")
        if not code and not cookie:
            return await self.async_step_companion_failed()
        return self.async_create_entry(
            title="Example",
            data={"code": code, "cookie": cookie},
        )
