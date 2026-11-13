"""Private draft revisions and optional material references, using fictional text."""

import json
from threading import Barrier, BrokenBarrierError
from types import SimpleNamespace

import pytest

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


@pytest.fixture
def exchange(qa):
    detail = mutate(qa, create(qa), "/questions", {"text": "Confirm the archive opening hours."})
    qid = detail["questions"][0]["id"]
    cid = detail["columns"][0]["id"]
    return detail, qid, cid


def generate(qa, exchange, **changes):
    client, base = qa
    detail, qid, cid = exchange
    return client.post(
        f"{base}/lists/{detail['id']}/questions/{qid}/drafts/{cid}/generate",
        json={
            "expected_revision": detail["revision"],
            "version_id": detail["version"]["id"],
            **changes,
        },
    )


def model(monkeypatch, *decisions):
    from src.services import services

    responses = iter(decisions)
    seen, searches = [], []

    class LLM:
        def generate(self, prompt, **_):
            packet = json.loads(prompt.split("\n", 1)[1])
            seen.append(packet)
            return json.dumps(next(responses))

    def project_search(query, **_):
        searches.append(query)
        return SimpleNamespace(retained_chunks=[], tasks=[], errors=[])

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", SimpleNamespace(run=project_search))
    return seen, searches


def material_search(monkeypatch, *, matches=(), missing=()):
    from src.qa import materials

    resolutions, searches = [], []

    def resolve(_collection, _lid, versions):
        resolutions.append(list(versions))
        return [
            {"file_id": "archive-guide", "version_id": version, "filename": "Archive guide.pdf"}
            for version in versions
        ]

    def search(_collection, _lid, query, *, versions, names, allow_auto):
        searches.append((query, versions, names, allow_auto))
        return list(matches), list(missing)

    monkeypatch.setattr(materials, "resolve_references", resolve, raising=False)
    monkeypatch.setattr(materials, "search_references", search, raising=False)
    return resolutions, searches


def test_new_request_distinguishes_existing_outward_text_from_edit_instruction(
    qa, exchange, monkeypatch
):
    seen, searches = model(
        monkeypatch,
        {"kind": "message", "needs_retrieval": False},
        {"body": "The archive opens at noon."},
    )
    result = generate(
        qa, exchange, instructions="The archive generally opens at noon.", guidance="Be concise."
    )
    assert result.status_code == 200, result.text
    assert result.json()["operation"] == "revise"
    assert (
        seen[0]["working_draft"]
        == seen[1]["working_draft"]
        == "The archive generally opens at noon."
    )
    assert seen[0]["guidance"] == seen[1]["guidance"] == "Be concise."
    assert searches == []


def test_continue_uses_private_previous_proposal_and_retains_its_citations(
    qa, exchange, monkeypatch
):
    seen, searches = model(
        monkeypatch,
        {"kind": "guidance", "needs_retrieval": True},
        {"body": "The archive opens at noon on Saturdays.", "sources": [1]},
        {"kind": "message", "needs_retrieval": False},
        {"body": "Here’s the revised draft:\n\nThe archive opens at noon on Saturday."},
    )
    source = {
        "kind": "material",
        "file_id": "archive-guide",
        "version_id": "archive-v1",
        "filename": "Archive guide.pdf",
        "chunk_id": "opening-hours",
        "page_number": 2,
        "excerpt": "Saturday opening begins at noon.",
    }
    resolutions, materials = material_search(monkeypatch, matches=[source])
    first = generate(
        qa,
        exchange,
        instructions="",
        guidance="Draft from the chosen material.",
        material_version_ids=["archive-v1"],
    )
    assert first.status_code == 200, first.text
    second = generate(
        qa,
        exchange,
        instructions="This is not the prior proposal.",
        guidance="Make it shorter.",
        previous_proposal_id=first.json()["id"],
        material_version_ids=["archive-v1"],
    )
    assert second.status_code == 200, second.text
    result = second.json()
    assert result["body"] == "The archive opens at noon on Saturday."
    assert result["sources"] == [source]
    assert result["id"] != first.json()["id"] and result["operation"] == "revise"
    assert seen[-1]["working_draft"] == first.json()["body"]
    assert seen[-1]["guidance"] == "Make it shorter." and seen[-1]["previous_replies"] == []
    assert (
        resolutions == [["archive-v1"], ["archive-v1"]] and len(materials) == 1 and searches == []
    )
    assert materials[0][1][0]["version_id"] == "archive-v1" and materials[0][3] is False
    current = qa[0].get(f"{qa[1]}/lists/{exchange[0]['id']}").json()
    assert current["questions"][0]["replies"][0]["body"] == ""


