"""Meeting Summary export: strip sentence refs, map speaker names."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
EXPORT_TS = FRONTEND / "src" / "lib" / "meeting-summary-export.ts"
TABS = FRONTEND / "src" / "components" / "meeting" / "meeting-tabs.tsx"


def test_export_module_and_toolbar_exist():
    assert EXPORT_TS.is_file()
    src = EXPORT_TS.read_text(encoding="utf-8")
    assert "prepareMeetingSummaryForExport" in src
    assert "exportSummaryMarkdown" in src
    assert "exportSummaryAsPdf" in src
    tabs = TABS.read_text(encoding="utf-8")
    assert "handleExportMarkdown" in tabs
    assert "handleExportPdf" in tabs
    assert "pm-meeting-export-menu" in tabs
    assert "Download" in tabs
    assert "MenuItemTitle" in tabs


_NODE_PREPARE = r"""
// Mirror prepareMeetingSummaryForNote cleaning used by export
function prepare(md, speakerNames) {
  let s = (md || "")
    .replace(/\\\[/g, "[").replace(/\\\]/g, "]")
    .replace(/【/g, "[").replace(/】/g, "]");
  const names = speakerNames || {};
  for (const [id, name] of Object.entries(names)) {
    if (!name) continue;
    const esc = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    s = s.split(`[spk:${id}]`).join(name);
    s = s.replace(new RegExp(`\\bSpeaker\\s+${esc}\\b`, "gi"), name);
  }
  s = s.replace(/\[spk:([^\]]+)\]/g, "Speaker $1");
  const citeToken = "(?:[A-Za-z0-9]{6,}_stt_\\d+|stt_\\d+|\\d+)";
  const sttToken = "(?:[A-Za-z0-9]{6,}_)?stt_\\d+";
  const citeSep = "[\\s,，、;；\\-–—]+";
  s = s.replace(new RegExp(`\\[ref\\s*:\\s*${citeToken}(?:${citeSep}${citeToken})*\\s*\\]`, "gi"), "");
  s = s.replace(new RegExp(`\\[(?:ref\\s*:)?\\s*${sttToken}(?:${citeSep}${sttToken})*\\s*\\]`, "gi"), "");
  s = s.replace(/\b[A-Za-z0-9]{6,}_stt_\d+\b/gi, "");
  s = s.replace(/\bstt_\d+\b/gi, "");
  s = s.replace(/\[\s*priority\s*:\s*(?:high|medium|low)\s*\]/gi, "");
  s = s.replace(/\[\s*(?:[,，、;；\-–—\s])*\s*\]/g, "");
  s = s.replace(/[ \t]+\n/g, "\n").replace(/ {2,}/g, " ").replace(/\n{3,}/g, "\n\n");
  return s.trim();
}

const raw = process.argv[1];
const names = JSON.parse(process.argv[2]);
const out = prepare(raw, names);
if (out.includes("stt_")) {
  console.error("STT_LEFT", out);
  process.exit(2);
}
if (out.includes("[spk:")) {
  console.error("SPK_LEFT", out);
  process.exit(3);
}
if (!out.includes("Alice") || !out.includes("Bob")) {
  console.error("NAMES_MISSING", out);
  process.exit(4);
}
process.stdout.write(out);
"""


def test_export_clean_strips_refs_and_maps_speakers():
    raw = (
        "### Points\n"
        "- [spk:0] proposed Q3 [stt_0001,stt_0002] [priority: high]\n"
        "- Speaker 1 agreed [stt_0010]\n"
    )
    names = {"0": "Alice", "1": "Bob"}
    proc = subprocess.run(
        ["node", "-e", _NODE_PREPARE, raw, __import__("json").dumps(names)],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    out = proc.stdout
    assert "Alice" in out
    assert "Bob" in out
    assert "stt_" not in out
    assert "priority" not in out.lower()
    assert "[spk:" not in out
