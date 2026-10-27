"""In-process event bus: monotonic seq, ring buffer, subscriber fan-out.

Synchronous by construction and built on queue.Queue, so publish() is safe from
any thread — event-loop route handlers, Starlette's sync-route threadpool, and
detached worker threads alike — with no loop marshalling. This mirrors the
broadcast mechanism src/api/routes/logs.py already uses in production.

Single-process only: the bus holds subscriptions in memory, so it assumes the
one-uvicorn-process deployment (src/main.py binds both entries in one process).
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections import deque
from dataclasses import dataclass, field

from src.events.models import EventEnvelope

DEFAULT_RING_SIZE = 2000
DEFAULT_QUEUE_SIZE = 500


def _offer_async(sub: "Subscription", event: EventEnvelope) -> None:
    """Enqueue onto a subscriber's asyncio queue, on that subscriber's loop.

    Runs via `call_soon_threadsafe`, so it is single-threaded with respect to the
    loop. Overflow is recorded rather than raised: the reader converts it into a
    `resync` frame.
    """
    aqueue = sub.aqueue
    if aqueue is None:
        return
    try:
        aqueue.put_nowait(event)
    except asyncio.QueueFull:
        sub.mark_overflow()


@dataclass
class Subscription:
    """One connected client's delivery channel.

    Two queues by design, and exactly one of them is used:

    - `queue` (sync) carries events for synchronous consumers — tests and any
      non-async caller. Used when the subscription has no loop.
    - `aqueue` (asyncio) carries events for a real SSE connection. The bus wakes
      it with `loop.call_soon_threadsafe`, which lets the SSE generator *await*
      instead of blocking a worker thread while idle. Before this, one open
      stream held an anyio threadpool token for the whole keepalive interval and
      ~40 streams starved every sync route in the app.

    Filling BOTH would be a bug in the other direction: the async reader never
    drains the sync queue, so it would silently fill to maxsize and then mark a
    perfectly healthy client as overflowed (a spurious full-client resync every
    ~500 events). Hence the either/or.

    `overflowed` records that events were dropped because the client could not
    keep up. The publisher never blocks on a slow client; the SSE endpoint reads
    the flag and tells that client to reconcile instead (resync {overflow}).
    """

    queue: "queue.Queue[EventEnvelope]"
    aqueue: "asyncio.Queue[EventEnvelope] | None" = None
    loop: "asyncio.AbstractEventLoop | None" = None
    _overflowed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def overflowed(self) -> bool:
        return self._overflowed

    @property
    def is_async(self) -> bool:
        """True when delivery goes through the asyncio queue."""
        return self.aqueue is not None

    def mark_overflow(self) -> None:
        with self._lock:
            self._overflowed = True

    def take_overflowed(self) -> bool:
        """Read-and-clear the overflow flag (called when the client drains)."""
        with self._lock:
            was = self._overflowed
            self._overflowed = False
            return was

    async def aget(self, timeout: float) -> EventEnvelope:
        """Await the next event, or raise asyncio.TimeoutError after `timeout`.

        Falls back to a polling read for a subscription with no async queue, so
        a caller that forgot `loop=` still works.
        """
        if self.aqueue is not None:
            return await asyncio.wait_for(self.aqueue.get(), timeout=timeout)
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            raise asyncio.TimeoutError() from None


class EventBus:
    def __init__(
        self,
        *,
        maxlen: int = DEFAULT_RING_SIZE,
        queue_size: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        self._ring: deque[EventEnvelope] = deque(maxlen=maxlen)
        self._queue_size = queue_size
        self._seq = 0
        self._subs: list[Subscription] = []
        self._lock = threading.Lock()

    @property
    def seq(self) -> int:
        """Head cursor: seq of the most recently published event (0 if none)."""
        return self._seq

    @property
    def oldest_seq(self) -> int | None:
        """Seq of the oldest event still replayable, or None if the ring is empty."""
        with self._lock:
            return self._ring[0].seq if self._ring else None

    def subscribe(self, loop: "asyncio.AbstractEventLoop | None" = None) -> Subscription:
        """Register a subscriber.

        Pass the running event loop for an SSE connection: the bus then also
        delivers into an asyncio queue and wakes it with
        `loop.call_soon_threadsafe`, so the reader can await without occupying a
        worker thread. Omit it for tests and synchronous consumers.
        """
        sub = Subscription(
            queue=queue.Queue(maxsize=self._queue_size),
            aqueue=asyncio.Queue(maxsize=self._queue_size) if loop is not None else None,
            loop=loop,
        )
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def publish(self, event: EventEnvelope) -> EventEnvelope:
        """Assign the next seq, retain in the ring, fan out to every subscriber."""
        with self._lock:
            self._seq += 1
            stamped = event.model_copy(update={"seq": self._seq})
            self._ring.append(stamped)
            subs = list(self._subs)
        # Fan out outside the bus lock: a full queue must not stall the publisher
        # or hold up other subscribers.
        for sub in subs:
            if sub.is_async and sub.loop is not None:
                try:
                    # Thread-safe wakeup: publish() is called from the loop, from
                    # Starlette's sync-route threadpool, and from detached worker
                    # threads alike.
                    sub.loop.call_soon_threadsafe(_offer_async, sub, stamped)
                except RuntimeError:
                    # Loop already closed (client gone mid-teardown); the
                    # subscriber will be unsubscribed by the SSE endpoint.
                    pass
                continue
            # Synchronous consumer: fill the blocking queue only. Filling both
            # would let the unused queue grow to maxsize and then falsely mark a
            # healthy client as overflowed.
            try:
                sub.queue.put_nowait(stamped)
            except queue.Full:
                sub.mark_overflow()
        return stamped

    def snapshot_since(self, since: int) -> list[EventEnvelope]:
        """Events strictly after `since`, oldest first."""
        with self._lock:
            return [e for e in self._ring if e.seq > since]

    def can_replay(self, since: int) -> bool:
        """Whether a client at cursor `since` can be caught up by replay alone.

        False means reconcile fully instead: either the cursor is in the future
        (the server restarted and seq reset) or the events after it were evicted.
        """
        with self._lock:
            if since > self._seq:
                return False
            if not self._ring:
                return since == self._seq
            return since >= self._ring[0].seq - 1


# Process-wide bus shared by every emit site and every SSE subscriber.
bus = EventBus()
