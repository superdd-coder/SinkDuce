"""Lock the user's project scope before Chat performs mixed document/Q&A retrieval."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from src.rag.agentic_query import AgenticQueryResult
from src.rag.direct_query import DirectQueryResult
from src.rag.source_scope import is_qa_chat_chunk, qa_chat_scope

_SEARCH = "search_knowledge_base"
_DIRECTORY = "list_collections"
_PROJECT_REFERENCE = re.compile(
    r"\b(?:this|that|the|the same)\s+project\b|(?:这个|那个|该|上述|本|同一(?:个)?|为什么|为何)\s*项目",
    re.IGNORECASE,
)
_DIRECTORY_REQUEST = re.compile(
    r"\b(?:list|show|enumerate|which|what)\b[^.!?\n]{0,35}\b(?:projects|collections|libraries)\b"
    r"|(?:列出|列举|显示|查看|有哪些|哪些)\s*(?:所有|全部|当前|可用的|已选的)?\s*(?:项目|资料库|集合)"
    r"|(?:项目|资料库|集合)\s*(?:清单|列表|有哪些)",
    re.IGNORECASE,
)


def _normalized(text):
    return unicodedata.normalize("NFKC", text).casefold()


def _named_projects(question, directory):
    names = defaultdict(set)
    for item in directory:
        name = _normalized(item["name"]).strip()
        alias = re.sub(r"^project\s+|\s+project$|项目$", "", name).strip()
        labels = {name, _normalized(item["id"])}
        if len(alias) >= 3:
            labels.add(alias)
        for label in labels - {""}:
            names[label].add(item["id"])
    remaining = _normalized(question)
    matches = []
    for label in sorted(names, key=len, reverse=True):
        pattern = rf"(?<![a-z0-9_]){re.escape(label)}(?![a-z0-9_])"
        remaining, count = re.subn(pattern, " ", remaining)
        if count:
            matches.append(names[label])
    return matches


class QaTurn:
    """A server-checked scope shared by every knowledge search in this turn."""

    def __init__(self, question, selected, agentic, *, previous_question="", previous_projects=()):
        self.question = question
        self.selected = tuple(dict.fromkeys(selected or ()))
        self.agentic_service = agentic
        self.previous_question = previous_question
        self.previous_projects = previous_projects
        self.projects = None
        self.has_qa = False
        self.issue = ""
        self.search_finished = False
        self.skipped_collections = {}
        self.embedding_searches = {}
        self.wants_directory = bool(_DIRECTORY_REQUEST.search(question))

    def resolve(self):
        if self.projects is not None:
            return self.projects
        self.projects = ()

        from src.collections.store import list_collections_meta
        from src.prompts import QA_CHAT_SELECT_PROJECT, QA_CHAT_CLARIFY_PROJECT
        from src.qa import search_index

        directory = [m for m in list_collections_meta() if search_index.can_read(m["id"])]
        visible = {item["id"] for item in directory}
        allowed = tuple(project for project in self.selected if project in visible) if self.selected else tuple(
            item["id"] for item in directory
        )
        if not allowed:
            return self.projects

        named = _named_projects(self.question, directory)
        if not named and _PROJECT_REFERENCE.search(self.question):
            previous = set().union(*_named_projects(self.previous_question, directory))
            if not previous:
                previous = set(self.previous_projects) & visible
            if len(previous) == 1:
                named = [previous]
        if named:
            targets = set()
            for candidates in named:
                in_scope = candidates.intersection(allowed)
                if not in_scope:
                    self.issue = QA_CHAT_SELECT_PROJECT
                    return self.projects
                if len(in_scope) != 1:
                    self.issue = QA_CHAT_CLARIFY_PROJECT
                    return self.projects
                targets.update(in_scope)
            self.projects = tuple(project for project in allowed if project in targets)
        elif len(allowed) == 1:
            self.projects = allowed
        elif any(search_index.has_questions(project) for project in allowed):
            try:
                service = self.agentic_service
                entries = service.catalog.get_catalog(list(allowed))
                suggested = service.decomposer.route_qa_projects(self.question, entries)
                if isinstance(suggested, (list, tuple)):
                    self.projects = tuple(project for project in allowed if project in suggested)
            except Exception:
                pass
            if self.selected and not self.projects:
                self.issue = QA_CHAT_CLARIFY_PROJECT
        self.has_qa = any(search_index.has_questions(project) for project in self.projects)
        return self.projects

    def _checked_search(self, call, query, kwargs, *, agentic):
        from src.collections import store
        from src.config import EmbeddingConfig
        from src.identity import get_actor, visibility
        from src.meeting.transcript_index import is_internal_collection
        from src.rag.collection_utils import get_collection_embedding_config
        from src.services import services

        if not isinstance(getattr(services.config, "embedding", None), EmbeddingConfig) or services.db is None:
            return call(query, **kwargs)
        scope = self.selected or services.db.list_collections()
        requested = set(kwargs.get("collections") or scope)
        candidates = [cid for cid in dict.fromkeys(scope)
                      if not is_internal_collection(cid)
                      and (kwargs.get("include_archived") or not store.is_archived(cid))]
        visible = visibility.filter_collection_ids(get_actor(), candidates)
        available = []
        unavailable = set()
        for cid in visible:
            try:
                config = get_collection_embedding_config(services.db.get_collection_config(cid), cid)
            except Exception:
                config = None
            if config is None:
                unavailable.add(cid)
                self.skipped_collections[cid] = True
            elif cid in requested:
                available.append(cid)
        if not available:
            context = "No searchable collections in the selected scope. No knowledge-base evidence is available."
            return AgenticQueryResult(answer=None, context=context) if agentic else DirectQueryResult(context=context)
        result = call(query, **(kwargs | {"collections": available}))
        if agentic and unavailable.intersection(requested):
            from src.rag.agent_prompts import PARTIAL_COLLECTION_COVERAGE
            result.coverage_complete = False
            if result.context and not result.context.startswith(PARTIAL_COLLECTION_COVERAGE):
                result.context = PARTIAL_COLLECTION_COVERAGE + "\n\n" + result.context
        # Only completed vector-store reads carry the model actually used.
        # Agentic searches may produce several events for one collection.
        for event in getattr(result, "trace", []) or []:
            if (event.get("kind") not in {"retrieval", "retrieval_fallback"}
                    or event.get("collection") not in available or not event.get("model")):
                continue
            row = {key: event[key] for key in ("collection", "model", "dimensions", "mode", "provider_id", "provider_name")}
            key = (row["collection"], row["provider_id"], row["model"], row["dimensions"], row["mode"])
            self.embedding_searches[key] = row
        for error in getattr(result, "errors", []) or []:
            if error.get("stage") == "retrieve" and error.get("collection") in available:
                self.skipped_collections[error["collection"]] = True
        return result

    def search_metadata(self):
        metadata = {}
        searched = {row["collection"] for row in self.embedding_searches.values()}
        skipped = [cid for cid in self.skipped_collections if cid not in searched]
        if skipped:
            metadata["skipped_collections"] = skipped
        if self.embedding_searches:
            metadata["embedding_searches"] = [self.embedding_searches[key] for key in sorted(self.embedding_searches)]
        return metadata

    def agentic(self, service, query, **kwargs):
        if kwargs.get("include_archived"):
            return self._checked_search(service.run, query, kwargs, agentic=True)
        projects = self.resolve()
        if self.issue:
            return AgenticQueryResult(answer=None, context=self.issue)
        if not projects:
            return self._checked_search(service.run, query, kwargs, agentic=True)
        with qa_chat_scope(projects if self.has_qa else ()):
            return self._checked_search(service.run, query, kwargs | {"collections": list(projects), "include_archived": False}, agentic=True)

    def direct(self, service, query, **kwargs):
        if not self.selected:
            return self._checked_search(service.retrieve, query, kwargs, agentic=False)
        projects = self.resolve()
        if self.issue:
            return DirectQueryResult(context=self.issue)
        if not projects:
            return self._checked_search(service.retrieve, query, kwargs, agentic=False)
        requested = kwargs.get("collections") or projects
        targets = [project for project in projects if project in requested]
        if not targets:
            return DirectQueryResult()
        kwargs = kwargs | {"collections": targets}
        with qa_chat_scope(targets if self.has_qa else ()):
            result = self._checked_search(service.retrieve, query, kwargs, agentic=False)
        if not any(is_qa_chat_chunk(chunk) for chunk in result.chunks):
            return result

        from src.rag.agent_nodes import node_combined_grade
        from src.rag.agent_state import AgentState
        from src.rag.context_builder import build_context

        state = AgentState(original_query=query, original_user_question=self.question)
        if service.llm is None:
            state.grade_error = "GradingUnavailable"
        else:
            try:
                node_combined_grade(state, result.chunks, llm=service.llm)
            except Exception:
                state.grade_error = "GradingUnavailable"
        if state.grade_error:
            result = self._checked_search(service.retrieve, query, kwargs, agentic=False)
            result.errors.append({"stage": "qa_grade", "error": state.grade_error})
        else:
            result.qa_hits = [chunk for chunk in state.retained_chunks if is_qa_chat_chunk(chunk)]
            result.chunks = [chunk for chunk in state.retained_chunks if not is_qa_chat_chunk(chunk)]
            result.context = build_context(result.chunks) if result.chunks else ""
        return result

    def finish_search(self, result, bundle):
        evidence_ready = bundle.posts if getattr(result, "qa_hits", None) else getattr(result, "retained_chunks", None)
        if self.issue or (getattr(result, "coverage_complete", False) is True and evidence_ready):
            self.search_finished = True

    def available_tools(self, tools):
        if not self.search_finished:
            return tools
        finished = {_SEARCH} if self.wants_directory else {_SEARCH, _DIRECTORY}
        return [tool for tool in tools if tool["function"]["name"] not in finished]

    def plan_tool_calls(self, calls, *, has_catalog):
        from src.chatbox.query_tools import merge_search_tool_calls

        calls = self.available_tools(merge_search_tool_calls(calls))
        if has_catalog and not self.wants_directory and any(
            call["function"]["name"] == _SEARCH for call in calls
        ):
            return [call for call in calls if call["function"]["name"] != _DIRECTORY]
        return calls
