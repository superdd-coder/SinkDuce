"""Explicit, request-local Q&A source scope shared by vector query entry points."""

from contextlib import contextmanager
from contextvars import ContextVar

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

_scope: ContextVar[tuple[str, list[str] | None] | None] = ContextVar(
    "qa_source_scope", default=None
)


@contextmanager
def qa_source_scope(list_id: str, version_ids: list[str] | None = None):
    token = _scope.set((list_id, version_ids))
    try:
        yield
    finally:
        _scope.reset(token)


def source_filter(existing: Filter | None = None) -> Filter:
    kind = FieldCondition(key="source_kind", match=MatchValue(value="qa_material"))
    ordinary = Filter(must_not=[kind])
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
