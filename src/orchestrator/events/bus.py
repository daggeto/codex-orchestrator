"""Async event bus for inter-agent and orchestrator communication."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

EventHandler = Callable[["Event"], Awaitable[None]]


@dataclass
class Event:
    topic: str
    payload: dict[str, Any]
    source: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class Subscription:
    id: str
    pattern: str
    handler: EventHandler
    _active: bool = True

    def cancel(self):
        self._active = False


class EventBus:
    def __init__(self):
        self._subscriptions: dict[str, Subscription] = {}
        self._history: list[Event] = []
        self._max_history = 10000
        self._lock = asyncio.Lock()

    async def publish(self, topic: str, payload: dict[str, Any], source: str = "") -> Event:
        event = Event(topic=topic, payload=payload, source=source)
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        handlers: list[tuple[str, EventHandler]] = []
        async with self._lock:
            for sub in self._subscriptions.values():
                if sub._active and fnmatch.fnmatch(topic, sub.pattern):
                    handlers.append((sub.id, sub.handler))

        for sub_id, handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("Event handler %s failed for topic %s", sub_id, topic)

        return event

    async def subscribe(self, pattern: str, handler: EventHandler) -> Subscription:
        sub = Subscription(
            id=uuid.uuid4().hex[:12],
            pattern=pattern,
            handler=handler,
        )
        async with self._lock:
            self._subscriptions[sub.id] = sub
        return sub

    async def unsubscribe(self, subscription: Subscription):
        subscription.cancel()
        async with self._lock:
            self._subscriptions.pop(subscription.id, None)

    def recent(self, topic_pattern: str = "*", limit: int = 50) -> list[Event]:
        return [
            e
            for e in reversed(self._history)
            if fnmatch.fnmatch(e.topic, topic_pattern)
        ][:limit]

    async def wait_for(
        self, topic_pattern: str, timeout: float = 60.0, predicate: Callable[[Event], bool] | None = None
    ) -> Event:
        future: asyncio.Future[Event] = asyncio.get_event_loop().create_future()

        async def _handler(event: Event):
            if future.done():
                return
            if predicate and not predicate(event):
                return
            future.set_result(event)

        sub = await self.subscribe(topic_pattern, _handler)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            await self.unsubscribe(sub)
