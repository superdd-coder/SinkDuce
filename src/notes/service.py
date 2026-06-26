"""LLM-powered distillation and propagation logic for Notes."""

from __future__ import annotations

import json
import logging
import re

from src.notes import store

logger = logging.getLogger("notes.service")


def expand_distill_blocks(collection: str, content: str, depth: int = 0, max_depth: int = 3) -> str:
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
            source_content = store.get_content(collection, source_id)
            if source_content:
                # Recursively expand nested blocks
                expanded_source = expand_distill_blocks(collection, source_content, depth + 1, max_depth)
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
    """Get the default LLM provider for distillation."""
    from src.config import get_config
    from src.providers.llm import create_llm_for_provider

    cfg = get_config()
    if cfg.llm.providers:
        default_p = next((p for p in cfg.llm.providers if p.is_default), cfg.llm.providers[0])
        return create_llm_for_provider(default_p)
    from src.services import services
    return services.llm


def distill_note(collection: str, source_note_id: str, target_note_id: str) -> str:
    """Distill source note content for embedding into target note.
    Uses cache if available. Cache is keyed by (collection, source_note_id) —
    distillation result depends only on the source, not the target.
    Returns the distilled markdown.

    NOTE: This function is synchronous (calls LLM via blocking generate()).
    Callers in async contexts must wrap with asyncio.to_thread()."""
    # Check cache (single-key: collection + source_note_id)
    cached = store.get_distillation(collection, source_note_id)
    if cached is not None:
        logger.info("Using cached distillation for %s", source_note_id)
        return cached

    # Get source content and expand nested distill blocks
    source_content = store.get_content(collection, source_note_id)
    if not source_content or not source_content.strip():
        source_note = store.get_note(collection, source_note_id)
        title = source_note.title if source_note else source_note_id
        return f"*Note '{title}' is empty.*"

    # Expand nested distill blocks so their content is included
    source_content = expand_distill_blocks(collection, source_content)

    # Call LLM
    logger.info("Generating distillation for %s (%d chars)", source_note_id, len(source_content))
    llm = get_llm()
    system_prompt, user_prompt = get_distillation_prompt(source_content)
    result = llm.generate(user_prompt, system=system_prompt, max_tokens=4096, thinking=False)

    # Strip any preamble the LLM might add despite instructions
    result = result.strip()
    for prefix in ["Here is", "Here's", "Distillation:", "Distilled:", "Summary:"]:
        if result.lower().startswith(prefix.lower()):
            # Find the first newline after the prefix
            nl = result.find("\n")
            if nl != -1:
                result = result[nl + 1:].strip()

    # Cache the result (single-key)
    store.save_distillation(collection, source_note_id, result)
    return result


def propagate_forward(collection: str, source_note_id: str, auto: bool = False) -> list[str]:
    """Re-distill source note content into all notes that reference it.
    If auto=True, also recursively propagate downstream (chain propagation).
    Returns list of updated note IDs."""
    updated = []
    referenced_by = store.get_referenced_by(collection, source_note_id)
    source_note = store.get_note(collection, source_note_id)
    if not source_note:
        return updated

    # Delete existing distillation so it gets regenerated
    store.delete_distillation(collection, source_note_id)

    # Set loading=true on downstream notes' distill blocks before distillation
    for target_id in referenced_by:
        target_content = store.get_content(collection, target_id)
        if target_content is None:
            continue
        loading_content = _set_loading_flag(target_content, source_note_id, True)
        if loading_content != target_content:
            store.save_content(collection, target_id, loading_content)

    for target_id in referenced_by:
        target_content = store.get_content(collection, target_id)
        if target_content is None:
            continue

        # Generate new distillation
        new_distilled = distill_note(collection, source_note_id, target_id)

        # Replace the injection block in target's content
        # Re-read content (may have loading flag set above)
        current_content = store.get_content(collection, target_id) or target_content
        new_content = replace_injection_block(current_content, source_note_id, new_distilled, source_note.title)
        if new_content != target_content:
            store.save_content(collection, target_id, new_content)
            updated.append(target_id)
            logger.info("Updated injection in %s from source %s", target_id, source_note_id)

        # Chain propagation — if this target is also referenced by others
        if auto:
            sub_updated = propagate_forward(collection, target_id, auto=True)
            updated.extend(sub_updated)

    # Touch the source note's updated_at so the frontend can detect completion
    if updated:
        note = store.get_note(collection, source_note_id)
        if note:
            # Save content to update updated_at without changing the actual content
            current_content = store.get_content(collection, source_note_id)
            if current_content is not None:
                store.save_content(collection, source_note_id, current_content)

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
        r'.*?\n'
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
    new_pattern = re.compile(r':::distill-block(\{[^}]+\})\n([\s\S]*?)\n:::', re.DOTALL)
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
