"""Pre-meeting brief: context assembly, synthesis, degradation, routes."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest


@pytest.fixture
def speakers_dir(tmp_path):
    root = tmp_path / "speakers"
    root.mkdir()
    with patch("src.speakers.store.SPEAKERS_DIR", root):
        yield root


@pytest.fixture
def meetings_dir(tmp_path):
    root = tmp_path / "meetings"
    root.mkdir()
    with patch("src.meeting.store.MEETINGS_DIR", root):
        yield root


@pytest.fixture
def groups_dir(tmp_path):
    root = tmp_path / "meeting_groups"
    root.mkdir()
    with patch("src.meeting.group_store.GROUPS_DIR", root):
        yield root


class _FakeLLM:
    """Dispatches by markers: 'pattern evidence' -> obs JSON,
    'working person profile' -> profile text, otherwise -> brief markdown."""

    def __init__(self, obs=None, profile_text="PROFILE(x)", brief_md="BRIEF_MD"):
        self.obs = obs if obs is not None else ["direct; pushes on cost"]
        self.profile_text = profile_text
        self.brief_md = brief_md
        self.calls: list[str] = []

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        low = prompt.lower()
        if "pattern evidence" in low:
            return json.dumps({"spk0": self.obs}, ensure_ascii=False)
        if "working person profile" in low:
            return self.profile_text
        return self.brief_md


def _seed_meeting(title, segs_by_spk=None, bindings=None):
    from src.meeting.models import TranscriptSegment, TranscriptionResult
    from src.meeting.store import create_meeting, save_transcript, update_meeting

    meeting = create_meeting(title)
    segments = []
    for spk, texts in (segs_by_spk or {}).items():
        for i, text in enumerate(texts):
            segments.append(
                TranscriptSegment(start=i * 10.0, end=i * 10.0 + 5.0, text=text, speaker_id=spk)
            )
    if segments:
        save_transcript(
            meeting.id,
            TranscriptionResult(text=" ".join(s.text for s in segments), segments=segments),
        )
    if bindings:
        update_meeting(meeting.id, speaker_people=dict(bindings))
    return meeting.id


def _seed_group(title, meeting_ids):
    from src.meeting.group_store import add_member, create_group

    group = create_group(title=title, meeting_id=meeting_ids[0])
    for mid in meeting_ids[1:]:
        add_member(group.id, mid)
    return group


def _todo_row(title, source_meeting_id, created_at, assignee=None):
    return {
        "title": title,
        "source_meeting_id": source_meeting_id,
        "created_at": created_at,
        "assignee_person_id": assignee,
    }


class TestBuildContext:
    def test_group_last_meeting_excludes_self(self, speakers_dir, meetings_dir, groups_dir):
        from src.meeting.prepare import build_brief_context
        from src.meeting.store import create_meeting, update_meeting

        m1 = _seed_meeting("Episode 1")
        m2 = _seed_meeting("Episode 2")
        self_meeting = create_meeting("This meeting")
        update_meeting(self_meeting.id, expected_people=["p_x"])
        _seed_group("Series", [m1, m2, self_meeting.id])
        # Make m2 the latest and give it a summary.
        from src.meeting.store import get_meeting

        update_meeting(m2, summary="settled plan A; deferred plan B")

        ctx = build_brief_context(self_meeting.id)
        assert ctx["group"]["title"] == "Series"
        assert ctx["last_meeting"]["id"] == m2
        assert ctx["last_meeting"]["title"] == "Episode 2"
        assert "settled plan A" in ctx["last_meeting"]["summary_clip"]

    def test_non_group_no_recap_no_todos(self, speakers_dir, meetings_dir, groups_dir):
        from src.meeting.prepare import build_brief_context
        from src.speakers.store import create_person

        person = create_person("Zhang")
        mid = _seed_meeting("Standalone", None, None)
        from src.meeting.store import update_meeting

        update_meeting(mid, expected_people=[person.id])

        ctx = build_brief_context(mid)
        assert ctx["group"] is None
        assert ctx["last_meeting"] is None
        assert ctx["open_todos"] == []

    def test_todos_group_scoped_oldest_first_cap5(self, speakers_dir, meetings_dir, groups_dir):
        from src.meeting.prepare import build_brief_context

        m1 = _seed_meeting("Episode 1")
        m2 = _seed_meeting("Episode 2")
        self_id = _seed_meeting("This meeting")
        from src.meeting.store import update_meeting

        update_meeting(self_id, expected_people=["p_x"])
        _seed_group("Series", [m1, m2, self_id])

        outside = _seed_meeting("Outside meeting")
        rows = [
            _todo_row("newer todo", m1, "2026-08-20T00:00:00"),
            _todo_row("oldest todo", m2, "2026-06-01T00:00:00"),
            _todo_row("outside todo", outside, "2026-05-01T00:00:00"),
        ] + [
            _todo_row(f"filler {i}", m1, f"2026-07-0{i + 1}T00:00:00") for i in range(5)
        ]
        with patch(
            "src.meeting.prepare._open_todos_for_meetings",
            lambda ids: [r for r in rows if r["source_meeting_id"] in ids],
        ):
            ctx = build_brief_context(self_id)
        titles = [t["title"] for t in ctx["open_todos"]]
        assert "outside todo" not in titles
        assert len(titles) == 5
        assert titles[0] == "oldest todo"  # stale first
        assert "newer todo" not in titles  # stale-first cap drops the newest
        assert ctx["open_todos"][0]["age_days"] >= 50

    def test_person_block_prefers_group_co_meeting(
        self, speakers_dir, meetings_dir, groups_dir
    ):
        from src.meeting.prepare import build_brief_context
        from src.speakers.store import create_person

        person = create_person("Zhang")
        group_m = _seed_meeting("Group old", {"spk0": ["hello"]}, {"spk0": person.id})
        standalone_new = _seed_meeting("Standalone new", {"spk0": ["hi"]}, {"spk0": person.id})
        self_id = _seed_meeting("This meeting")
        from src.meeting.store import update_meeting

        update_meeting(self_id, expected_people=[person.id])
        _seed_group("Series", [group_m, self_id])

        ctx = build_brief_context(self_id)
        assert len(ctx["persons"]) == 1
        block = ctx["persons"][0]
        assert block["person_id"] == person.id
        assert block["last_together"]["title"] == "Group old"
        assert standalone_new != block["last_together"]["title"]

    def test_agenda_from_title_and_notes(self, speakers_dir, meetings_dir, groups_dir):
        from src.meeting.prepare import build_brief_context
        from src.meeting.store import save_notes, update_meeting

        mid = _seed_meeting("Phase 2 planning")
        update_meeting(mid, expected_people=["p_x"])
        save_notes(mid, "convince them to double the budget")

        ctx = build_brief_context(mid)
        assert ctx["agenda"]["title"] == "Phase 2 planning"
        assert "budget" in ctx["agenda"]["text"]


class TestGenerateBrief:
    def test_generates_saves_and_records(self, speakers_dir, meetings_dir, groups_dir):
        from src.meeting.prepare import generate_brief
        from src.meeting.store import get_meeting, save_notes
        from src.speakers.store import create_person

        person = create_person("Zhang")
        m1 = _seed_meeting("Episode 1", {"spk0": ["hello world"]}, {"spk0": person.id})
        from src.meeting.store import update_meeting

        self_id = _seed_meeting("This meeting")
        update_meeting(self_id, expected_people=[person.id])
        save_notes(self_id, "finalize API ownership")
        _seed_group("Series", [m1, self_id])
        update_meeting(m1, summary="settled plan A; API ownership unresolved")

        fake = _FakeLLM()
        brief = generate_brief(self_id, llm=fake)
        assert brief["state"] == "ready"
        assert brief["markdown"] == "BRIEF_MD"
        assert brief["person_ids"] == [person.id]
        # Dirty profile was refreshed through the same LLM before synthesis.
        assert len([c for c in fake.calls if "pattern evidence" in c.lower()]) == 1
        assert len([c for c in fake.calls if "working person profile" in c.lower()]) == 1
        # Exactly one synthesis call, containing the recap and person blocks.
        synthesis = [c for c in fake.calls if "pre-meeting brief" in c.lower()]
        assert len(synthesis) == 1
        prompt = synthesis[0]
        assert "settled plan A" in prompt
        assert "Zhang" in prompt
        # The agenda is the organizing lens of the brief.
        assert "Agenda" in prompt
        # Section tokens are a UI contract — the frontend parses these H2s.
        assert "## Recap" in prompt
        assert "### Name" in prompt
        # Saved on the meeting.
        meeting = get_meeting(self_id)
        assert meeting.brief is not None
        assert meeting.brief.markdown == "BRIEF_MD"
        assert meeting.brief.group_id is not None
        assert meeting.brief.person_ids == [person.id]

    def test_non_group_prompt_has_no_recap_block(self, speakers_dir, meetings_dir, groups_dir):
        from src.meeting.prepare import generate_brief
        from src.meeting.store import update_meeting
        from src.speakers.store import create_person

        person = create_person("Zhang")
        mid = _seed_meeting("Standalone")
        update_meeting(mid, expected_people=[person.id])

        fake = _FakeLLM()
        brief = generate_brief(mid, llm=fake)
        assert brief["state"] == "ready"
        synthesis = [c for c in fake.calls if "pre-meeting brief" in c.lower()][0]
        assert "Last meeting" not in synthesis
        assert "Open follow-ups" not in synthesis
        assert "Zhang" in synthesis

    def test_last_meeting_without_summary_degrades(self, speakers_dir, meetings_dir, groups_dir):
        from src.meeting.prepare import generate_brief
        from src.meeting.store import update_meeting

        m1 = _seed_meeting("Episode 1")  # no summary
        self_id = _seed_meeting("This meeting")
        update_meeting(self_id, expected_people=["p_x"])
        _seed_group("Series", [m1, self_id])

        fake = _FakeLLM()
        generate_brief(self_id, llm=fake)
        synthesis = [c for c in fake.calls if "pre-meeting brief" in c.lower()][0]
        assert "not yet generated" in synthesis

    def test_no_expected_people_generates_without_attendee_input(
        self, speakers_dir, meetings_dir, groups_dir
    ):
        """No pre-selected attendees: generation still succeeds, and the
        synthesis input carries no person block at all."""
        from src.meeting.prepare import generate_brief

        mid = _seed_meeting("Nobody selected")
        fake = _FakeLLM()
        brief = generate_brief(mid, llm=fake)
        assert brief["state"] == "ready"
        assert brief["person_ids"] == []
        synthesis = [c for c in fake.calls if "pre-meeting brief" in c.lower()][0]
        assert "(none)" not in synthesis
        assert "PROFILE" not in synthesis


class TestBriefStateAndRoutes:
    @pytest.fixture
    def client(self, speakers_dir, meetings_dir, groups_dir):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.meeting.routes import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_state_none_when_missing(self, client, speakers_dir, meetings_dir, groups_dir):
        mid = _seed_meeting("Fresh")
        resp = client.get(f"/meetings/{mid}/brief")
        assert resp.status_code == 200
        assert resp.json()["state"] == "none"

    def test_brief_unknown_meeting_404(self, client, speakers_dir, meetings_dir, groups_dir):
        assert client.get("/meetings/unknown/brief").status_code == 404

    def test_patch_expected_people(self, client, speakers_dir, meetings_dir, groups_dir):
        from src.speakers.store import create_person

        person = create_person("Zhang")
        mid = _seed_meeting("Patch me")
        resp = client.put(f"/meetings/{mid}", json={"expected_people": [person.id]})
        assert resp.status_code == 200
        assert resp.json()["expected_people"] == [person.id]

    def test_generate_route_end_to_end(self, client, speakers_dir, meetings_dir, groups_dir):
        from src.speakers.store import create_person

        person = create_person("Zhang")
        m1 = _seed_meeting("Episode 1", {"spk0": ["hello world"]}, {"spk0": person.id})
        from src.meeting.store import update_meeting

        self_id = _seed_meeting("This meeting")
        update_meeting(self_id, expected_people=[person.id])
        _seed_group("Series", [m1, self_id])
        update_meeting(m1, summary="settled plan A")

        fake = _FakeLLM(brief_md="BRIEF_ROUTE")
        with patch("src.meeting.prepare._resolve_llm", lambda: fake), patch(
            "src.speakers.profile._resolve_llm", lambda: fake
        ):
            resp = client.post(f"/meetings/{self_id}/brief/generate", json={"locale": "zh-CN"})
            assert resp.status_code == 200
            assert resp.json()["state"] in {"generating", "ready"}

            deadline = time.monotonic() + 5.0
            body = None
            while time.monotonic() < deadline:
                body = client.get(f"/meetings/{self_id}/brief").json()
                if body["state"] == "ready":
                    break
                time.sleep(0.05)
        assert body is not None and body["state"] == "ready"
        assert body["markdown"] == "BRIEF_ROUTE"
        assert body["person_ids"] == [person.id]

    def test_generate_route_without_people_succeeds(
        self, client, speakers_dir, meetings_dir, groups_dir
    ):
        """No pre-selected attendees: the route accepts the kick and the
        brief lands ready with an empty person list."""
        mid = _seed_meeting("Nobody")
        fake = _FakeLLM(brief_md="BRIEF_NO_PEOPLE")
        with patch("src.meeting.prepare._resolve_llm", lambda: fake):
            resp = client.post(f"/meetings/{mid}/brief/generate", json={})
            assert resp.status_code == 200
            assert resp.json()["state"] in {"generating", "ready"}

            deadline = time.monotonic() + 5.0
            body = None
            while time.monotonic() < deadline:
                body = client.get(f"/meetings/{mid}/brief").json()
                if body["state"] == "ready":
                    break
                time.sleep(0.05)
        assert body is not None and body["state"] == "ready"
        assert body["markdown"] == "BRIEF_NO_PEOPLE"
        assert body["person_ids"] == []


class _SlowLLM(_FakeLLM):
    def __init__(self, delay: float = 0.5, **kw):
        super().__init__(**kw)
        self.delay = delay

    def generate(self, prompt: str, **kwargs) -> str:
        import time as _time

        _time.sleep(self.delay)
        return super().generate(prompt, **kwargs)


class TestBriefProfileCoordination:
    def test_brief_waits_for_ongoing_regeneration(self, speakers_dir, meetings_dir, groups_dir):
        from src.meeting.prepare import generate_brief
        from src.speakers.profile import start_regenerate
        from src.speakers.store import create_person

        person = create_person("Zhang")
        m1 = _seed_meeting("Episode 1", {"spk0": ["hello world"]}, {"spk0": person.id})
        from src.meeting.store import update_meeting

        self_id = _seed_meeting("This meeting")
        update_meeting(self_id, expected_people=[person.id])
        _seed_group("Series", [m1, self_id])
        update_meeting(m1, summary="settled plan A")

        slow = _SlowLLM(delay=0.4, profile_text="SLOW_PROFILE")
        with patch("src.speakers.profile._resolve_llm", lambda: slow):
            start_regenerate(person.id, force=True)
            fast = _FakeLLM(brief_md="BRIEF_AFTER_WAIT")
            brief = generate_brief(self_id, llm=fast)
        assert brief["markdown"] == "BRIEF_AFTER_WAIT"
        assert not [c for c in fast.calls if "pattern evidence" in c.lower()]
        assert len([c for c in slow.calls if "pattern evidence" in c.lower()]) == 1

    def test_profiles_refresh_concurrently(self, speakers_dir, meetings_dir, groups_dir):
        import time as _time

        from src.meeting.prepare import generate_brief
        from src.speakers.store import create_person
        from src.meeting.store import update_meeting

        zhang = create_person("Zhang")
        li = create_person("Li")
        m1 = _seed_meeting("E1", {"spk0": ["hello world"]}, {"spk0": zhang.id})
        m2 = _seed_meeting("E2", {"spk0": ["other words"]}, {"spk0": li.id})
        self_id = _seed_meeting("This meeting")
        update_meeting(self_id, expected_people=[zhang.id, li.id])
        _seed_group("Series", [m1, m2, self_id])

        slow = _SlowLLM(delay=0.3)
        t0 = _time.monotonic()
        brief = generate_brief(self_id, llm=slow)
        elapsed = _time.monotonic() - t0
        assert brief["state"] == "ready"
        # Per person: extraction + aggregation = 2 delayed calls. Concurrent
        # refresh wall ≈ 0.6s + 0.3s synthesis; sequential would be ≥ 1.5s.
        assert elapsed < 1.2, f"refresh took {elapsed:.2f}s — not concurrent"


class TestSummarySource:
    def test_recap_reads_general_tab_md_not_only_field(self, speakers_dir, meetings_dir, groups_dir):
        """Production stores the general summary in tab_general.md (Docker-path
        md_file_path), NOT in Meeting.summary — the recap must read the md."""
        from src.meeting.prepare import build_brief_context
        from src.meeting.store import save_section_md, update_meeting

        m1 = _seed_meeting("Episode 1")
        self_id = _seed_meeting("This meeting")
        update_meeting(self_id, expected_people=["p_x"])
        _seed_group("Series", [m1, self_id])
        save_section_md(m1, "tab_general", "Settled plan A; deferred plan B.")

        ctx = build_brief_context(self_id)
        assert ctx["last_meeting"]["has_summary"] is True
        assert "Settled plan A" in ctx["last_meeting"]["summary_clip"]


class TestSpkResolution:
    def test_summary_spk_markers_resolve_to_names(self, speakers_dir, meetings_dir, groups_dir):
        """Summaries keep [spk:N] markers; the brief context must resolve them
        to speaker names so raw markers never reach the prompt."""
        from src.meeting.prepare import build_brief_context
        from src.meeting.store import save_section_md, update_meeting
        from src.speakers.store import create_person

        zhang = create_person("Zhang")
        li = create_person("Li")
        m1 = _seed_meeting(
            "Episode 1", {"spk0": ["a"], "spk1": ["b"]}, {"spk0": zhang.id, "spk1": li.id}
        )
        self_id = _seed_meeting("This meeting")
        update_meeting(self_id, expected_people=[zhang.id])
        _seed_group("Series", [m1, self_id])
        save_section_md(
            m1,
            "tab_general",
            "[spk:1] to send the consolidated quote to [spk:0] for approval.",
        )

        ctx = build_brief_context(self_id)
        clip = ctx["last_meeting"]["summary_clip"]
        assert "[spk:" not in clip
        assert "Li" in clip and "Zhang" in clip
