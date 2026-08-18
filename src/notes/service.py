"""LLM-powered distillation and propagation logic for Notes."""

from __future__ import annotations

import json
import logging
import re

from src.notes import store

logger = logging.getLogger("notes.service")


def expand_distill_blocks(content: str, depth: int = 0, max_depth: int = 3) -> str:
    """Recursively expand distill blocks to include their content.
    Prevents infinite loops with max_depth."""
    if depth >= max_depth:
        return content

    import re
    pattern = re.compile(r':::distill-block(\{[^}]+\})\n([\s\S]*?)\n:::', re.DOTALL)

    def replace_block(match):
        try:
            attrs = json.loads(match.group(1))
            source_id = attrs.get("source", "")
            source_title = attrs.get("source-title", "Unknown")
            block_content = match.group(2).strip()

            # Get source note content
            source_content = store.get_content(source_id)
            if source_content:
                # Recursively expand nested blocks
                expanded_source = expand_distill_blocks(source_content, depth + 1, max_depth)
                return f"[Source: {source_title}]\n{expanded_source}"
            else:
                return f"[Source: {source_title}]\n{block_content}"
        except (json.JSONDecodeError, Exception):
            return match.group(0)

    return pattern.sub(replace_block, content)


from src.prompts import DISTILL_SYSTEM_PROMPT, DISTILL_USER_PROMPT


