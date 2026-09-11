"""Config-flow mixin without Home Assistant installed."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ha_browser_companion import CompanionError, CompanionLoginFlow, CompanionStart


class FakeHass:
    def async_create_task(self, coro):
        return asyncio.get_running_loop().create_task(coro)


class FakeFlow(CompanionLoginFlow):
    companion_client_id = "demo"

    def __init__(self) -> None:
        self.hass = FakeHass()
        self.forms: list[dict[str, Any]] = []
        self.progress: list[dict[str, Any]] = []

    def async_show_progress(self, **kwargs: Any) -> dict[str, Any]:
        self.progress.append(kwargs)
        return {"type": "progress", **kwargs}

    def async_show_progress_done(self, next_step_id: str) -> dict[str, Any]:
        return {"type": "progress_done", "next_step_id": next_step_id}

    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        self.forms.append(kwargs)
        return {"type": "form", **kwargs}

    async def async_companion_start(self) -> CompanionStart:
        return CompanionStart(
            start_url="https://example.com/login",
            wait={"event": "http_redirect", "location_prefixes": ["app://x"]},
        )

    async def async_companion_finish(self, captured: dict[str, Any]) -> dict[str, Any]:
        return {"type": "create_entry", "data": captured}


@pytest.fixture
def flow() -> FakeFlow:
    return FakeFlow()


async def test_create_session_failure_shows_retry_form(flow: FakeFlow):
    with (
        patch(
            "ha_browser_companion.flow.async_discover_companion",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "ha_browser_companion.flow.async_create_session",
            new=AsyncMock(side_effect=CompanionError("cannot_connect")),
        ),
        patch(
            "ha_browser_companion.flow.CompanionLoginFlow._companion_http",
            return_value=object(),
        ),
    ):
        result = await flow.async_step_companion()
    assert result["type"] == "form"
    assert result["step_id"] == "companion_failed"
    assert "companion_href" in result["description_placeholders"]


async def test_progress_then_finish(flow: FakeFlow):
    created = {"id": "sess-1", "status": "pending"}
    captured = {"id": "sess-1", "status": "captured", "query": {"code": "abc"}}

    with (
        patch(
            "ha_browser_companion.flow.async_discover_companion",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "ha_browser_companion.flow.async_create_session",
            new=AsyncMock(return_value=created),
        ) as create,
        patch(
            "ha_browser_companion.flow.async_wait_captured",
            new=AsyncMock(return_value=captured),
        ),
        patch(
            "ha_browser_companion.flow.async_delete_session",
            new=AsyncMock(),
        ) as delete,
        patch(
            "ha_browser_companion.flow.CompanionLoginFlow._companion_http",
            return_value=object(),
        ),
    ):
        first = await flow.async_step_companion()
        assert first["type"] == "progress"
        assert create.await_count == 1
        await flow._companion_task
        second = await flow.async_step_companion()
        assert second == {"type": "progress_done", "next_step_id": "companion_done"}
        finished = await flow.async_step_companion_done()
        assert finished["type"] == "create_entry"
        assert finished["data"]["query"]["code"] == "abc"
        delete.assert_awaited()


async def test_wait_error_goes_to_failed(flow: FakeFlow):
    with (
        patch(
            "ha_browser_companion.flow.async_discover_companion",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "ha_browser_companion.flow.async_create_session",
            new=AsyncMock(return_value={"id": "sess-1"}),
        ),
        patch(
            "ha_browser_companion.flow.async_wait_captured",
            new=AsyncMock(side_effect=CompanionError("expired")),
        ),
        patch(
            "ha_browser_companion.flow.CompanionLoginFlow._companion_http",
            return_value=object(),
        ),
    ):
            await flow.async_step_companion()
            assert flow._companion_task is not None
            await asyncio.wait([flow._companion_task])
            result = await flow.async_step_companion()
    assert result == {"type": "progress_done", "next_step_id": "companion_failed"}
