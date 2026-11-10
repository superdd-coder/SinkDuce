"""Explicit, request-local Q&A source scope shared by vector query entry points."""

from contextlib import contextmanager
from contextvars import ContextVar

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

QA_CHAT_KINDS = ("qa_question", "qa_reply")

_scope: ContextVar[tuple[str, list[str] | None] | None] = ContextVar(
    "qa_source_scope", default=None
)



qa_chat_projects: ContextVar[tuple[str, ...]] = ContextVar("qa_chat_projects", default=())


def is_qa_chat_chunk(chunk) -> bool:
    return chunk.metadata.get("source_kind") in QA_CHAT_KINDS


@contextmanager
def qa_chat_scope(projects):
    token = qa_chat_projects.set(tuple(projects))
    try:
        yield
    finally:
        qa_chat_projects.reset(token)


@contextmanager
def qa_source_scope(list_id: str, version_ids: list[str] | None = None):
    token = _scope.set((list_id, version_ids))
    try:
        yield
    finally:
        _scope.reset(token)


def source_filter(existing: Filter | None = None, *, include_qa_materials: bool = False) -> Filter:
    qa_chat = FieldCondition(key="source_kind", match=MatchAny(any=list(QA_CHAT_KINDS)))
    if include_qa_materials:
        return Filter(must=[existing] if existing else None, must_not=[qa_chat])
    kind = FieldCondition(key="source_kind", match=MatchValue(value="qa_material"))
    ordinary = Filter(must_not=[kind, qa_chat])
    scope = _scope.get()
    allowed = ordinary
    if scope:
        lid, versions = scope
        must = [kind, FieldCondition(key="qa_list_id", match=MatchValue(value=lid))]
        if versions is not None:
            must.append(FieldCondition(key="qa_material_version_id", match=MatchAny(any=versions)))
        material = Filter(
            must=must,
            must_not=[
                FieldCondition(key="archived", match=MatchValue(value=True)),
                FieldCondition(key="is_current", match=MatchValue(value=False)),
            ],
        )
        allowed = Filter(should=[ordinary, material])
    return Filter(must=[existing, allowed]) if existing else allowed