@pytest.mark.parametrize("original", ["", "The desk has the preliminary opening schedule."])
def test_follow_up_uses_the_edited_proposal_as_its_only_working_draft(
    qa, exchange, monkeypatch, original
):
    detail, qid, first_column = exchange
    earlier = "The reception team will confirm the hours."
    detail = mutate(
        qa, detail, f"/questions/{qid}/replies/{first_column}", {"body": earlier}, "put"
    )
    exchange = detail, qid, detail["columns"][1]["id"]
    edited = "We expect to reopen in spring, subject to confirmation."
    seen, searches = model(
        monkeypatch,
        {"kind": "guidance", "needs_retrieval": False},
        {"body": "We will send the new opening schedule."},
        {"kind": "guidance", "needs_retrieval": False},
        {"body": "We expect to reopen in spring and will confirm the timing."},
    )
    first = generate(qa, exchange, instructions=original, guidance="Write one sentence.")
    assert first.status_code == 200, first.text
    second = generate(
        qa,
        exchange,
        instructions=original,
        guidance="Make the timing more tentative.",
        previous_proposal_id=first.json()["id"],
        edited_draft=edited,
    )
    assert second.status_code == 200, second.text
    assert second.json()["operation"] == "revise"
    assert seen[0]["working_draft"] == seen[1]["working_draft"] == original
    assert seen[1]["operation"] == ("revise" if original else "draft")
    assert seen[2]["working_draft"] == seen[3]["working_draft"] == edited
    assert seen[3]["guidance"] == "Make the timing more tentative."
    assert seen[3]["responding_to"]["body"] == earlier
    assert seen[3]["evidence"] == [] and searches == []
    assert first.json()["body"] not in json.dumps(seen[2:])
    if original:
        assert original not in json.dumps(seen[2:])
    assert "Write one sentence." not in json.dumps(seen[2:])


