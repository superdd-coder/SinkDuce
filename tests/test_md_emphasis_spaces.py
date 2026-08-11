"""Regression: bold/italic interior-space trim must not glue adjacent words.

Bug history
-----------
A "trim interior spaces" regex used in Meeting Summary / streaming markdown:

    r"\\*\\*\\s+([^*]+?)\\s*\\*\\*"

matches the *gap between two bold spans*:

    "**capex** does **not**"  →  treats ``** does **`` as one pair
                              →  "**capex**does**not**"

so exterior word spaces around ``**…**`` disappear. Edit mode (TipTap marks)
looked fine; read mode (normalize → parse) looked sticky.

Safe approach: match a complete pair first, then strip only leading/trailing
spaces *inside* that pair (see frontend/src/lib/md-emphasis.ts).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"

# The historical bug pattern — must not reappear in product code.
_BROKEN_BOLD_TRIM = re.compile(
    r"\\\*\\\*\\s\+\(\[\^\*\]\+\?\)\\s\*\\\*\\\*"
)


def test_broken_interior_trim_regex_not_in_frontend_src():
    offenders: list[str] = []
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        if _BROKEN_BOLD_TRIM.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], (
        "Broken bold-interior-space regex reintroduced (eats spaces between "
        f"adjacent **spans**):\n  " + "\n  ".join(offenders)
    )


def test_md_emphasis_module_exports_safe_trim():
    path = FRONTEND_SRC / "lib" / "md-emphasis.ts"
    assert path.is_file()
    src = path.read_text(encoding="utf-8")
    assert "trimEmphasisInteriorSpaces" in src
    # Pair-first strategy: **([^*]+)** then strip ends — not **\\s+…\\s**
    assert r"**([^*]+)**" in src or r"\*\*([^*]+)\*\*" in src


# Behavioral checks via Node (same algorithm as md-emphasis.ts), no TS build needed.
_NODE_TRIM = r"""
function trimEmphasisInteriorSpaces(md) {
  return (md || "")
    .replace(/\*\*([^*]+)\*\*/g, (_, inner) => `**${inner.replace(/^[ \t]+|[ \t]+$/g, "")}**`)
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, (_, inner) => `*${inner.replace(/^[ \t]+|[ \t]+$/g, "")}*`);
}
const cases = JSON.parse(process.argv[1]);
const out = {};
for (const [k, v] of Object.entries(cases)) out[k] = trimEmphasisInteriorSpaces(v);
process.stdout.write(JSON.stringify(out));
"""


def _run_trim(cases: dict[str, str]) -> dict[str, str]:
    import json

    proc = subprocess.run(
        ["node", "-e", _NODE_TRIM, json.dumps(cases)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"node trim failed: {proc.stderr or proc.stdout}")
    return json.loads(proc.stdout)


def test_trim_preserves_spaces_between_adjacent_bold():
    import json

    cases = {
        "double": "the **capex** does **not** include",
        "single": "does **not** include pre-treatment",
        "phrase": "about **replacement costs** for the membrane",
        "lead": "**Polyplastics lab test results** are not good",
        "interior": "see **  both  ** sides",
        "italic": "a * spaced * word and ** bold ** here",
    }
    out = _run_trim(cases)
    assert out["double"] == "the **capex** does **not** include"
    assert out["single"] == "does **not** include pre-treatment"
    assert out["phrase"] == "about **replacement costs** for the membrane"
    assert out["lead"] == "**Polyplastics lab test results** are not good"
    assert out["interior"] == "see **both** sides"
    assert out["italic"] == "a *spaced* word and **bold** here"


def test_prepare_pipeline_keeps_bold_spaces_on_synthetic_summary():
    """Full prepare-shaped pipeline + markdown-it must keep spaces around <strong>."""
    # Synthetic doc (do not depend on mutable meeting data on disk)
    raw = (
        "### Points\n"
        "- The capex does **not** include pre-treatment [stt_0008,stt_0009]\n\n"
        "He clarified that the **capex** does **not** include equipment; "
        "[spk:0] agreed. [stt_0010]\n\n"
        "about **replacement costs** for membranes.\n"
        "**Polyplastics lab test results** are not good [priority: high]\n"
    )

    script = r"""
import MarkdownIt from "markdown-it";

function trimEmphasisInteriorSpaces(md) {
  return (md || "")
    .replace(/\*\*([^*]+)\*\*/g, (_, inner) => `**${inner.replace(/^[ \t]+|[ \t]+$/g, "")}**`)
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, (_, inner) => `*${inner.replace(/^[ \t]+|[ \t]+$/g, "")}*`);
}

function prepare(md, names) {
  let s = (md || "")
    .replace(/\\\[/g, "[").replace(/\\\]/g, "]")
    .replace(/\\~/g, "~").replace(/\\_/g, "_").replace(/\\\*/g, "*")
    .replace(/【/g, "[").replace(/】/g, "]");
  s = trimEmphasisInteriorSpaces(s);
  s = s.replace(/\[spk:(\d+)\]/g, (_, id) => names[id] ?? `Speaker ${id}`);
  s = s.replace(
    /\[(?:ref:)?\s*(stt_\d+(?:\s*,\s*stt_\d+)*)\s*\]/gi,
    (_m, ids) => `<span data-meeting-ref="${ids}"></span>`,
  );
  s = s.replace(
    /\[\s*priority\s*:\s*(high|medium|low)\s*\]/gi,
    (_m, level) => `<span data-meeting-pri="${String(level).toLowerCase()}"></span>`,
  );
  return s;
}

const raw = process.argv[1];
const prepared = prepare(raw, { "0": "A", "1": "B" });
if (!prepared.includes("the **capex** does **not**")) {
  console.error("PREPARED_BAD", prepared);
  process.exit(2);
}
const html = new MarkdownIt({ html: true }).render(prepared);
const re = /<strong>([^<]*)<\/strong>/g;
let m, bad = 0;
while ((m = re.exec(html)) !== null) {
  const b = html[m.index - 1], a = html[m.index + m[0].length];
  const beforeOk = !b || /[\s>\(\[\{“"']/.test(b);
  const afterOk = !a || /[\s<\.,;:!?\}\)\]”"']/.test(a);
  if (!beforeOk || !afterOk) {
    bad++;
    console.error("STRONG_GLUE", JSON.stringify(m[1]), JSON.stringify(b), JSON.stringify(a));
  }
}
if (bad) process.exit(3);
process.stdout.write("ok");
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script, raw],
        cwd=str(ROOT / "frontend"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"prepare+markdown-it still glues bold (code={proc.returncode}):\n"
        f"{proc.stderr or proc.stdout}"
    )
    assert proc.stdout.strip() == "ok"
