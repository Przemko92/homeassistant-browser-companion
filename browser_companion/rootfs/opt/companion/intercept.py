"""Wait rules declared by integrations: what to capture from Chromium."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

EVENTS = frozenset({"http_redirect", "navigation", "protocol_handler"})
DEFAULT_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


@dataclass(frozen=True)
class CaptureCandidate:
    event: str
    url: str
    status_code: int | None = None


@dataclass(frozen=True)
class WaitRule:
    event: str
    status_codes: tuple[int, ...]
    prefixes: tuple[str, ...]
    schemes: tuple[str, ...]

    def schemes_to_register(self) -> tuple[str, ...]:
        if self.event != "protocol_handler":
            return ()
        return self.schemes


class WaitSpecError(ValueError):
    """Invalid wait payload from an integration."""


def _as_str_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        item = value.strip()
        return (item,) if item else ()
    if not isinstance(value, list):
        raise WaitSpecError("wait_invalid")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _as_int_list(value: object, default: tuple[int, ...]) -> tuple[int, ...]:
    if value is None:
        return default
    if isinstance(value, int):
        return (value,)
    if not isinstance(value, list) or not value:
        raise WaitSpecError("wait_status_codes_invalid")
    codes: list[int] = []
    for item in value:
        try:
            codes.append(int(item))
        except (TypeError, ValueError) as err:
            raise WaitSpecError("wait_status_codes_invalid") from err
    return tuple(codes)


def parse_wait(body: dict) -> list[WaitRule]:
    """Parse `wait` (object or list). Integrations must say what they expect."""
    raw = body.get("wait")
    if isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        raise WaitSpecError("wait_required")
    if not items:
        raise WaitSpecError("wait_required")
    rules = [_parse_rule(item) for item in items]
    if not rules:
        raise WaitSpecError("wait_required")
    return rules


def _parse_rule(item: object) -> WaitRule:
    if not isinstance(item, dict):
        raise WaitSpecError("wait_invalid")
    event = str(item.get("event") or "").strip()
    if event not in EVENTS:
        raise WaitSpecError("wait_event_unknown")
    if event == "http_redirect":
        prefixes = _as_str_list(item.get("location_prefixes"))
        schemes = _as_str_list(item.get("location_schemes"))
        statuses = _as_int_list(item.get("status_codes"), DEFAULT_REDIRECT_STATUSES)
        if not prefixes and not schemes:
            raise WaitSpecError("wait_location_required")
        return WaitRule(event, statuses, prefixes, schemes)
    if event == "navigation":
        prefixes = _as_str_list(item.get("url_prefixes") or item.get("prefixes"))
        schemes = _as_str_list(item.get("url_schemes") or item.get("schemes"))
        if not prefixes and not schemes:
            raise WaitSpecError("wait_url_required")
        return WaitRule(event, (), prefixes, schemes)
    schemes = _as_str_list(item.get("schemes"))
    prefixes = _as_str_list(item.get("url_prefixes"))
    if not schemes and not prefixes:
        raise WaitSpecError("wait_scheme_required")
    return WaitRule(event, (), prefixes, schemes)


def url_matches(url: str, prefixes: tuple[str, ...], schemes: tuple[str, ...]) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    for prefix in prefixes:
        if prefix and raw.startswith(prefix):
            return True
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    for item in schemes:
        if item and scheme == item.lower():
            return True
    return False


def matches_wait(candidate: CaptureCandidate, rules: list[WaitRule]) -> bool:
    raw = (candidate.url or "").strip()
    if not raw or candidate.event not in EVENTS:
        return False
    for rule in rules:
        if candidate.event != rule.event:
            continue
        if rule.event == "http_redirect":
            if candidate.status_code not in rule.status_codes:
                continue
            if url_matches(raw, rule.prefixes, rule.schemes):
                return True
            continue
        if url_matches(raw, rule.prefixes, rule.schemes):
            return True
    return False


def extract_result(candidate: CaptureCandidate) -> dict:
    """Return captured payload: url, query, event, optional status_code."""
    raw = (candidate.url or "").strip()
    parsed = urlparse(raw)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not query and parsed.fragment:
        query = parse_qs(parsed.fragment, keep_blank_values=True)
    flat: dict[str, str | list[str]] = {}
    for key, values in query.items():
        if len(values) == 1:
            flat[key] = values[0]
        else:
            flat[key] = values
    payload: dict = {"url": raw, "query": flat, "event": candidate.event}
    if candidate.status_code is not None:
        payload["status_code"] = candidate.status_code
    return payload


def location_from_headers(headers: list[dict] | None) -> str | None:
    """CDP Fetch/Network header list -> Location value."""
    for header in headers or []:
        name = str(header.get("name") or "")
        if name.lower() == "location":
            value = header.get("value")
            if value:
                return str(value)
    return None


def protocol_schemes(rules: list[WaitRule]) -> list[str]:
    found: list[str] = []
    for rule in rules:
        for scheme in rule.schemes_to_register():
            if scheme not in found:
                found.append(scheme)
    return found
