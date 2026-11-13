"""Single-reply AI proposals with immutable, validated evidence references."""

import json
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from src.async_ctx import ContextThreadPoolExecutor
from src.identity import get_actor
from src.prompts import QA_DRAFT_SYSTEM, QA_REPLY_INTENT_SYSTEM
from src.rag.index_text import source_name
from src.rag.search_settings import search_settings
from src.rag.source_scope import qa_source_scope

from . import drafts, intelligence, materials, service, store

AuthorRelation = Literal["same_party", "counterparty", "unknown"]


class TurnRelation(BaseModel):
    position: int = Field(ge=1)
    author_relation: AuthorRelation = "unknown"


class ReplyIntent(BaseModel):
    kind: Literal["guidance", "message", "answer", "mixed", "unclear"]
    needs_retrieval: bool | None = None
    skip_retrieval: bool = False
    consult_selected_materials: bool = False
    speech_act: Literal["question", "answer", "mixed", "unknown"] = "unknown"
    question_author_relation: AuthorRelation = "unknown"
    turn_relations: list[TurnRelation] = Field(default_factory=list, max_length=100)
    contribution: Literal["answer", "follow_up", "neutral"] = "neutral"
    material_names: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list, max_length=8
    )


def _responding_to(question, previous):
    if previous:
        return {"kind": "reply", **previous[-1]}
    return {"kind": "question", "position": 0, "label": "", "body": question}


def _column_contribution(label):
    if re.search(r"follow[\s_-]*up|追问|跟进", label, re.IGNORECASE):
        return "follow_up"
    if re.search(r"repl(?:y|ies)|response|answer|答复|回复|回答", label, re.IGNORECASE):
        return "answer"
    return "neutral"


# Match standalone directives. A reference to search inside correspondence or a
# restriction on a specific source is interpreted by the intent model instead.
_NO_SEARCH = re.compile(
    r"(?:^|[.!?;。！？；\n])\s*(?:"
    r"(?:请\s*)?(?:不需要|无需|不用|不要|不必|别|跳过)\s*(?:再\s*|去\s*)?(?:搜索|检索|查资料)"
    r"(?=\s*(?:$|[,.!?;，。！？；\n]|直接|什么的))"
    r"|(?:please\s+)?(?:(?:do not|don['’]t|no need to)\s+(?:search|retrieve|look up)"
    r"|(?:skip|no|without)\s+(?:the\s+)?(?:search(?:ing)?|retrieval))"
    r"(?=\s*(?:$|[,.!?;:\n]|(?:just\s+)?(?:draft|write|reply|answer)\b)))",
    re.IGNORECASE,
)


def reply_operation(
    question, text, *, reply_column, reply_position, previous_replies, guidance=None
):
    packet = {
        "stage": "reply_intent",
        "question": question,
        "reply_column": reply_column,
        "reply_position": reply_position,
        "previous_replies": previous_replies,
        "responding_to": _responding_to(question, previous_replies),
    }
    if guidance is not None:
        packet.update(working_draft=text, guidance=guidance)
    else:
        packet["input_text"] = text
    try:
        intent = intelligence.generate_json(QA_REPLY_INTENT_SYSTEM, packet, ReplyIntent)
    except Exception:  # noqa: BLE001 — keep supplied prose when intent analysis is unavailable
        intent = ReplyIntent(kind="unclear")
    if guidance is not None and text.strip():
        if guidance.strip():
            return "revise", bool(intent.needs_retrieval), intent
        return "polish", False, intent
    if not text.strip() or intent.kind == "guidance":
        return "draft", intent.needs_retrieval is not False, intent
    if intent.kind == "mixed":
        return "revise", bool(intent.needs_retrieval), intent
    return "polish", False, intent


def _attribute_turns(previous, intent):
    relations = {}
    for turn in intent.turn_relations:
        existing = relations.get(turn.position)
        relations[turn.position] = (
            "unknown" if existing and existing != turn.author_relation else turn.author_relation
        )
    attributed = [
        {**turn, "author_relation": relations.get(turn["position"], "unknown")} for turn in previous
    ]
    if attributed:
        # The current column responds to the preceding completed contribution.
        # Older free-form party inference cannot reverse that response direction.
        attributed[-1]["author_relation"] = "counterparty"
    return attributed


class DraftResult(BaseModel):
    body: str = Field(max_length=32767)
    sources: list[int] = Field(default_factory=list, max_length=16)
    insufficient_evidence: bool = True


def _repeats_previous(body, previous):
    normalized = " ".join(body.split()).casefold()
    return bool(normalized) and any(
        normalized == " ".join(reply["body"].split()).casefold() for reply in previous
    )


def _previous_proposal(conn, proposal_id, vid, qid, cid, base, context):
    saved = conn.execute(
        """SELECT result_json,context_hash FROM qa_draft_proposals
        WHERE id=? AND version_id=? AND question_id=? AND column_id=? AND actor_id=?""",
        (proposal_id, vid, qid, cid, get_actor().id),
    ).fetchone()
    if not saved or saved["context_hash"] != context:
        store.fail("stale_draft")
    result = json.loads(saved["result_json"])
    if result.get("base_cell_revision_id") != base or not isinstance(result.get("body"), str):
        store.fail("stale_draft")
    return result


def _explicit_material_names(names, guidance):
    confirmed = {}
    for proposed in names:
        name = proposed.strip().strip("\"'“”‘’").strip()
        if not name:
            continue
        exact = re.escape(name)
        explicit_file = Path(name).suffix.casefold() in materials.EXTENSIONS and re.search(
            rf"(?<![A-Za-z0-9_.-]){exact}(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])", guidance, re.IGNORECASE
        )
        quoted_attachment = re.search(
            rf"[\"'“‘]{exact}[\"'”’]", guidance, re.IGNORECASE
        ) and re.search(r"attach|reference|附件|参考(?:资料|文件)", guidance, re.IGNORECASE)
        if explicit_file or quoted_attachment:
            confirmed.setdefault(name.casefold(), name)
    return list(confirmed.values())


def _filename_key(value):
    return " ".join(Path(value.replace("\\", "/")).name.casefold().split())


_ASSISTANT_PREFACE = re.compile(
    r"\A\s*(?:"
    r"(?:sure[,!.]\s*)?here(?:['’]s| is) (?:a|the|your) "
    r"(?:(?:revised|updated|polished) (?:draft|reply|response)|draft)"
    r"|(?:好的[，,。]\s*)?(?:以下是|这是)"
    r"(?:(?:修改|润色|修订|更新)后(?:的)?(?:草稿|回复|答复)|草稿|回复草稿|答复草稿)"
    r")[：:]\s*",
    re.IGNORECASE,
)
_ASSISTANT_PROGRESS_LINE = re.compile(
    r"\A\s*(?:(?:我)?(?:明白了|了解了)|好的)[，,。！! ]*"
    r"我(?:现在就|这就|马上)(?:生成|起草|修改|润色)(?:回复|答复|草稿)?"
    r"[。！! \t]*(?:\r?\n|\Z)\s*"
)


def _proposal_body(text, interactive):
    if not interactive:
        return text
    return _ASSISTANT_PROGRESS_LINE.sub("", _ASSISTANT_PREFACE.sub("", text, count=1), count=1)


def generate(collection, lid, qid, cid, data, *, on_progress=None, check_cancel=None):
    from src.services import services

    def progress(stage, **details):
        if on_progress:
            on_progress({"stage": stage, **details})

    progress("preparing")
    interactive = data.guidance is not None or bool(
        data.previous_proposal_id or data.material_version_ids
    )
    guidance = (data.guidance or "") if interactive else None
    material_versions = list(dict.fromkeys(data.material_version_ids))
    prior = None
    with store.connection(collection, "update") as conn:
        listing = store.get_list(conn, lid)
        vid = data.version_id or listing["current_version_id"]
        cell = drafts.cell(conn, lid, vid, qid, cid, editable=True)
        if listing["revision"] != data.expected_revision:
            store.fail("stale_revision")
        base = cell["text_revision_id"]
        context = drafts.context_key(conn, vid, qid)
        if data.previous_proposal_id:
            prior = _previous_proposal(
                conn, data.previous_proposal_id, vid, qid, cid, base, context
            )
    working_draft = prior["body"] if prior else data.instructions
    if data.edited_draft is not None:
        working_draft = data.edited_draft
    detail = service.detail(collection, lid, vid)
    question = next(q for q in detail["questions"] if q["id"] == qid)
    index = next(i for i, c in enumerate(detail["columns"]) if c["id"] == cid)
    previous = [
        {"position": position + 1, "label": r["label"], "body": r["body"]}
        for position, r in enumerate(question["replies"][:index])
        if r["included"] and r["body"].strip()
    ]
    target = detail["columns"][index]["label"]
    if intelligence.get_llm() is None:
        store.fail("ai_unavailable", 503)
    progress("intent")
    operation, needs_retrieval, intent = reply_operation(
        question["text"],
        working_draft,
        reply_column=target,
        reply_position=index + 1,
        previous_replies=previous,
        guidance=guidance,
    )
    if prior and working_draft.strip():
        operation = "revise"
    request_text = guidance or ""
    if not interactive and intent.kind in {"guidance", "mixed"}:
        request_text = working_draft
    skip_retrieval = bool(request_text.strip()) and intent.skip_retrieval
    skip_broader_search = (interactive or intent.kind == "guidance") and bool(
        _NO_SEARCH.search(request_text)
    )
    names = (
        _explicit_material_names(intent.material_names, guidance)
        if interactive and not skip_retrieval
        else []
    )
    if skip_retrieval or skip_broader_search:
        needs_retrieval = False
        if skip_retrieval or not (names or intent.consult_selected_materials):
            material_versions = []
    previous = _attribute_turns(previous, intent)
    responding_to = _responding_to(question["text"], previous)
    named_action = _column_contribution(target)
    contribution = intent.contribution
    if contribution == "neutral":
        contribution = named_action
    # The current turn supplies the search focus; the original question supplies
    # background. Old answers alone must not hide a newly raised topic.
    focus = (guidance if interactive else data.instructions).strip()
    query = "\n\n".join(
        dict.fromkeys(
            part
            for part in [
                focus[:4000],
                working_draft.strip()[:2000] if interactive else "",
                responding_to["body"][:4000],
                question["text"][:2000],
            ]
            if part
        )
    )
    chunks, retrieval_gaps = [], []
    material_sources, missing_names = [], []
    material_failed = project_failed = False
    new_selection = bool(material_versions) and set(material_versions) != set(
        prior.get("material_version_ids", []) if prior else []
    )
    if new_selection and working_draft.strip():
        operation = "revise"
    search_materials = interactive and bool(
        names
        or new_selection
        or needs_retrieval
        or (material_versions and intent.consult_selected_materials)
    )
    search_project = needs_retrieval and not (interactive and (names or material_versions))
    search_requested = search_materials or search_project
    project_names = set()
    if search_requested:
        progress("retrieving", operation=operation)
    refs = (
        materials.resolve_references(collection, lid, material_versions)
        if material_versions
        else []
    )

    def read_materials():
        auto = not (names or material_versions)
        try:
            found, missing = materials.search_references(
                collection,
                lid,
                query,
                versions=refs,
                names=names,
                allow_auto=auto,
                **({"check_cancel": check_cancel} if check_cancel else {}),
            )
            return found, missing, False
        except (OSError, sqlite3.OperationalError):
            if not auto:
                raise
            return [], [], True

    def read_project():
        if not services.agentic_query:
            return [], [], True

        def retrieval_progress(event):
            stage = {
                "decompose": "planning",
                "variant_generation": "planning",
                "retrieve": "retrieving",
                "retrieving": "retrieving",
                "grading": "grading",
            }.get(event.get("step"))
            if stage:
                progress(stage, operation=operation)

        try:
            # Attachments are read separately; the shared project search never
            # includes private Q&A material, even under a surrounding source scope.
            with qa_source_scope(lid, []):
                retrieved = services.agentic_query.run(
                    query,
                    collections=[collection],
                    **search_settings("agentic"),
                    generate_answer=False,
                    include_images=False,
                    include_archived=False,
                    decompose=True,
                    preliminary_retrieval=True,
                    original_user_question=query,
                    on_step=retrieval_progress,
                )
                # all_chunks can restore rejected hits for Search/Recall display.
                # Only the shared pipeline's actual context evidence can be used.
                # Provider/grading fallbacks are unverified and stay out of drafts.
                if retrieved.errors:
                    return [], [], True
                gaps = list(
                    dict.fromkeys(
                        str(sub.get("gap_analysis") or "").strip()[:1500]
                        for task in retrieved.tasks
                        for sub in task.get("sub_queries", [])
                        if str(sub.get("gap_analysis") or "").strip()
                    )
                )[:8]
                return list(retrieved.retained_chunks), gaps, False
        except Exception:  # noqa: BLE001 — provider/parser failures retain a manual or retry path
            return [], [], True

    if search_materials and search_project:
        # These paths are independent for an unscoped request. Preserve actor and
        # scope on the material worker and join it before releasing the draft slot.
        with ContextThreadPoolExecutor(max_workers=1) as pool:
            pending_materials = pool.submit(read_materials)
            chunks, retrieval_gaps, project_failed = read_project()
            material_sources, missing_names, material_failed = pending_materials.result()
    else:
        if search_materials:
            material_sources, missing_names, material_failed = read_materials()
            if not (skip_broader_search or material_versions) and not re.search(
                r"\battach(?:ed|ments?)\b|附件", guidance, re.IGNORECASE
            ):
                project_names = {
                    _filename_key(name)
                    for name in missing_names
                    if Path(name).suffix.casefold() in materials.EXTENSIONS
                }
        if search_project or project_names:
            chunks, retrieval_gaps, project_failed = read_project()
    if check_cancel:
        check_cancel()
    sources, evidence = [], []

    def add_evidence(source, sheet_name=""):
        sources.append(source)
        evidence.append(
            {
                "number": len(sources),
                "kind": source["kind"],
                "text": source["excerpt"],
                "filename": source["filename"],
                "sheet_name": sheet_name,
                "file_id": source["file_id"],
                "version_id": source["version_id"],
                "chunk_id": source["chunk_id"],
            }
        )

    project_sources = []
    project_seen = set()
    for chunk in chunks:
        if len(project_sources) == 8:
            break
        m = chunk.metadata
        # Stable version identities are required for a source card.
        if m.get("source_kind") == "qa_material":
            continue
        if m.get("collection") not in (None, "", collection):
            continue
        version, fid = m.get("version_id"), m.get("file_id")
        if not version or not fid:
            continue
        key = (fid, version, m.get("id") or chunk.text)
        if key in project_seen:
            continue
        source = {
            "kind": "project",
            "file_id": fid,
            "version_id": version,
            "filename": source_name(m),
            "chunk_id": m.get("id", ""),
            "page_number": m.get("page_number"),
            "excerpt": chunk.text,
        }
        with store.connection(collection) as conn:
            file = conn.execute(
                "SELECT storage_file_id FROM file_versions WHERE file_id=? AND version_id=?",
                (fid, version),
            ).fetchone()
            if file:
                source["filename"] = Path(file[0]).name
        matched = project_names & {_filename_key(source["filename"]), _filename_key(source_name(m))}
        if project_names and not matched:
            continue
        project_seen.add(key)
        project_sources.append((source, str(m.get("sheet_name") or ""), matched))

    # Keep project counter-evidence in view even when materials fill the shortlist.
    material_budget = max(5, 8 - len(project_sources))
    chosen_materials = material_sources[:material_budget]
    for source in chosen_materials:
        add_evidence(source)
    verified_names = set()
    for source, sheet_name, matched in project_sources[: 8 - len(sources)]:
        verified_names.update(matched)
        add_evidence(source, sheet_name)
    missing_names = [name for name in missing_names if _filename_key(name) not in verified_names]
    content_scope = "project_context"
    if not search_requested:
        content_scope = (
            "conversation_only"
            if skip_retrieval or skip_broader_search or operation == "draft"
            else "input_only"
        )
    packet = {
        "question": question["text"],
        "question_author_relation": intent.question_author_relation,
        "conversation_stage": "continuation" if previous else "first_reply",
        "previous_replies": previous,
        "responding_to": responding_to,
        "contribution": contribution,
        "reply_column": target,
        "reply_position": index + 1,
        "operation": operation,
        "speech_act": intent.speech_act,
        "content_scope": content_scope,
        "retrieval_status": "skipped"
        if not search_requested
        else "ok"
        if evidence
        else "unavailable"
        if material_failed or project_failed
        else "empty",
        "evidence": evidence,
        "retrieval_gaps": retrieval_gaps,
    }
    if interactive:
        packet.update(working_draft=working_draft, guidance=guidance)
        if missing_names:
            packet["unverified_sources"] = missing_names
    else:
        packet["input_text"] = working_draft
    empty_continuation = not (working_draft.strip() or (guidance or "").strip()) and bool(previous)
    progress("drafting", operation=operation, count=len(evidence))
    try:
        result = intelligence.generate_json(QA_DRAFT_SYSTEM, packet, DraftResult)
        body = _proposal_body(result.body, interactive)
        retry = (
            "assistant_commentary"
            if interactive and not body.strip()
            else "repeated_previous_turn"
            if empty_continuation and _repeats_previous(body, previous)
            else ""
        )
        if retry:
            progress("retrying", operation=operation)
            result = intelligence.generate_json(
                QA_DRAFT_SYSTEM,
                {**packet, "retry_reason": retry},
                DraftResult,
            )
            body = _proposal_body(result.body, interactive)
    except Exception:  # noqa: BLE001 — provider/parser failures retain a manual or retry path
        store.fail("ai_unavailable", 503)
    if interactive and not body.strip():
        store.fail("ai_unavailable", 503)
    if empty_continuation and _repeats_previous(body, previous):
        store.fail("ai_repeated_reply", 503)
    selected = [sources[i - 1] for i in dict.fromkeys(result.sources) if 1 <= i <= len(sources)]
    current_reply = next(reply for reply in question["replies"] if reply["column_id"] == cid)
    prior_evidence = prior if working_draft.strip() and not search_requested else None
    if prior_evidence:
        selected = prior_evidence.get("sources", [])
        missing_names = prior_evidence.get("unverified_sources", [])
    elif (
        not prior
        and not search_requested
        and (operation == "polish" or interactive)
        and working_draft == current_reply["body"]
    ):
        selected = current_reply.get("sources", [])
    proposal = {
        "id": store.new_id(),
        "body": body,
        "operation": operation,
        "sources": selected,
        "base_cell_revision_id": base,
        "insufficient_evidence": bool(missing_names)
        or bool(prior_evidence and prior_evidence.get("insufficient_evidence"))
        or search_requested
        and (
            result.insufficient_evidence
            or not selected
            or (not chosen_materials and bool(retrieval_gaps))
        ),
        "context_hash": context,
    }
    if interactive:
        proposal["material_version_ids"] = material_versions
    if missing_names:
        proposal["unverified_sources"] = missing_names
    progress("finalizing", operation=operation)
    with store.connection(collection, "update") as conn, conn:
        current = drafts.cell(conn, lid, vid, qid, cid, editable=True)
        if current["text_revision_id"] != base or drafts.context_key(conn, vid, qid) != context:
            store.fail("stale_draft")
        conn.execute(
            "INSERT INTO qa_draft_proposals VALUES(?,?,?,?,?,?,?,?)",
            (
                proposal["id"],
                vid,
                qid,
                cid,
                get_actor().id,
                context,
                json.dumps(proposal, ensure_ascii=False),
                store.now(),
            ),
        )
    return proposal
