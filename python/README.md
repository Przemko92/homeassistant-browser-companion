# ha-browser-companion

Python client for the [Browser Companion](https://github.com/Przemko92/homeassistant-browser-companion) add-on. This directory is the PyPI package; the add-on itself is [`../browser_companion`](../browser_companion).

```bash
pip install ha-browser-companion
```

In `manifest.json`:

```json
{
  "requirements": ["ha-browser-companion>=0.1.0"]
}
```

Depends on `aiohttp` (already in Home Assistant). Home Assistant itself is **not** a package dependency: discovery and the config-flow mixin import it only when those helpers run.

## Config flow (recommended)

Most of the work is the progress/wait/retry UI. Subclass `CompanionLoginFlow` and implement two methods:

```python
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from ha_browser_companion import CompanionLoginFlow, CompanionStart, captured_query

from .const import DOMAIN, WAIT

class MyConfigFlow(CompanionLoginFlow, ConfigFlow, domain=DOMAIN):
    VERSION = 1
    companion_client_id = DOMAIN

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        if not self.companion_supervisor_present():
            return self.async_abort(reason="companion_requires_supervisor")
        return await self.async_step_companion()

    async def async_companion_start(self) -> CompanionStart:
        return CompanionStart(
            start_url="https://example.com/login",
            wait=WAIT,  # same payload as POST /v1/sessions
        )

    async def async_companion_finish(self, captured: dict) -> ConfigFlowResult:
        code = captured_query(captured, "code")
        if not code:
            return await self.async_step_companion_failed()
        return self.async_create_entry(title="Example", data={"code": code})
```

Optional: `navigate_after` and `success_message` on `CompanionStart`; `async_companion_on_close()` for extra cleanup (close an OAuth helper, drop PKCE state, …).

Cookie capture (Allegro-style):

```python
from ha_browser_companion import captured_cookie

cookie = captured_cookie(captured, "QXLSESSID")
```

### Translations

The mixin uses these keys. Copy them and change the surrounding sentences:

```json
{
  "config": {
    "step": {
      "companion": {
        "title": "Browser Companion",
        "description": "Open Browser Companion in a new window:\n\n[{companion_url}]({companion_href})\n\nSign in there. This screen waits until the add-on captures the result."
      },
      "companion_failed": {
        "title": "Browser Companion",
        "description": "Could not sign in with Browser Companion. Start the add-on, then submit to try again.\n\n[{companion_url}]({companion_href})"
      }
    },
    "progress": {
      "companion_wait": "Open Browser Companion in a new window:\n\n[{companion_url}]({companion_href})\n\nThis screen updates when the login is captured."
    },
    "abort": {
      "companion_requires_supervisor": "This integration needs Home Assistant OS or Supervised with the Browser Companion add-on."
    }
  }
}
```

Placeholders: `companion_url` (Ingress path or absolute URL), `companion_href` (`my.home-assistant.io` redirect that opens the sidebar panel).

## Low-level client

If you already have a config flow and only need HTTP:

```python
from ha_browser_companion import (
    CompanionError,
    async_create_session,
    async_delete_session,
    async_discover_companion,
    async_wait_captured,
)

found = await async_discover_companion(hass)
if not found:
    raise RuntimeError("Browser Companion add-on is not running")

created = await async_create_session(
    session,
    found.base_url,
    start_url=auth_url,
    wait={"event": "http_redirect", "location_prefixes": ["app://callback"]},
    client_id="my_integration",
)
captured = await async_wait_captured(session, found.base_url, created["id"])
code = captured["query"]["code"]
await async_delete_session(session, found.base_url, created["id"])
```

`BrowserCompanionClient(session, base_url)` is the same API as methods.

`async_discover_companion` lists Supervisor add-ons whose slug ends with `browser_companion`, then probes `GET /v1/health`. `async_wait_captured` polls about every 2 seconds until `captured`, or raises `CompanionError` (`expired`, `timeout`, `browser_unavailable`, …).

## Wait rules

Same JSON as the add-on session API. Typical patterns:

**OAuth redirect to a custom scheme**

```python
{"event": "http_redirect", "status_codes": [302], "location_prefixes": ["app://callback"]}
```

**Navigation + cookies**

```python
{
    "event": "navigation",
    "url_prefixes": ["https://example.com/account"],
    "cookies": ["SESSION"],
}
```

Full request/response reference: [add-on README](https://github.com/Przemko92/homeassistant-browser-companion#session-api).

## Publish to PyPI

From `python/` (after tests pass):

```bash
cd python
pip install build twine
python -m build
twine upload dist/*
```

Or create a GitHub Release: `.github/workflows/publish.yml` publishes with trusted publishing. On PyPI, add a trusted publisher for this GitHub repository (workflow `publish.yml`).

