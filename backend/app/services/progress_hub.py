"""In-process pub/sub so the browser-facing WebSocket can stream live job
progress that the background processor publishes.

Single-process only. For multiple backend workers, swap this for Redis pub/sub.
"""
import asyncio
from collections import defaultdict

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)


def subscribe(job_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers[job_id].add(q)
    return q


def unsubscribe(job_id: str, q: asyncio.Queue) -> None:
    subs = _subscribers.get(job_id)
    if subs:
        subs.discard(q)
        if not subs:
            _subscribers.pop(job_id, None)


def publish(job_id: str, payload: dict) -> None:
    for q in list(_subscribers.get(job_id, ())):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass
