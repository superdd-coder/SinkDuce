"""Exercise the real upload → pinned evidence → revision → adoption flow."""

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_draft_stream import events


def test_streamed_revision_keeps_the_exact_reference_and_only_adoption_saves_it(qa, monkeypatch):
    from src.qa import drafting, intelligence
    from src.services import services

    client, base = qa
    listing = mutate(
        qa, create(qa), "/questions", {"text": "Confirm the conservatory closing time."}
    )
    qid, cid = listing["questions"][0]["id"], listing["columns"][0]["id"]
    list_path = f"{base}/lists/{listing['id']}"
    materials_path = list_path + "/materials"
    draft_path = f"{list_path}/questions/{qid}/drafts/{cid}"
    payload = {"expected_revision": listing["revision"], "version_id": listing["version"]["id"]}

    def upload(content):
        response = client.post(materials_path, files={"file": ("visitor-hours.txt", content)})
        assert response.status_code == 200, response.text
        return response.json()

    older_text = b"The conservatory closes at 17:00. The side entrance is accessible."
    first = upload(older_text)
    newest = upload(b"The conservatory closes at 19:00. The side entrance is accessible.")
    assert first["id"] == newest["id"]
    assert first["version_id"] != newest["version_id"]
    old_version_path = f"{materials_path}/{first['id']}/versions/{first['version_id']}"
    assert client.get(old_version_path).json()["parse_state"] == "ready"

    packets, project_queries = [], []
    monkeypatch.setattr(intelligence, "get_llm", lambda: object())

    class ProjectQuery:
        def run(self, *args, **kwargs):
            project_queries.append(args)
            raise AssertionError("a pinned reference should not be replaced by generic retrieval")

    monkeypatch.setattr(services, "agentic_query", ProjectQuery())

    def generate_json(system, packet, schema):
        if schema is drafting.ReplyIntent:
            return schema(kind="guidance", needs_retrieval=not packets)
        assert schema is drafting.DraftResult
        packets.append(packet)
        if len(packets) == 1:
            assert any("17:00" in evidence["text"] for evidence in packet["evidence"])
            assert all("19:00" not in evidence["text"] for evidence in packet["evidence"])
            return schema(
                body="The conservatory closes at 17:00.", sources=[1], insufficient_evidence=False
            )
        assert packet["working_draft"] == "The conservatory is scheduled to close at 17:00."
        assert packet["evidence"] == []
        return schema(body="Closing time is 17:00.", sources=[], insufficient_evidence=False)

    monkeypatch.setattr(intelligence, "generate_json", generate_json)

    def send(guidance, previous=None, edited=None):
        response = client.post(
            draft_path + "/generate/stream",
            json={
                **payload,
                "instructions": "",
                "guidance": guidance,
                "previous_proposal_id": previous,
                "edited_draft": edited,
                "material_version_ids": [first["version_id"]],
            },
        )
        terminal = [event for event in events(response.text) if event[0] != "progress"]
        assert len(terminal) == 1 and terminal[0][0] == "result", response.text
        return terminal[0][1]

    initial = send("Use the attached visitor hours.")
    final = send(
        "Make the wording shorter.",
        initial["id"],
        "The conservatory is scheduled to close at 17:00.",
    )
    assert final["body"] == "Closing time is 17:00."
    assert final["id"] != initial["id"]
    assert final["sources"] == initial["sources"]
    assert final["sources"][0]["version_id"] == first["version_id"]
    assert project_queries == []
    assert client.get(list_path).json()["questions"][0]["replies"][0]["body"] == ""

    adopted_body = "The closing time is 17:00."
    draft = client.put(
        draft_path,
        json={
            "version_id": payload["version_id"],
            "body": adopted_body,
            "draft_revision": 0,
            "base_cell_revision_id": final["base_cell_revision_id"],
            "proposal_id": final["id"],
        },
    )
    assert draft.status_code == 200, draft.text
    saved = client.put(
        f"{list_path}/questions/{qid}/replies/{cid}",
        json={
            **payload,
            "body": adopted_body,
            "draft_revision": draft.json()["draft_revision"],
            "base_cell_revision_id": final["base_cell_revision_id"],
        },
    )
    assert saved.status_code == 200, saved.text
    reply = saved.json()["questions"][0]["replies"][0]
    assert reply["body"] == adopted_body
    assert reply["sources"] == final["sources"]
    archived = client.patch(
        f"{materials_path}/{newest['id']}",
        json={"expected_revision": newest["revision"], "archived": True},
    )
    assert archived.status_code == 200, archived.text
    assert client.get(old_version_path + "/file").content == older_text
