"""Discovery helpers and HTTP client for ha-browser-companion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from ha_browser_companion import (
    BrowserCompanionClient,
    CompanionEndpoint,
    CompanionError,
    addon_is_available,
    addon_state,
    async_create_session,
    async_delete_session,
    async_get_session,
    async_wait_captured,
    base_url_from_slug,
    candidate_base_urls,
    captured_cookie,
    captured_query,
    companion_placeholders,
    companion_redirect_href,
    companion_ui_url,
    hostname_from_addon_slug,
    ingress_path,
    is_companion_slug,
    slug_from_api_base,
)


def test_hostname_from_addon_slug():
    assert hostname_from_addon_slug("local_browser_companion") == "local-browser-companion"
    assert hostname_from_addon_slug("abc123_browser_companion") == "abc123-browser-companion"


def test_base_url_from_slug():
    assert base_url_from_slug("local_browser_companion") == "http://local-browser-companion:8100"


def test_is_companion_slug():
    assert is_companion_slug("local_browser_companion")
    assert is_companion_slug("deadbeef_browser_companion")
    assert not is_companion_slug("mosquitto")
    assert not is_companion_slug("")


def test_addon_is_available():
    assert addon_is_available({"state": "started"})
    assert addon_is_available({"state": "running"})
    assert addon_is_available({"state": "startup"})
    assert addon_is_available({"state": ""})
    assert addon_is_available(None)
    assert addon_is_available(SimpleNamespace(state="AddonState.STARTED"))
    assert not addon_is_available({"state": "stopped"})
    assert not addon_is_available({"state": "error"})


def test_addon_state_from_enum_style():
    assert addon_state(SimpleNamespace(state="AddonState.RUNNING")) == "running"
    assert addon_state({"state": "STARTED"}) == "started"


def test_candidate_base_urls_prefers_running_addons():
    urls = candidate_base_urls(
        addons={
            "local_browser_companion": {"state": "started"},
            "mosquitto": {"state": "started"},
            "deadbeef_browser_companion": {"state": "stopped"},
        },
        extra_slugs=("browser_companion",),
    )
    assert urls[0] == "http://local-browser-companion:8100"
    assert "http://browser-companion:8100" in urls
    assert "http://deadbeef-browser-companion:8100" not in urls
    assert all("mosquitto" not in url for url in urls)


def test_candidate_base_urls_from_addon_list():
    urls = candidate_base_urls(
        addon_list=[{"slug": "local_browser_companion", "state": "running"}],
        extra_slugs=(),
    )
    assert urls == ["http://local-browser-companion:8100"]


def test_slug_and_ingress_url_from_api_base():
    assert slug_from_api_base("http://local-browser-companion:8100") == (
        "local_browser_companion"
    )
    assert ingress_path("local_browser_companion") == (
        "/hassio/ingress/local_browser_companion"
    )
    assert companion_ui_url(None, "local_browser_companion") == (
        "/hassio/ingress/local_browser_companion"
    )
    assert companion_redirect_href("local_browser_companion") == (
        "https://my.home-assistant.io/redirect/supervisor_ingress/"
        "?addon=local_browser_companion"
    )
    placeholders = companion_placeholders(None, "local_browser_companion")
    assert placeholders["companion_url"].endswith("/hassio/ingress/local_browser_companion")
    assert "addon=local_browser_companion" in placeholders["companion_href"]


def test_endpoint_client():
    endpoint = CompanionEndpoint(
        base_url="http://local-browser-companion:8100",
        slug="local_browser_companion",
    )
    assert endpoint.ingress_href == "/hassio/ingress/local_browser_companion"
    client = endpoint.client(AsyncMock())
    assert isinstance(client, BrowserCompanionClient)
    assert client.base_url == "http://local-browser-companion:8100"


def test_captured_helpers():
    captured = {
        "url": "app://x?code=abc",
        "query": {"code": "abc"},
        "cookies": {"QXLSESSID": "tok"},
    }
    assert captured_query(captured, "code") == "abc"
    assert captured_query(captured, "missing") is None
    assert captured_cookie(captured, "QXLSESSID") == "tok"
    assert captured_cookie(captured, "qxlSESSID") == "tok"
    assert captured_cookie(captured, "other") == ""


@pytest.fixture
async def api_client():
    store: dict[str, dict] = {}

    async def create(request: web.Request) -> web.Response:
        body = await request.json()
        if not body.get("start_url"):
            return web.json_response({"error": "start_url_required"}, status=400)
        store["session"] = {
            "id": "sess-1",
            "status": "pending",
            "client_id": body.get("client_id"),
            "payload": body,
        }
        return web.json_response(
            {"id": "sess-1", "status": "pending", "client_id": body.get("client_id")},
            status=201,
        )

    async def get_session(request: web.Request) -> web.Response:
        session = store.get("session")
        if session is None or request.match_info["session_id"] != session["id"]:
            return web.json_response({"error": "not_found"}, status=404)
        polls = store.get("polls", 0) + 1
        store["polls"] = polls
        if polls >= 2:
            session = {
                **session,
                "status": "captured",
                "url": "app://x?code=abc",
                "query": {"code": "abc"},
            }
            store["session"] = session
        return web.json_response(session)

    async def delete_session(request: web.Request) -> web.Response:
        store.pop("session", None)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/v1/sessions", create)
    app.router.add_get("/v1/sessions/{session_id}", get_session)
    app.router.add_delete("/v1/sessions/{session_id}", delete_session)
    async with TestClient(TestServer(app)) as client:
        yield client, store


async def test_create_session_sends_optional_fields(api_client):
    http, store = api_client
    created = await async_create_session(
        http.session,
        str(http.make_url("/")).rstrip("/"),
        start_url="https://example.com/login",
        wait={"event": "http_redirect", "location_prefixes": ["app://x"]},
        client_id="demo",
        navigate_after={"url": "https://example.com/next", "cookies": ["A"]},
        success_message="Done",
    )
    assert created["id"] == "sess-1"
    payload = store["session"]["payload"]
    assert payload["navigate_after"]["url"] == "https://example.com/next"
    assert payload["success_message"] == "Done"


async def test_create_session_maps_api_error(api_client):
    http, _store = api_client
    with pytest.raises(CompanionError) as err:
        await async_create_session(
            http.session,
            str(http.make_url("/")).rstrip("/"),
            start_url="",
            wait={"event": "http_redirect", "location_prefixes": ["app://x"]},
        )
    assert err.value.code == "start_url_required"


async def test_wait_captured_polls_until_match(api_client):
    http, _store = api_client
    base = str(http.make_url("/")).rstrip("/")
    created = await async_create_session(
        http.session,
        base,
        start_url="https://example.com/login",
        wait={"event": "http_redirect", "location_prefixes": ["app://x"]},
    )
    captured = await async_wait_captured(
        http.session, base, created["id"], poll_interval=0.01, timeout_seconds=2
    )
    assert captured["status"] == "captured"
    assert captured["query"]["code"] == "abc"
    await async_delete_session(http.session, base, created["id"])
    with pytest.raises(CompanionError) as err:
        await async_get_session(http.session, base, created["id"])
    assert err.value.code == "not_found"
