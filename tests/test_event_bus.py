"""In-process event bus — the server→client change-notification substrate.

Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§4, §6, §7).

The bus is deliberately synchronous and built on queue.Queue (the same primitive
src/api/routes/logs.py uses) so that publish() is safe from any thread — route
handlers run on the event loop, a threadpool, or a detached worker thread — with
no loop marshalling. Async tests would be silently skipped here (no
pytest-asyncio in this environment), so a synchronous design is also the only
one that is actually covered by the suite.
"""

from __future__ import annotations

import threading

from src.events.bus import EventBus
from src.events.models import EventEnvelope, make_event, resolve_domain


# ── Envelope construction & domain resolution ───────────────────


class TestEnvelope:
    def test_make_event_fills_timestamp_and_empty_collections(self):
        ev = make_event("meeting.updated", scope={"meeting_id": "m1"})
        assert isinstance(ev, EventEnvelope)
        assert ev.type == "meeting.updated"
        assert ev.scope == {"meeting_id": "m1"}
        assert ev.payload == {}
        assert ev.origin == {}
        assert ev.ts > 0
        assert ev.seq == 0  # assigned by the bus on publish


class TestDomainResolution:
    def test_group_and_person_are_not_prefix_derived(self):
        # meeting_group.created must NOT resolve to "meeting_group"; it belongs to meetings.
        assert resolve_domain("meeting_group.created") == "meetings"
        assert resolve_domain("meeting_group.updated") == "meetings"
        # person.profile_started belongs to people, not "person".
        assert resolve_domain("person.profile_started") == "people"

    def test_session_message_is_distinct_from_file_mgmt_message(self):
        # These are different entities and must not collapse into one domain.
        assert resolve_domain("session.message_created") == "sessions"
        assert resolve_domain("message.created") == "files"

    def test_known_domains(self):
        assert resolve_domain("meeting.updated") == "meetings"
        assert resolve_domain("note.created") == "notes"
        assert resolve_domain("collection.deleted") == "collections"
        assert resolve_domain("hot_words.library_created") == "hot_words"
        assert resolve_domain("config.updated") == "config"
        assert resolve_domain("task.queued") == "tasks"
        assert resolve_domain("file.uploaded") == "files"
        assert resolve_domain("session.created") == "sessions"
        assert resolve_domain("chain.created") == "timeline"
        assert resolve_domain("node.updated") == "timeline"


# ── seq assignment, ring buffer, replay semantics ───────────────


class TestSeqAndReplay:
    def test_seq_starts_at_one_and_is_monotonic(self):
        bus = EventBus()
        assert bus.seq == 0
        a = bus.publish(make_event("file.uploaded"))
        b = bus.publish(make_event("file.updated"))
        assert (a.seq, b.seq) == (1, 2)
        assert bus.seq == 2

    def test_fresh_bus_can_replay_cursor_zero(self):
        bus = EventBus()
        assert bus.can_replay(0) is True
        assert bus.snapshot_since(0) == []

    def test_snapshot_since_returns_only_events_after_cursor(self):
        bus = EventBus()
        for _ in range(4):
            bus.publish(make_event("file.updated"))
        got = bus.snapshot_since(2)
        assert [e.seq for e in got] == [3, 4]

    def test_cursor_at_head_replays_nothing_without_gap(self):
        bus = EventBus()
        bus.publish(make_event("file.updated"))
        assert bus.can_replay(1) is True
        assert bus.snapshot_since(1) == []

    def test_evicted_events_are_a_gap_not_a_silent_short_replay(self):
        bus = EventBus(maxlen=3)
        for _ in range(5):
            bus.publish(make_event("file.updated"))  # seq 1..5, ring holds 3..5
        assert bus.oldest_seq == 3
        # A cursor at 1 cannot be served: seq 2 was evicted.
        assert bus.can_replay(1) is False
        # A cursor at 2 is exactly at the floor — seq 3.. are all still here.
        assert bus.can_replay(2) is True
        assert [e.seq for e in bus.snapshot_since(2)] == [3, 4, 5]

    def test_future_cursor_means_server_restarted_and_is_a_gap(self):
        bus = EventBus()
        bus.publish(make_event("file.updated"))  # seq 1; a restarted server is back at 1
        # Client last saw seq 99 before the restart, so replay is impossible.
        assert bus.can_replay(99) is False

    def test_oldest_seq_is_none_before_any_publish(self):
        assert EventBus().oldest_seq is None


# ── subscription fan-out ────────────────────────────────────────


class TestFanOut:
    def test_subscriber_receives_published_event(self):
        bus = EventBus()
        sub = bus.subscribe()
        published = bus.publish(make_event("meeting.updated", scope={"meeting_id": "m1"}))
        got = sub.queue.get(timeout=1)
        assert got.seq == published.seq
        assert got.type == "meeting.updated"

    def test_all_subscribers_receive_the_same_event(self):
        bus = EventBus()
        a, b = bus.subscribe(), bus.subscribe()
        bus.publish(make_event("note.created"))
        assert a.queue.get(timeout=1).type == "note.created"
        assert b.queue.get(timeout=1).type == "note.created"

    def test_publish_from_worker_thread_reaches_subscriber(self):
        # The load-bearing thread-safety case: collections routes are sync `def`,
        # task handlers and meeting generation run on detached threads.
        bus = EventBus()
        sub = bus.subscribe()
        done = threading.Event()
        published: list[EventEnvelope] = []

        def worker():
            published.append(bus.publish(make_event("collection.updated")))
            done.set()

        t = threading.Thread(target=worker)
        t.start()
        got = sub.queue.get(timeout=2)
        t.join(timeout=2)
        assert got.seq == published[0].seq
        assert got.type == "collection.updated"

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        sub = bus.subscribe()
        bus.unsubscribe(sub)
        bus.publish(make_event("file.updated"))
        assert sub.queue.empty()

    def test_overflow_marks_subscriber_without_blocking_publisher(self):
        # A slow peer must never stall the bus; it gets told to reconcile instead.
        bus = EventBus(queue_size=2)
        sub = bus.subscribe()
        for _ in range(5):
            bus.publish(make_event("file.updated"))
        assert sub.overflowed is True
        # Publisher kept going: all 5 got a seq even though only 2 fit.
        assert bus.seq == 5

    def test_overflow_recovers_on_drain(self):
        bus = EventBus(queue_size=2)
        sub = bus.subscribe()
        for _ in range(5):
            bus.publish(make_event("file.updated"))
        assert sub.overflowed is True
        while not sub.queue.empty():
            sub.queue.get_nowait()
        # Draining past the overflow clears the flag so live delivery resumes.
        assert sub.take_overflowed() is True
        assert sub.overflowed is False
