"""Wait-rule matching without Chromium."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "browser_companion" / "rootfs" / "opt" / "companion"
sys.path.insert(0, str(ROOT))

from intercept import (  # noqa: E402
    CaptureCandidate,
    WaitSpecError,
    extract_result,
    location_from_headers,
    matches_wait,
    parse_wait,
    protocol_schemes,
)


def _biedronka_wait() -> list:
    return parse_wait(
        {
            "wait": {
                "event": "http_redirect",
                "status_codes": [302],
                "location_prefixes": ["app://cma20.biedronka.pl"],
            }
        }
    )


def test_parse_wait_object_or_list():
    rule = {
        "event": "navigation",
        "url_prefixes": ["https://example/callback"],
    }
    assert len(parse_wait({"wait": rule})) == 1
    assert len(parse_wait({"wait": [rule]})) == 1


def test_wait_required():
    with pytest.raises(WaitSpecError, match="wait_required"):
        parse_wait({})
    with pytest.raises(WaitSpecError, match="wait_event_unknown"):
        parse_wait({"wait": {"event": "cookies"}})


def test_http_redirect_requires_location():
    with pytest.raises(WaitSpecError, match="wait_location_required"):
        parse_wait({"wait": {"event": "http_redirect", "status_codes": [302]}})


def test_biedronka_302_app_location():
    rules = _biedronka_wait()
    hit = CaptureCandidate(
        "http_redirect",
        "app://cma20.biedronka.pl?code=abc",
        302,
    )
    assert matches_wait(hit, rules)
    miss_status = CaptureCandidate(
        "http_redirect",
        "app://cma20.biedronka.pl?code=abc",
        301,
    )
    assert not matches_wait(miss_status, rules)
    miss_nav = CaptureCandidate(
        "navigation",
        "app://cma20.biedronka.pl?code=abc",
    )
    assert not matches_wait(miss_nav, rules)
    miss_https = CaptureCandidate(
        "http_redirect",
        "https://konto.biedronka.pl/login",
        302,
    )
    assert not matches_wait(miss_https, rules)


def test_should_abort_fetch():
    from intercept import is_http_url, should_abort_fetch

    assert is_http_url("https://allegro.pl/moje-allegro/zakupy/kupione")
    assert not should_abort_fetch(
        "navigation", "https://allegro.pl/moje-allegro/zakupy/kupione"
    )
    assert should_abort_fetch("protocol_handler", "app://cma20.biedronka.pl?code=1")
    assert should_abort_fetch("http_redirect", "app://cma20.biedronka.pl?code=1")
    assert not should_abort_fetch("http_redirect", "https://allegro.pl/")


def test_navigation_wait_with_cookies():
    rules = parse_wait(
        {
            "wait": {
                "event": "navigation",
                "url_prefixes": ["https://allegro.pl/moje-allegro/zakupy/kupione"],
                "cookies": ["QXLSESSID"],
            }
        }
    )
    assert rules[0].cookies == ("QXLSESSID",)
    hit = CaptureCandidate(
        "navigation",
        "https://allegro.pl/moje-allegro/zakupy/kupione",
    )
    assert matches_wait(hit, rules)


def test_extract_cookies():
    result = extract_result(
        CaptureCandidate(
            "navigation",
            "https://allegro.pl/moje-allegro/zakupy/kupione",
            cookies={"QXLSESSID": "abc.def"},
        )
    )
    assert result["cookies"]["QXLSESSID"] == "abc.def"


def test_cookies_from_cdp():
    from intercept import cookies_complete, cookies_from_cdp

    found = cookies_from_cdp(
        [
            {"name": "other", "value": "x"},
            {"name": "QXLSESSID", "value": "sess"},
            {"name": "__Secure-QXLSESSID", "value": "secure"},
        ],
        ("QXLSESSID",),
    )
    assert found == {"QXLSESSID": "sess"}
    found_secure = cookies_from_cdp(
        [{"name": "__Secure-QXLSESSID", "value": "secure"}],
        ("QXLSESSID",),
    )
    assert found_secure == {"QXLSESSID": "secure"}
    assert cookies_complete(found, ("QXLSESSID",))
    assert not cookies_complete({}, ("QXLSESSID",))


def test_parse_navigate_after():
    from intercept import parse_navigate_after

    spec = parse_navigate_after(
        {
            "navigate_after": {
                "url": "https://allegro.pl/moje-allegro/zakupy/kupione",
                "cookies": ["QXLSESSID"],
            }
        }
    )
    assert spec["url"].endswith("/kupione")
    assert spec["cookies"] == ["QXLSESSID"]
    assert parse_navigate_after({}) is None


def test_navigation_wait():
    rules = parse_wait(
        {
            "wait": {
                "event": "navigation",
                "url_prefixes": ["https://example/oauth/callback"],
            }
        }
    )
    assert matches_wait(
        CaptureCandidate("navigation", "https://example/oauth/callback?code=1"),
        rules,
    )
    assert not matches_wait(
        CaptureCandidate(
            "http_redirect",
            "https://example/oauth/callback?code=1",
            302,
        ),
        rules,
    )


def test_protocol_handler_wait():
    rules = parse_wait({"wait": {"event": "protocol_handler", "schemes": ["myapp"]}})
    assert protocol_schemes(rules) == ["myapp"]
    assert matches_wait(CaptureCandidate("protocol_handler", "myapp://x?a=1"), rules)
    assert not matches_wait(CaptureCandidate("navigation", "myapp://x?a=1"), rules)


def test_extract_query_from_candidate():
    result = extract_result(
        CaptureCandidate(
            "http_redirect",
            "app://cma20.biedronka.pl?code=abc.def&session_state=1",
            302,
        )
    )
    assert result["query"]["code"] == "abc.def"
    assert result["event"] == "http_redirect"
    assert result["status_code"] == 302


def test_extract_fragment():
    result = extract_result(
        CaptureCandidate("navigation", "https://example/cb#access_token=tok&token_type=bearer")
    )
    assert result["query"]["access_token"] == "tok"


def test_location_from_cdp_headers():
    headers = [
        {"name": "Content-Type", "value": "text/html"},
        {"name": "Location", "value": "app://cma20.biedronka.pl?code=z"},
    ]
    assert location_from_headers(headers) == "app://cma20.biedronka.pl?code=z"


def test_http_redirect_default_status_codes():
    rules = parse_wait(
        {
            "wait": {
                "event": "http_redirect",
                "location_prefixes": ["app://x"],
            }
        }
    )
    assert matches_wait(CaptureCandidate("http_redirect", "app://x", 303), rules)

