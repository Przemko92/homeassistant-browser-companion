"""In-memory login session (one at a time)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from intercept import WaitRule


@dataclass
class Session:
    id: str
    client_id: str
    start_url: str
    wait_rules: list[WaitRule]
    timeout_seconds: int
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | captured | expired | error
    result: dict[str, Any] | None = None
    error: str | None = None
    navigate_after: dict[str, Any] | None = None
    success_message: str | None = None

    def is_timed_out(self) -> bool:
        return time.time() - self.created_at >= self.timeout_seconds

    def to_public(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "client_id": self.client_id,
        }
        if self.status == "captured" and self.result:
            payload["url"] = self.result.get("url")
            payload["query"] = self.result.get("query") or {}
            payload["event"] = self.result.get("event")
            if self.result.get("status_code") is not None:
                payload["status_code"] = self.result.get("status_code")
            if self.result.get("cookies"):
                payload["cookies"] = self.result.get("cookies")
        if self.status == "error" and self.error:
            payload["error"] = self.error
        return payload


class SessionStore:
    def __init__(self) -> None:
        self._current: Session | None = None

    @property
    def current(self) -> Session | None:
        session = self._current
        if session and session.status == "pending" and session.is_timed_out():
            session.status = "expired"
        return self._current

    def replace(
        self,
        *,
        client_id: str,
        start_url: str,
        wait_rules: list[WaitRule],
        timeout_seconds: int,
        navigate_after: dict[str, Any] | None = None,
        success_message: str | None = None,
    ) -> Session:
        session = Session(
            id=str(uuid.uuid4()),
            client_id=client_id,
            start_url=start_url,
            wait_rules=wait_rules,
            timeout_seconds=timeout_seconds,
            navigate_after=navigate_after,
            success_message=success_message,
        )
        self._current = session
        return session

    def get(self, session_id: str) -> Session | None:
        session = self.current
        if session is None or session.id != session_id:
            return None
        return session

    def capture(self, result: dict[str, Any]) -> bool:
        session = self.current
        if session is None or session.status != "pending":
            return False
        session.status = "captured"
        session.result = result
        return True

    def fail(self, message: str) -> None:
        session = self.current
        if session is None or session.status != "pending":
            return
        session.status = "error"
        session.error = message

    def clear(self) -> None:
        self._current = None
