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

    def agentic(self, service, query, **kwargs):
        if kwargs.get("include_archived"):
            return service.run(query, **kwargs)
        projects = self.resolve()
        if self.issue:
            return AgenticQueryResult(answer=None, context=self.issue)
        if not projects:
            return service.run(query, **kwargs)
        with qa_chat_scope(projects if self.has_qa else ()):
            return service.run(query, **(kwargs | {"collections": list(projects), "include_archived": False}))

    def direct(self, service, query, **kwargs):
        if not self.selected:
            return service.retrieve(query, **kwargs)
        projects = self.resolve()
        if self.issue:
            return DirectQueryResult(context=self.issue)
        if not projects:
            return service.retrieve(query, **kwargs)
        requested = kwargs.get("collections") or projects
        targets = [project for project in projects if project in requested]
        if not targets:
            return DirectQueryResult()
        kwargs = kwargs | {"collections": targets}
        with qa_chat_scope(targets if self.has_qa else ()):
            result = service.retrieve(query, **kwargs)
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
            result = service.retrieve(query, **kwargs)
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
