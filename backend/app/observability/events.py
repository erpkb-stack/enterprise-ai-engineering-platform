"""In-process event bus used to stream execution events over WebSockets.

Bounded per-subscriber queues: slow consumers drop the oldest events rather than
blocking producers (backpressure isolation between agent execution and UI streaming).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


class EventBus:
    def __init__(self, queue_size: int = 500) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._queue_size = queue_size
        self.dropped = 0

    def subscribe(self, org_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs[org_id].add(q)
        return q

    def unsubscribe(self, org_id: str, q: asyncio.Queue) -> None:
        self._subs[org_id].discard(q)

    def publish(self, org_id: str, topic: str, data: dict[str, Any]) -> None:
        event = {"topic": topic, "ts": time.time(), "data": data}
        for q in list(self._subs.get(org_id, ())):
            if q.full():
                try:
                    q.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped += 1

    @property
    def subscriber_count(self) -> int:
        return sum(len(s) for s in self._subs.values())


bus = EventBus()


def encode(event: dict) -> str:
    return json.dumps(event, default=str)
