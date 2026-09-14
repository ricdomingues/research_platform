"""In-memory AlertSink for integration tests. Never touches the network."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class RecordingSink:
    name = "recording"

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.failure: Exception | None = None
        self.failing_keys: set[str] | None = None  # None: `failure` applies to every alert

    def deliver(self, alert_key: str, document: Mapping[str, Any]) -> None:
        self.sent.append((alert_key, dict(document)))
        if self.failure is not None and (self.failing_keys is None or alert_key in self.failing_keys):
            raise self.failure
