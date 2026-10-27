from __future__ import annotations

import json
from pathlib import Path

from src.parsers.base import DocumentParser, ParsedDocument

MAX_ARRAY_ITEMS = 1000


def _flatten(obj: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested dict into dot-separated key=value pairs."""
    items: dict[str, str] = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.update(_flatten(v, key))
        elif isinstance(v, list):
            items[key] = json.dumps(v, ensure_ascii=False)
        else:
            items[key] = str(v) if v is not None else ""
    return items


def _objects_to_table(objects: list[dict]) -> str:
    """Convert list of dicts to markdown table."""
    if not objects:
        return ""
    # Collect all keys preserving order from first object
    keys = list(objects[0].keys())
    for obj in objects[1:]:
        for k in obj:
            if k not in keys:
                keys.append(k)
    header = "| " + " | ".join(keys) + " |"
    sep = "| " + " | ".join("---" for _ in keys) + " |"
    rows = []
    for obj in objects:
        cells = []
        for k in keys:
            val = obj.get(k, "")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            cells.append(str(val) if val is not None else "")
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep] + rows)


def _format_json(data: object) -> str:
    """Format parsed JSON data into readable text."""
    if isinstance(data, list):
        if len(data) > MAX_ARRAY_ITEMS:
            data = data[:MAX_ARRAY_ITEMS]
            truncated = True
        else:
            truncated = False

        if data and isinstance(data[0], dict):
            table = _objects_to_table(data)
            if truncated:
                table += f"\n\n> Showing first {MAX_ARRAY_ITEMS} items"
            return table

        # Array of primitives / mixed types
        lines = [json.dumps(item, ensure_ascii=False) for item in data]
        content = "\n".join(lines)
        if truncated:
            content += f"\n\n> Showing first {MAX_ARRAY_ITEMS} items"
        return content

    if isinstance(data, dict):
        flat = _flatten(data)
        return "\n".join(f"{k}={v}" for k, v in flat.items())

    return json.dumps(data, indent=2, ensure_ascii=False)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


class JSONParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        text = _read_text(path)
        data = json.loads(text)
        content = _format_json(data)
        return ParsedDocument(
            content=content,
            metadata={"keys": len(data) if isinstance(data, dict) else None},
            source_path=str(path),
            file_type="json",
        )


class JSONLParser(DocumentParser):
    """Newline-delimited JSON parser."""

    def parse(self, path: Path) -> ParsedDocument:
        objects: list[dict] = []
        for i, line in enumerate(_read_text(path).splitlines()):
            if i >= MAX_ARRAY_ITEMS:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # Skip malformed lines
            if isinstance(obj, dict):
                objects.append(obj)
            else:
                objects.append({"value": obj})

        if objects and isinstance(objects[0], dict):
            content = _objects_to_table(objects)
        else:
            content = "\n".join(json.dumps(o, ensure_ascii=False) for o in objects)

        return ParsedDocument(
            content=content,
            metadata={"lines": len(objects)},
            source_path=str(path),
            file_type="json",
        )