def get_distillation_prompt(source_content: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for distilling a note."""
    return DISTILL_SYSTEM_PROMPT, DISTILL_USER_PROMPT.format(source_content=source_content)


def get_llm():
    """Note distill slot → default card."""
    from src.rag.contextual import get_note_distill_llm

    return get_note_distill_llm()


def _run_distill_llm(source_content: str, log_label: str) -> str:
    """Call the distill LLM and strip common preambles. Synchronous."""
    from src.parsers.image_utils import prepare_text_for_non_visual_llm
    from src.rag.contextual import provider_model_is_visual, resolve_note_distill_target

    logger.info("Generating distillation for %s (%d chars)", log_label, len(source_content))
    target = resolve_note_distill_target()
    if not provider_model_is_visual(*target):
        source_content = prepare_text_for_non_visual_llm(source_content)
    llm = get_llm()
    system_prompt, user_prompt = get_distillation_prompt(source_content)
    result = llm.generate(user_prompt, system=system_prompt, max_tokens=4096, thinking=False)

    result = result.strip()
    for prefix in ["Here is", "Here's", "Distillation:", "Distilled:", "Summary:"]:
        if result.lower().startswith(prefix.lower()):
            nl = result.find("\n")
            if nl != -1:
                result = result[nl + 1:].strip()
    return result


def distill_note(source_note_id: str, target_note_id: str) -> str:
    """Distill source note content for embedding into target note.
    Uses cache if available. Cache is keyed by source_note_id —
    distillation result depends only on the source, not the target.
    Returns the distilled markdown.

    NOTE: This function is synchronous (calls LLM via blocking generate()).
    Callers in async contexts must wrap with asyncio.to_thread()."""
    # Check cache (single-key: collection + source_note_id)
    cached = store.get_distillation(source_note_id)
    if cached is not None:
        logger.info("Using cached distillation for %s", source_note_id)
        return cached

    # Get source content and expand nested distill blocks
    source_content = store.get_content(source_note_id)
    if not source_content or not source_content.strip():
        source_note = store.get_note(source_note_id)
        title = source_note.title if source_note else source_note_id
        return f"*Note '{title}' is empty.*"

    # Expand nested distill blocks so their content is included
    source_content = expand_distill_blocks(source_content)

    result = _run_distill_llm(source_content, source_note_id)
    store.save_distillation(source_note_id, result)
    return result


# ── Meeting → Note distillation ────────────────────────────────


MEETING_SOURCE_PREFIX = "meeting:"


def meeting_source_id(meeting_id: str, tab_id: str = "tab_general") -> str:
    """Stable distill-block source id for one meeting summary file (one tab)."""
    tid = (tab_id or "tab_general").strip() or "tab_general"
    return f"{MEETING_SOURCE_PREFIX}{meeting_id}:{tid}"


def parse_meeting_source_id(source_id: str) -> tuple[str, str] | None:
    """Return (meeting_id, tab_id) if source_id is a meeting source, else None.

    Accepts:
      meeting:{meeting_id}:{tab_id}
      meeting:{meeting_id}            → tab_general (legacy)
    """
    if not source_id.startswith(MEETING_SOURCE_PREFIX):
        return None
    rest = source_id[len(MEETING_SOURCE_PREFIX) :].strip()
    if not rest:
        return None
    # Split on first colon only — meeting ids are hex without colons
    if ":" in rest:
        mid, tid = rest.split(":", 1)
        mid, tid = mid.strip(), tid.strip()
        if mid:
            return mid, tid or "tab_general"
        return None
    return rest, "tab_general"


def prepare_meeting_summary_for_note(
    content: str,
    speaker_names: dict[str, str] | None = None,
    *,
    resolve_speakers: bool = False,
) -> str:
    """In-memory clean for meeting → note distillation / freeze.

    Must never be written back to meeting section .md files.

    Distill path (``resolve_speakers=False``, default):
      keep ``[spk:ID]`` tokens so the note UI can render *latest* speaker names;
      strip transcript refs + priority only.

    Ingest freeze path (``resolve_speakers=True``):
      map ``[spk:ID]`` / ``Speaker N`` → real names (same idea as meeting allocate).
    """
    import re as _re

    md = content or ""

    # 1a. Unescape markdown-escaped brackets
    md = md.replace("\\[", "[").replace("\\]", "]")

    # 1b. CJK / fullwidth brackets
    for a, b in (
        ("【", "["), ("】", "]"),
        ("〔", "["), ("〕", "]"),
        ("［", "["), ("］", "]"),
    ):
        md = md.replace(a, b)

    # Artifacts like \Speaker 4\
    md = _re.sub(r"\\+[Ss]peaker\s+(\d+)\\*", r"Speaker \1", md)

    names = speaker_names or {}
    if resolve_speakers:
        # Freeze: [spk:ID] / Speaker N → display names
        for spk_id, name in names.items():
            if not name:
                continue
            sid = str(spk_id)
            md = md.replace(f"[spk:{sid}]", name)
            md = _re.sub(
                rf"\bSpeaker\s+{_re.escape(sid)}\b",
                name,
                md,
                flags=_re.IGNORECASE,
            )
        md = _re.sub(r"\[spk:([^\]]+)\]", r"Speaker \1", md)
    else:
        # Distill: keep [spk:ID]; normalize plain "Speaker N" → [spk:N] when N is mapped
        # so the token stays a stable id for live rendering
        for spk_id in list(names.keys()) or []:
            sid = str(spk_id)
            md = _re.sub(
                rf"\bSpeaker\s+{_re.escape(sid)}\b",
                f"[spk:{sid}]",
                md,
                flags=_re.IGNORECASE,
            )
        # Unmapped "Speaker N" (digits) → [spk:N] for consistency
        md = _re.sub(r"\bSpeaker\s+(\d+)\b", r"[spk:\1]", md, flags=_re.IGNORECASE)

    # 3. References — strip [ref:…] and [stt_…], not bare [1]
    _cite_token = (
        r"(?:"
        r"[A-Za-z0-9]{6,}_stt_\d+"
        r"|stt_\d+"
        r"|\d+"
        r")"
    )
    _cite_sep = r"[\s,，、;；\-–—]+"
    _stt_token = r"(?:[A-Za-z0-9]{6,}_)?stt_\d+"
    md = _re.sub(
        rf"\[ref\s*:\s*{_cite_token}(?:{_cite_sep}{_cite_token})*\s*\]",
        "",
        md,
        flags=_re.IGNORECASE,
    )
    md = _re.sub(
        rf"\[(?:ref\s*:)?\s*{_stt_token}(?:{_cite_sep}{_stt_token})*\s*\]",
        "",
        md,
        flags=_re.IGNORECASE,
    )
    md = _re.sub(r"\b[A-Za-z0-9]{6,}_stt_\d+\b", "", md, flags=_re.IGNORECASE)
    md = _re.sub(r"\bstt_\d+\b", "", md, flags=_re.IGNORECASE)

    # 4. priority tags
    md = _re.sub(
        r"\[\s*priority\s*:\s*(?:high|medium|low)\s*\]",
        "",
        md,
        flags=_re.IGNORECASE,
    )

    # 5. Leftover empty / comma-only brackets (never strip [spk:…])
    md = _re.sub(r"\[\s*(?:[,，、;；\-–—\s])*\s*\]", "", md)

    # 6. whitespace
    md = _re.sub(r"[ \t]+\n", "\n", md)
    md = _re.sub(r" +([.,;:!?])", r"\1", md)
    md = _re.sub(r" {2,}", " ", md)
    md = _re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def freeze_speakers_in_note_content(content: str) -> str:
    """Resolve [spk:ID] using speaker maps from meeting distill sources in this note.

    Called only when the note is ingested / reingested into the collection —
    not when viewing distill blocks in the editor.

    Only freezes speakers — does **not** strip refs/priority from the rest of
    the note body (unlike prepare_meeting_summary_for_note for distill).
    """
    import re as _re
    from src.meeting import store as meeting_store

    md = content or ""
    if "[spk:" not in md and not _re.search(r"\bSpeaker\s+\d+\b", md, _re.I):
        return md

    blocks = parse_injection_blocks(md)
    maps: dict[str, str] = {}
    for block in blocks:
        parsed = parse_meeting_source_id(block.get("source_note_id") or "")
        if not parsed:
            continue
        mid, _tid = parsed
        try:
            meeting = meeting_store.get_meeting(mid)
        except Exception:
            meeting = None
        if not meeting:
            continue
        for k, v in (getattr(meeting, "speaker_names", None) or {}).items():
            if v:
                maps[str(k)] = str(v)

    md = md.replace("\\[", "[").replace("\\]", "]")
    for sid, name in maps.items():
        md = md.replace(f"[spk:{sid}]", name)
        md = _re.sub(
            rf"\bSpeaker\s+{_re.escape(sid)}\b",
            name,
            md,
            flags=_re.IGNORECASE,
        )
    # Unmapped leftovers → readable Speaker N
    md = _re.sub(r"\[spk:([^\]]+)\]", r"Speaker \1", md)
    return md


def build_meeting_tab_distill_source(
    meeting_id: str,
    tab_id: str = "tab_general",
) -> tuple[str, str, dict[str, str]]:
    """Build (display_title, raw_markdown, speaker_names) for one meeting tab file.

    display_title is \"{meeting} / {section}\" for sections, or meeting title for general.
    """
    from src.meeting import store as meeting_store

    meeting = meeting_store.get_meeting(meeting_id)
    if not meeting:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")

    meeting_title = (meeting.title or "").strip() or meeting_id
    tid = (tab_id or "tab_general").strip() or "tab_general"
    speaker_names: dict[str, str] = getattr(meeting, "speaker_names", None) or {}

    md = meeting_store.get_section_md(meeting_id, tid)
    if (not md or not md.strip()) and tid == "tab_general" and meeting.detail:
        md = str(meeting.detail)

    section_name = "General Summary"
    if tid != "tab_general":
        section_name = tid
        for raw in meeting.tabs or []:
            tab = raw if isinstance(raw, dict) else (raw.model_dump() if hasattr(raw, "model_dump") else {})
            if tab.get("tab_id") == tid:
                section_name = (tab.get("name") or tid).strip()
                break
        display = f"{meeting_title} / {section_name}"
    else:
        display = meeting_title

    return display, (md or "").strip(), {str(k): str(v) for k, v in speaker_names.items() if v}


def _meeting_distill_cache_paths(meeting_id: str, tab_id: str = "tab_general"):
    from src.meeting.store import _meeting_dir

    mdir = _meeting_dir(meeting_id)
    safe = (tab_id or "tab_general").replace("/", "_")
    return (
        mdir / f"note_distillation_{safe}.md",
        mdir / f"note_distillation_{safe}.hash",
    )


def invalidate_meeting_distillation(meeting_id: str, tab_id: str | None = None) -> None:
    """Clear cached meeting→note distillation after summary edits."""
    try:
        from src.meeting.store import _meeting_dir

        mdir = _meeting_dir(meeting_id)
        if not mdir.is_dir():
            return
        if tab_id:
            dist_path, hash_path = _meeting_distill_cache_paths(meeting_id, tab_id)
            for p in (dist_path, hash_path):
                if p.exists():
                    p.unlink()
        else:
            for p in mdir.glob("note_distillation*"):
                p.unlink()
        logger.info(
            "Invalidated meeting distillation cache for %s tab=%s",
            meeting_id, tab_id or "*",
        )
    except Exception as e:
        logger.warning("Failed to invalidate meeting distillation for %s: %s", meeting_id, e)


def distill_meeting(meeting_id: str, tab_id: str = "tab_general") -> tuple[str, str]:
    """Distill one meeting summary file (General or a Section) for a note.

    Before LLM: strip refs + priority; **keep [spk:ID]** (live-resolved in the
    note editor; frozen only when the host note is ingested).
    Cache is per (meeting_id, tab_id) under the meeting dir.
    Returns (source_title, distilled_markdown).

    NOTE: Synchronous LLM call — wrap with asyncio.to_thread in async routes.
    """
    import hashlib

    title, raw, speaker_names = build_meeting_tab_distill_source(meeting_id, tab_id)
    source_content = prepare_meeting_summary_for_note(
        raw, speaker_names, resolve_speakers=False,
    )
    if not source_content.strip():
        return title, f"*'{title}' has no summary content yet.*"

    content_hash = hashlib.sha256(source_content.encode("utf-8")).hexdigest()
    dist_path, hash_path = _meeting_distill_cache_paths(meeting_id, tab_id)

    if dist_path.exists() and hash_path.exists():
        try:
            if hash_path.read_text(encoding="utf-8").strip() == content_hash:
                logger.info("Using cached meeting distillation for %s/%s", meeting_id, tab_id)
                return title, dist_path.read_text(encoding="utf-8")
        except OSError:
            pass

    result = _run_distill_llm(source_content, f"meeting:{meeting_id}:{tab_id}")
    try:
        dist_path.parent.mkdir(parents=True, exist_ok=True)
        dist_path.write_text(result, encoding="utf-8")
        hash_path.write_text(content_hash, encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to cache meeting distillation for %s/%s: %s", meeting_id, tab_id, e)

    return title, result


def propagate_forward(source_note_id: str, auto: bool = False) -> list[str]:
    """Re-distill source note content into all notes that reference it.
    If auto=True, also recursively propagate downstream (chain propagation).
    Returns list of updated note IDs."""
    updated = []
    referenced_by = store.get_referenced_by(source_note_id)
    source_note = store.get_note(source_note_id)
    if not source_note:
        return updated

    # Delete existing distillation so it gets regenerated
    store.delete_distillation(source_note_id)

    # Set loading=true on downstream notes' distill blocks before distillation
    for target_id in referenced_by:
        target_content = store.get_content(target_id)
        if target_content is None:
            continue
        loading_content = _set_loading_flag(target_content, source_note_id, True)
        if loading_content != target_content:
            store.save_content(target_id, loading_content)

    for target_id in referenced_by:
        target_content = store.get_content(target_id)
        if target_content is None:
            continue

        # Generate new distillation
        new_distilled = distill_note(source_note_id, target_id)

        # Replace the injection block in target's content
        # Re-read content (may have loading flag set above)
        current_content = store.get_content(target_id) or target_content
        new_content = replace_injection_block(current_content, source_note_id, new_distilled, source_note.title)
        if new_content != target_content:
            store.save_content(target_id, new_content)
            updated.append(target_id)
            logger.info("Updated injection in %s from source %s", target_id, source_note_id)

        # Chain propagation — if this target is also referenced by others
        if auto:
            sub_updated = propagate_forward(target_id, auto=True)
            updated.extend(sub_updated)

    # Touch the source note's updated_at so the frontend can detect completion
    if updated:
        note = store.get_note(source_note_id)
        if note:
            # Save content to update updated_at without changing the actual content
            current_content = store.get_content(source_note_id)
            if current_content is not None:
                store.save_content(source_note_id, current_content)

    return updated


# ── New format: :::distill-block{...} fences ─────────────────

def _set_loading_flag(content: str, source_note_id: str, loading: bool) -> str:
    """Set or clear the loading flag on distill-blocks matching source_note_id."""
    # Match "source":"<source_note_id>" in the distill-block attrs
    source_attr = '"source":"' + re.escape(source_note_id) + '"'
    pattern = re.compile(
        r'(:::distill-block\{[^}]*' + source_attr + r'[^}]*?)("loading"\s*:\s*(?:true|false))',
        re.DOTALL,
    )
    # If there's already a loading key, replace it
    result, count = pattern.subn(r'\g<1>"loading":' + str(loading).lower(), content)
    if count > 0:
        return result
    # No loading key yet — insert it after the opening brace
    pattern2 = re.compile(
        r'(:::distill-block\{[^}]*' + source_attr + r'[^}]*?)\}',
        re.DOTALL,
    )
    result2, count2 = pattern2.subn(r'\g<1>,"loading":' + str(loading).lower() + '}', content)
    if count2 > 0:
        return result2
    return content

def replace_injection_block(content: str, source_note_id: str, new_distilled: str, source_title: str) -> str:
    """Replace the content of a distill-block matching source_note_id.
    Format: :::distill-block{...}\ncontent\n:::
    """
    # Match the full block: opening fence + attributes + content + closing :::
    pattern = re.compile(
        r':::distill-block\{[^}]*"' + re.escape(source_note_id) + r'"[^}]*\}\n'
        r'(?:.*?\n)?'
        r':::',
        re.DOTALL,
    )
    # Build replacement — preserve the original blockId from the matched block
    def _replacer(match: re.Match) -> str:
        original = match.group(0)
        # Extract the blockId from the original attrs
        id_match = re.search(r'"id"\s*:\s*"([^"]*)"', original)
        block_id = id_match.group(1) if id_match else "unknown"
        attrs = json.dumps({
            "id": block_id,
            "source": source_note_id,
            "source-title": source_title,
        }, ensure_ascii=False)[1:-1]  # Remove outer braces
        return f':::distill-block{{{attrs}}}\n{new_distilled}\n:::'

    result, count = pattern.subn(_replacer, content, count=1)
    if count == 0:
        logger.warning("No distill-block found for source %s in content", source_note_id)
        return content
    return result


def parse_injection_blocks(content: str) -> list[dict]:
    """Parse distill blocks from markdown content.
    Supports both formats:
    1. New format: :::distill-block{"id":"...","source":"...","source-title":"..."}\ncontent\n:::
    2. Old format: ```distill-block\n@distill:id:source:title\n---\ncontent\n```
    Returns list of {block_id, source_note_id}."""
    blocks = []

    # Try new format first: :::distill-block{...}
    new_pattern = re.compile(r':::distill-block(\{[^}]+\})\n([\s\S]*?):::', re.DOTALL)
    for match in new_pattern.finditer(content):
        try:
            attrs = json.loads(match.group(1))
            blocks.append({
                "block_id": attrs.get("id", ""),
                "source_note_id": attrs.get("source", "")
            })
        except json.JSONDecodeError:
            continue

    # Also try old format: ```distill-block\n@distill:...
    old_pattern = re.compile(r'```distill-block\n(.*?)\n```', re.DOTALL)
    for match in old_pattern.finditer(content):
        lines = match.group(1).split("\n")
        if not lines or not lines[0].startswith("@distill:"):
            continue
        parts = lines[0].split(":")
        if len(parts) >= 3:
            blocks.append({"block_id": parts[1], "source_note_id": parts[2] or ""})

    return blocks
