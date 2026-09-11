"""Session store without Chromium."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "browser_companion" / "rootfs" / "opt" / "companion"
sys.path.insert(0, str(ROOT))

from intercept import CaptureCandidate, extract_result, parse_wait  # noqa: E402
from session import SessionStore  # noqa: E402
from cdp import prompt_chose_close  # noqa: E402


def _rules():
    return parse_wait(
        {
            "wait": {
                "event": "http_redirect",
                "status_codes": [302],
                "location_prefixes": ["app://cma20.biedronka.pl"],
            }
        }
    )


def test_replace_and_capture():
    store = SessionStore()
    session = store.replace(
        client_id="biedronka",
        start_url="https://example/login",
        wait_rules=_rules(),
        timeout_seconds=600,
    )
    candidate = CaptureCandidate(
        "http_redirect",
        "app://cma20.biedronka.pl?code=abc",
        302,
    )
    assert store.capture(extract_result(candidate))
    public = store.get(session.id).to_public()
    assert public["status"] == "captured"
    assert public["query"]["code"] == "abc"
    assert public["event"] == "http_redirect"
    assert public["status_code"] == 302


def test_unknown_session():
    store = SessionStore()
    store.replace(
        client_id="x",
        start_url="https://example",
        wait_rules=_rules(),
        timeout_seconds=60,
    )
    assert store.get("nope") is None


def test_replace_drops_previous():
    store = SessionStore()
    first = store.replace(
        client_id="a",
        start_url="https://a",
        wait_rules=_rules(),
        timeout_seconds=60,
    )
    second = store.replace(
        client_id="b",
        start_url="https://b",
        wait_rules=_rules(),
        timeout_seconds=60,
    )
    assert store.get(first.id) is None
    assert store.get(second.id) is not None
    assert store.get(second.id).status == "pending"


def test_capture_includes_cookies():
    store = SessionStore()
    session = store.replace(
        client_id="allegro",
        start_url="https://allegro.pl",
        wait_rules=_rules(),
        timeout_seconds=60,
        navigate_after={
            "url": "https://allegro.pl/moje-allegro/zakupy/kupione",
            "cookies": ["QXLSESSID"],
        },
    )
    assert session.navigate_after["cookies"] == ["QXLSESSID"]
    store.capture(
        {
            "url": "https://allegro.pl/moje-allegro/zakupy/kupione",
            "query": {},
            "event": "navigation",
            "cookies": {"QXLSESSID": "tok"},
        }
    )
    public = store.get(session.id).to_public()
    assert public["cookies"]["QXLSESSID"] == "tok"


def test_success_message_stored():
    store = SessionStore()
    session = store.replace(
        client_id="allegro",
        start_url="https://allegro.pl",
        wait_rules=_rules(),
        timeout_seconds=60,
        success_message="Signed in. Click OK to close.",
    )
    assert session.success_message == "Signed in. Click OK to close."


def test_prompt_chose_close_keep_open():
    assert prompt_chose_close({"result": {"result": {"type": "boolean", "value": False}}}) is False


def test_prompt_chose_close_defaults_to_close():
    assert prompt_chose_close({"result": {"result": {"type": "boolean", "value": True}}}) is True
    assert prompt_chose_close({}) is True
    assert prompt_chose_close(None) is True
