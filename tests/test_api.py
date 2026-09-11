"""HTTP session API without Chromium."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1] / "browser_companion" / "rootfs" / "opt" / "companion"
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

BIE_WAIT = {
    "event": "http_redirect",
    "status_codes": [302],
    "location_prefixes": ["app://cma20.biedronka.pl"],
}


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "_start_browser", AsyncMock())
    monkeypatch.setattr(server.cdp, "stop", AsyncMock())
    server.store.clear()
    async with TestClient(TestServer(server.build_app())) as client:
        yield client
    server.store.clear()


async def test_health_empty(client: TestClient):
    resp = await client.get("/v1/health")
    assert resp.status == 200
    body = await resp.json()
    assert body == {"ok": True, "session": None}


async def test_create_session_returns_pending(client: TestClient):
    resp = await client.post(
        "/v1/sessions",
        json={
            "client_id": "biedronka",
            "start_url": "https://example.com/login",
            "wait": BIE_WAIT,
        },
    )
    assert resp.status == 201
    body = await resp.json()
    assert body["status"] == "pending"
    assert body["client_id"] == "biedronka"
    assert body["id"]


async def test_create_session_missing_start_url(client: TestClient):
    resp = await client.post("/v1/sessions", json={"wait": BIE_WAIT})
    assert resp.status == 400
    assert await resp.json() == {"error": "start_url_required"}


async def test_create_session_invalid_wait(client: TestClient):
    resp = await client.post(
        "/v1/sessions",
        json={"start_url": "https://example.com", "wait": {"event": "nope"}},
    )
    assert resp.status == 400
    assert await resp.json() == {"error": "wait_event_unknown"}


async def test_create_session_invalid_json(client: TestClient):
    resp = await client.post(
        "/v1/sessions",
        data="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    assert await resp.json() == {"error": "invalid_json"}


async def test_get_and_delete_session(client: TestClient):
    created = await client.post(
        "/v1/sessions",
        json={"start_url": "https://example.com/login", "wait": BIE_WAIT},
    )
    session_id = (await created.json())["id"]

    got = await client.get(f"/v1/sessions/{session_id}")
    assert got.status == 200
    assert (await got.json())["status"] == "pending"

    deleted = await client.delete(f"/v1/sessions/{session_id}")
    assert deleted.status == 200
    assert await deleted.json() == {"ok": True}

    missing = await client.get(f"/v1/sessions/{session_id}")
    assert missing.status == 404
    assert await missing.json() == {"error": "not_found"}


async def test_get_unknown_session(client: TestClient):
    resp = await client.get("/v1/sessions/does-not-exist")
    assert resp.status == 404
    assert await resp.json() == {"error": "not_found"}


async def test_delete_unknown_session(client: TestClient):
    resp = await client.delete("/v1/sessions/does-not-exist")
    assert resp.status == 404
    assert await resp.json() == {"error": "not_found"}


async def test_health_includes_current_session(client: TestClient):
    created = await client.post(
        "/v1/sessions",
        json={
            "client_id": "allegro",
            "start_url": "https://allegro.pl",
            "wait": {
                "event": "navigation",
                "url_prefixes": ["https://allegro.pl/done"],
            },
        },
    )
    session = await created.json()
    health = await (await client.get("/v1/health")).json()
    assert health["ok"] is True
    assert health["session"]["id"] == session["id"]
    assert health["session"]["client_id"] == "allegro"
