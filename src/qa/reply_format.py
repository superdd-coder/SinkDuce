"""Only inline emphasis is supported in replies; block syntax stays ordinary text."""

import re
from dataclasses import dataclass
from html import unescape
from xml.etree import ElementTree as ET

import markdown
from markdown.util import Registry
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont


@dataclass(frozen=True)
class ReplyRun:
    text: str
    bold: bool = False
    italic: bool = False


def reply_runs(body: str) -> list[ReplyRun]:
    # Running only inline processors keeps paragraphs/newlines intact and prevents
    # headings, links, HTML, tables and code from becoming active content.
    md = markdown.Markdown()
    patterns = Registry()
    patterns.register(md.inlinePatterns["escape"], "escape", 180)
    patterns.register(md.inlinePatterns["em_strong"], "em_strong", 60)
    # Markdown 3.11 handles literal markers and underscores in the shared delimiter processor.
    for name, priority in (("not_strong", 70), ("em_strong2", 50)):
        if name in md.inlinePatterns:
            patterns.register(md.inlinePatterns[name], name, priority)
    md.inlinePatterns = patterns
    md.treeprocessors["inline"].inlinePatterns = patterns
    root = ET.Element("div")
    paragraph = ET.SubElement(root, "p")
    paragraph.text = re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1\n", body)
    md.treeprocessors["inline"].run(root)
    md.treeprocessors["unescape"].run(root)
    runs = []

    def append(text, bold, italic):
        if not text:
            return
        text = unescape(text)
        if runs and (runs[-1].bold, runs[-1].italic) == (bold, italic):
            runs[-1] = ReplyRun(runs[-1].text + text, bold, italic)
        else:
            runs.append(ReplyRun(text, bold, italic))

    def visit(node, bold=False, italic=False):
        bold, italic = bold or node.tag == "strong", italic or node.tag == "em"
        append(node.text, bold, italic)
        for child in node:
            visit(child, bold, italic)
            append(child.tail, bold, italic)

    visit(paragraph)
    return runs


def rich_reply(body: str) -> str | CellRichText:
    runs = reply_runs(body)
    if not any(run.bold or run.italic for run in runs):
        return "".join(run.text for run in runs)
    return CellRichText([TextBlock(InlineFont(b=run.bold, i=run.italic), run.text) for run in runs])


def rich_to_markdown(value: CellRichText) -> str:
    """Keep supported marks when the recipient returns an exported workbook."""
    result, active = [], []
    for part in value:
        text = part.text if isinstance(part, TextBlock) else str(part)
        if not text:
            continue
        marks = []
        if isinstance(part, TextBlock):
            if part.font.b:
                marks.append("**")
            if part.font.i:
                marks.append("*")
        # Whitespace at a mark boundary belongs outside the Markdown delimiters.
        for fragment in re.split(r"(^\s+|\s+$)", text):
            if not fragment:
                continue
            desired = [] if fragment.isspace() else marks
            common = 0
            while common < min(len(active), len(desired)) and active[common] == desired[common]:
                common += 1
            result.extend(reversed(active[common:]))
            result.extend(desired[common:])
            result.append(re.sub(r"([\\*_])", r"\\\1", fragment))
            active = desired
    result.extend(reversed(active))
    return "".join(result)