@pytest.mark.parametrize("other", ["column", "actor", "changed_cell"])
def test_previous_proposal_requires_current_actor_cell_and_context(
    qa, exchange, monkeypatch, other
):
    from src.qa import store

    model(
        monkeypatch,
        {"kind": "message", "needs_retrieval": False},
        {"body": "Could you confirm Saturday opening?"},
    )
    first = generate(qa, exchange, instructions="Could you confirm Saturday opening?", guidance="")
    assert first.status_code == 200, first.text
    detail, qid, cid = exchange
    if other == "column":
        cid = detail["columns"][1]["id"]
    elif other == "actor":
        with store.connection("col_qa", "update") as conn, conn:
            conn.execute(
                "UPDATE qa_draft_proposals SET actor_id=? WHERE id=?",
                ("fictional_other_actor", first.json()["id"]),
            )
    else:
        detail = mutate(
            qa, detail, f"/questions/{qid}/replies/{cid}", {"body": "The archive is closed."}, "put"
        )
    rejected = generate(
        qa,
        (detail, qid, cid),
        guidance="Shorter.",
        previous_proposal_id=first.json()["id"],
        edited_draft="Will the archive open on Saturday?",
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "stale_draft"


def test_edited_draft_requires_a_previous_proposal(qa, exchange):
    result = generate(qa, exchange, edited_draft="Could you confirm the opening hours?")
    assert result.status_code == 422, result.text


def test_missing_explicit_material_is_reported_without_substituting_other_documents(
    qa, exchange, monkeypatch
):
    seen, project = model(
        monkeypatch,
        {"kind": "guidance", "needs_retrieval": True, "material_names": ["Hours notice.pdf"]},
        {"body": "Please confirm the archive hours.", "insufficient_evidence": False},
        {"kind": "message", "needs_retrieval": False},
        {"body": "Could you confirm the hours?", "insufficient_evidence": False},
    )
    _, searches = material_search(monkeypatch, missing=["Hours notice.pdf"])
    response = generate(
        qa, exchange, guidance="Use the attached Hours notice.pdf to verify the opening hours."
    )
    assert response.status_code == 200, response.text
    assert response.json()["unverified_sources"] == ["Hours notice.pdf"]
    assert response.json()["insufficient_evidence"] is True
    assert searches[0][2:] == (["Hours notice.pdf"], False)
    assert seen[-1]["evidence"] == []
    revised = generate(
        qa, exchange, guidance="Make it shorter.", previous_proposal_id=response.json()["id"]
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["unverified_sources"] == ["Hours notice.pdf"]
    assert revised.json()["insufficient_evidence"] is True
    assert project == [] and len(searches) == 1


@pytest.mark.parametrize(
    "guidance,pinned,names,found",
    [
        (
            "No search; use the attached Hours notice.pdf to confirm opening time.",
            [],
            ["Hours notice.pdf"],
            True,
        ),
        ("不用搜索，直接根据已选附件确认开放时间。", ["archive-v1"], [], True),
        ("No search; check Hours notice.pdf.", [], ["Hours notice.pdf"], False),
    ],
)
def test_skip_broader_search_still_reads_requested_material(
    qa, exchange, monkeypatch, guidance, pinned, names, found
):
    source = {
        "kind": "material",
        "file_id": "archive-guide",
        "version_id": "archive-v1",
        "filename": "Hours notice.pdf",
        "chunk_id": "opening-hours",
        "excerpt": "The archive opens at noon.",
    }
    packets, project = model(
        monkeypatch,
        {
            "kind": "guidance",
            "needs_retrieval": True,
            "material_names": names,
            "consult_selected_materials": bool(pinned),
        },
        {"body": "Please confirm the opening time.", "sources": [1]},
    )
    resolutions, searches = material_search(
        monkeypatch, matches=[source] if found else [], missing=[] if found else names
    )

    result = generate(qa, exchange, guidance=guidance, material_version_ids=pinned)

    assert result.status_code == 200, result.text
    assert resolutions == ([pinned] if pinned else [])
    assert len(searches) == 1 and searches[0][2:] == (names, False)
    assert project == []
    assert result.json()["sources"] == ([source] if found else [])
    assert result.json().get("unverified_sources", []) == ([] if found else names)
    assert [evidence["text"] for evidence in packets[-1]["evidence"]] == (
        [source["excerpt"]] if found else []
    )


def test_replacing_a_pin_uses_only_the_new_version_as_evidence(qa, exchange, monkeypatch):
    from src.qa import materials

    seen, project = model(
        monkeypatch,
        {"kind": "guidance", "needs_retrieval": True},
        {"body": "Opening starts at noon.", "sources": [1]},
        {"kind": "message", "needs_retrieval": False},
        {"body": "Opening starts at one.", "sources": [1]},
    )
    material_search(monkeypatch)

    def search(_collection, _lid, _query, *, versions, names, allow_auto):
        assert not names and not allow_auto
        version = versions[0]["version_id"]
        excerpt = "Opening starts at noon." if version == "archive-v1" else "Opening starts at one."
        return [{**versions[0], "kind": "material", "excerpt": excerpt, "chunk_id": version}], []

    monkeypatch.setattr(materials, "search_references", search)
    initial = generate(qa, exchange, guidance="", material_version_ids=["archive-v1"])
    assert initial.status_code == 200, initial.text
    assert initial.json()["sources"][0]["version_id"] == "archive-v1"
    changed = generate(
        qa,
        exchange,
        guidance="",
        previous_proposal_id=initial.json()["id"],
        material_version_ids=["archive-v2"],
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["operation"] == "revise"
    assert [source["version_id"] for source in changed.json()["sources"]] == ["archive-v2"]
    assert [entry["version_id"] for entry in seen[-1]["evidence"]] == ["archive-v2"]
    assert project == []


def test_named_project_fallback_discards_other_retained_files(qa, exchange, monkeypatch):
    from src.rag.retriever import RetrievedChunk
    from src.services import services

    seen, _ = model(
        monkeypatch,
        {"kind": "guidance", "needs_retrieval": True, "material_names": ["Hours notice.pdf"]},
        {"body": "The archive opens at noon.", "sources": [1], "insufficient_evidence": False},
    )
    _, searches = material_search(monkeypatch, missing=["Hours notice.pdf"])
    retained = [
        RetrievedChunk(
            body,
            0.9,
            {"file_id": key, "version_id": f"{key}-v1", "source_label": filename, "id": key},
        )
        for key, filename, body in [
            ("distractor", "Tour calendar.pdf", "The tours start in the morning."),
            ("wanted", "HOURS NOTICE.PDF", "The archive opens at noon."),
        ]
    ]
    monkeypatch.setattr(
        services,
        "agentic_query",
        SimpleNamespace(
            run=lambda *a, **kw: SimpleNamespace(retained_chunks=retained, tasks=[], errors=[])
        ),
    )
    response = generate(qa, exchange, guidance="根据Hours notice.pdf确认开放时间。")
    assert response.status_code == 200, response.text
    assert searches[0][2:] == (["Hours notice.pdf"], False)
    assert [source["file_id"] for source in response.json()["sources"]] == ["wanted"]
    assert response.json()["insufficient_evidence"] is False
    assert not response.json().get("unverified_sources")
    assert [item["file_id"] for item in seen[-1]["evidence"]] == ["wanted"]


def test_generic_project_request_does_not_become_a_named_attachment(qa, exchange, monkeypatch):
    _, project = model(
        monkeypatch,
        {"kind": "guidance", "needs_retrieval": True, "material_names": ["project budget"]},
        {"body": "Please confirm the requested budget."},
    )
    _, materials = material_search(monkeypatch)
    response = generate(qa, exchange, guidance="Check the project budget.")
    assert response.status_code == 200, response.text
    assert project and materials[0][2:] == ([], True)


def test_unscoped_fact_search_runs_both_sources_concurrently_and_preserves_actor(
    qa, exchange, monkeypatch
):
    from src.identity import Actor, get_actor, reset_actor, set_actor
    from src.qa import materials
    from src.rag.retriever import RetrievedChunk
    from src.services import services

    seen, _ = model(
        monkeypatch,
        {"kind": "guidance", "needs_retrieval": True},
        {"body": "Please reconcile the two published times.", "sources": [1, 6]},
    )
    barrier = Barrier(2, timeout=1)
    actors, met = {}, []

    def meet(kind):
        actors[kind] = get_actor().id
        try:
            barrier.wait()
        except BrokenBarrierError:
            return
        met.append(kind)

    def search_materials(*_args, **_kwargs):
        meet("material")
        return [
            {
                "kind": "material",
                "file_id": "notice",
                "version_id": "notice-v1",
                "filename": "Desk notice.txt",
                "chunk_id": f"desk-{index}",
                "excerpt": f"Opening time is noon; notice item {index}.",
            }
            for index in range(8)
        ], []

    def search_project(*_args, **_kwargs):
        meet("project")
        return SimpleNamespace(
            retained_chunks=[
                RetrievedChunk(
                    f"The archive schedule lists an opening time of one, item {index}.",
                    0.9,
                    {
                        "file_id": "schedule",
                        "version_id": "schedule-v1",
                        "source_label": "Archive schedule.pdf",
                        "id": f"schedule-{index}",
                    },
                )
                for index in range(8)
            ],
            tasks=[],
            errors=[],
        )

    monkeypatch.setattr(materials, "search_references", search_materials)
    monkeypatch.setattr(services, "agentic_query", SimpleNamespace(run=search_project))
    actor = Actor(id="example_draft_owner", kind="key", role_id="owner")
    token = set_actor(actor)
    try:
        result = generate(qa, exchange, guidance="Verify the archive opening time.")
    finally:
        reset_actor(token)
    assert result.status_code == 200, result.text
    assert set(met) == {"material", "project"}
    assert actors == {"material": actor.id, "project": actor.id}
    evidence = seen[-1]["evidence"]
    assert [item["kind"] for item in evidence] == ["material"] * 5 + ["project"] * 3
    assert {source["kind"] for source in result.json()["sources"]} == {"material", "project"}


def test_project_outage_does_not_discard_a_complete_material_answer(qa, exchange, monkeypatch):
    from src.services import services

    seen, _ = model(
        monkeypatch,
        {"kind": "guidance", "needs_retrieval": True},
        {"body": "The archive opens at noon.", "sources": [1], "insufficient_evidence": False},
    )
    source = {
        "kind": "material",
        "file_id": "notice",
        "version_id": "notice-v1",
        "filename": "Desk notice.txt",
        "chunk_id": "opening-hours",
        "excerpt": "Opening time: noon.",
    }
    material_search(monkeypatch, matches=[source])
    monkeypatch.setattr(services, "agentic_query", None)
    result = generate(qa, exchange, guidance="Verify the archive opening time.")
    assert result.status_code == 200, result.text
    assert result.json()["sources"] == [source]
    assert result.json()["insufficient_evidence"] is False
    assert seen[-1]["retrieval_status"] == "ok"


@pytest.mark.parametrize(
    "meta", ["Here is a draft:\n\n", "我明白了，我现在就生成", "好的，我现在就生成回复。"]
)
def test_assistant_preface_removed_or_retried_without_saving_chat(qa, exchange, monkeypatch, meta):
    content = "Could you confirm the archive hours?"
    payload = meta + content if meta.endswith("\n") else meta
    decisions = [{"kind": "message", "needs_retrieval": False}, {"body": payload}]
    if payload == meta:
        decisions.append({"body": content})
    seen, _ = model(monkeypatch, *decisions)
    result = generate(
        qa, exchange, instructions="Confirm archive hours.", guidance="Make this courteous."
    )
    assert result.status_code == 200, result.text
    assert result.json()["body"] == content
    assert len(seen) == len(decisions)


def test_unstated_material_name_and_outward_intro_do_not_get_treated_as_assistant_content(
    qa, exchange, monkeypatch
):
    _, project = model(
        monkeypatch,
        {"kind": "message", "needs_retrieval": False, "material_names": ["Invented file.pdf"]},
        {"body": "Here is the revised archive schedule: Saturday at noon."},
    )
    _, searches = material_search(monkeypatch)
    result = generate(
        qa,
        exchange,
        instructions="Here is the revised archive schedule: Saturday noon.",
        guidance="Polish only.",
    )
    assert result.status_code == 200, result.text
    assert result.json()["body"] == "Here is the revised archive schedule: Saturday at noon."
    assert not project and not searches


def test_rejects_more_than_eight_reference_versions(qa, exchange):
    result = generate(
        qa, exchange, guidance="", material_version_ids=[f"version-{n}" for n in range(9)]
    )
    assert result.status_code == 422, result.text
