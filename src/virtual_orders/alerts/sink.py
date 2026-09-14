"""Provider-neutral alert delivery contract (D24, D30). n8n is never in the critical path (spec 1.2 item 7)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class AlertDeliveryFailed(Exception):
    """One delivery attempt failed. Carries only a status code and an exception type, never provider text."""

    def __init__(self, *, status_code: int | None, error_type: str) -> None:
        super().__init__(error_type if status_code is None else f"HTTP {status_code}")
        self.status_code = status_code
        self.error_type = error_type


class AlertSink(Protocol):
    name: str

    def deliver(self, alert_key: str, document: Mapping[str, Any]) -> None:
        """Sends one JSON-native document once; raises AlertDeliveryFailed. Never retries."""
        ...
