"""Visible Markdown text with server-owned Unicode source ranges.

Uses markdown-it-py already present in the locked runtime (via Rich). Parser
rules retain their input positions; matching never searches URLs/attributes
for text that happened to appear in a rendered label.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.parser_inline import ParserInline
from markdown_it.rules_inline.state_inline import StateInline

from src.reports.blocks import markdown_lines

_CITATION = re.compile(r'\[cite:[^\]\r\n]*\]')
MAX_LITERAL_EDITS = 2000


class _State(StateInline):
    pending_start = None

    def pushPending(self):
        start = self.pending_start
        token = super().pushPending()
        token.meta['source_span'] = (start if start is not None else self.pos - len(token.content), self.pos)
        self.pending_start = None
        return token

    def push(self, ttype, tag, nesting):
        token = super().push(ttype, tag, nesting)
        token.meta['source_span'] = (self.pos, None)
        return token


class _Inline(ParserInline):
    def tokenize(self, state):
        rules = self.ruler.getRules('')
        while state.pos < state.posMax:
            start, count = state.pos, len(state.tokens)
            if not state.pending:
                state.pending_start = start
            ok = False
            if state.level < state.md.options['maxNesting']:
                for rule in rules:
                    if rule(state, False):
                        ok = True
                        break
            if not ok:
                state.pending += state.src[state.pos]
                state.pos += 1
            for token in state.tokens[count:]:
                a, b = token.meta.get('source_span', (start, None))
                if b is None:
                    token.meta['source_span'] = (a, state.pos)
            if not state.pending:
                state.pending_start = None
        if state.pending:
            state.pushPending()

    def parse(self, src, md, env, tokens):
        state = _State(src, md, env, tokens)
        self.tokenize(state)
        for rule in self.ruler2.getRules(''):
            rule(state)
        return state.tokens


def _citation(state, silent):
    match = _CITATION.match(state.src, state.pos)
    if match is None:
        return False
    if not silent:
        state.push('report_citation', '', 0)
    state.pos = match.end()
    return True


def _parser():
    parser = MarkdownIt('commonmark', {'html': True}).enable(['table', 'strikethrough'])
    parser.inline = _Inline()
    parser.inline.ruler.before('link', 'report_citation', _citation)
    # Joining text tokens would discard their individual source coordinates.
    parser.inline.ruler2.disable('fragments_join')
    parser.core.ruler.disable('text_join')
    return parser


def _line_positions(source):
    lines, offsets, at = markdown_lines(source, keepends=True), [], 0
    for line in lines:
        offsets.append(at)
        at += len(line)
    return lines, offsets


def _content_positions(content, line_map, lines, offsets, cursors):
    """Map stripped heading/list/table content back to its original lines."""
    result = []
    row = line_map[0]
    for part in markdown_lines(content, keepends=True):
        value = part.rstrip('\r\n')
        if row >= line_map[1] or row >= len(lines):
            raise ValueError('Report text cannot be mapped to its source lines')
        raw = lines[row].rstrip('\r\n')
        lo = cursors.get(row, 0)
        at = raw.find(value, lo)
        if at >= 0:
            points = [(offsets[row] + at + i, offsets[row] + at + i + 1) for i in range(len(value))]
        else:
            # GFM table parsing unescapes a cell's escaped vertical bars.
            normalized, points, i = '', [], 0
            while i < len(raw):
                end = i + 2 if raw[i:i + 2] == '\\|' else i + 1
                normalized += raw[end - 1]
                points.append((offsets[row] + i, offsets[row] + end))
                i = end
            candidates = [i for i, p in enumerate(points) if p[0] >= offsets[row] + lo]
            at = normalized.find(value, candidates[0] if candidates else len(normalized))
            if at < 0:
                raise ValueError('Report text mapping is ambiguous; use an explicit Markdown block edit')
            points = points[at:at + len(value)]
        result.extend(points)
        if points:
            cursors[row] = points[-1][1] - offsets[row]
        if part.endswith('\n'):
            newline = offsets[row] + len(raw)
            result.append((newline, newline + len(lines[row][len(raw):])))
        row += 1
    return result


def _leaf_points(raw, value, start, end, kind, used):
    fragment = raw[start:end]
    if kind == 'entity':
        return [(start, end)] * len(value)
    if kind == 'escape' and fragment.startswith('\\') and value == fragment[1:]:
        return [(start, end)] * len(value)
    at = fragment.find(value, used.get((start, end), 0))
    if at < 0:
        at = fragment.find(value)
    if at < 0:
        raise ValueError('Report text token does not match its source')
    used[start, end] = at + len(value)
    return [(start + at + i, start + at + i + 1) for i in range(len(value))]


def _inline_text(raw, tokens, positions):
    text, points, used = '', [], {}
    auto_link = False
    for token in tokens:
        start, end = token.meta.get('source_span', (0, 0))
        if token.type == 'link_open':
            auto_link = token.markup == 'autolink'
        elif token.type == 'link_close':
            auto_link = False
        elif token.type in {'text', 'text_special'} and token.content:
            value = raw[start + 1:end - 1] if auto_link else token.content
            mapped = [(i, i + 1) for i in range(start + 1, end - 1)] if auto_link else _leaf_points(raw, value, start, end, token.info, used)
            text += value
            points.extend((*positions[a], 'url' if auto_link else 'text') if b == a + 1 else (positions[a][0], positions[b - 1][1], 'text') for a, b in mapped)
        elif token.type == 'code_inline':
            lo, hi = start + len(token.markup), end - len(token.markup)
            content = raw[lo:hi].replace('\n', ' ')
            if content.startswith(' ') and content.endswith(' ') and content.strip():
                lo += 1
                hi -= 1
            text += token.content
            points.extend((*positions[i], 'code') for i in range(lo, hi))
        elif token.type in {'softbreak', 'hardbreak'}:
            text += '\n'
            points.append((*positions[start], 'text'))
        elif token.type == 'image' and token.children:
            lo = start + 2
            child_text, child_points = _inline_text(token.content, token.children, positions[lo:lo + len(token.content)])
            text += child_text
            points.extend(child_points)
    if len(text) != len(points):
        raise ValueError('Report text source map is incomplete')
    # GFM turns bare addresses into links as well. Their destinations remain
    # protected even when they were parsed as plain CommonMark text.
    for match in re.finditer(r'(?:https?://|www\.|mailto:)[^\s<>()]+', text, re.IGNORECASE):
        for i in range(match.start(), match.end()):
            points[i] = (*points[i][:2], 'url')
    return text, points


class _SvgText(HTMLParser):
    def __init__(self, source, positions):
        super().__init__(convert_charrefs=False)
        self.positions = positions
        self.lines, self.offsets = _line_positions(source)
        self.stack, self.units, self.current = [], [], None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        tag = tag.split(':')[-1]
        self.stack.append(tag)
        if tag == 'text':
            self.current = ['', []]
            self.units.append(self.current)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        tag = tag.split(':')[-1]
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]
        if tag == 'text':
            self.current = None

    def _data(self, raw, value):
        if self.current is None or any(t in self.stack for t in ('script', 'style')):
            return
        row, column = self.getpos()
        at = self.offsets[row - 1] + column
        self.current[0] += value
        if raw == value:
            self.current[1].extend((*self.positions[i], 'svg') for i in range(at, at + len(raw)))
        else:
            self.current[1].extend((self.positions[at][0], self.positions[at + len(raw) - 1][1], 'svg') for _ in value)

    def handle_data(self, data):
        self._data(data, data)

    def handle_entityref(self, name):
        self._data('&' + name + ';', html.unescape('&' + name + ';'))

    def handle_charref(self, name):
        self._data('&#' + name + ';', html.unescape('&#' + name + ';'))


def text_units(source):
    parser = _parser()
    lines, offsets = _line_positions(source)
    units, cursors = [], {}
    tokens = parser.parse(source)
    for index, token in enumerate(tokens):
        if token.type == 'inline' and token.map:
            positions = _content_positions(token.content, token.map, lines, offsets, cursors)
            text, points = _inline_text(token.content, token.children or [], positions)
            task = re.match(r'^\[[ xX]\][ \t]+', token.content)
            if task and index >= 2 and tokens[index - 2].type == 'list_item_open' and tokens[index - 1].type == 'paragraph_open':
                # remark-gfm renders this prefix as a checkbox, which has no
                # selectable text and does not belong in selection offsets.
                boundary = positions[task.end() - 1][1]
                kept = [(char, point) for char, point in zip(text, points) if point[0] >= boundary]
                text, points = ''.join(char for char, _ in kept), [point for _, point in kept]
            units.append((text, points))
        elif token.type in {'fence', 'code_block'} and token.map:
            start = token.map[0] + (1 if token.type == 'fence' else 0)
            positions = _content_positions(token.content, [start, token.map[1]], lines, offsets, cursors)
            if token.info.strip().lower() == 'svg':
                units.extend((text, points) for text, points in _SvgText(token.content, positions).units)
            else:
                units.append((token.content, [(*p, 'code_block') for p in positions]))
    return units


def visible_text(source):
    return '\n'.join(text for text, _ in text_units(source))


def _normalized_points(units):
    text, points = '', []
    for value, positions in units:
        for char, point in zip(value, positions):
            if not char.isspace() and char != '\ufeff':
                text += char
                points.append(point)
    return text, points


def _ranges(source, points):
    ranges = []
    for start, end, _ in points:
        if ranges and start >= ranges[-1]['start'] and (start <= ranges[-1]['end'] or not re.sub(r'[\s\ufeff]+', '', source[ranges[-1]['end']:start])):
            ranges[-1]['end'] = max(ranges[-1]['end'], end)
        else:
            ranges.append({'start': start, 'end': end})
    return [{**r, 'before': source[r['start']:r['end']]} for r in ranges]


def selection_anchor(source, anchor):
    lines, offsets = _line_positions(source)
    first, last = anchor['start_line'] - 1, anchor['end_line']
    lo, hi = offsets[first], offsets[last] if last < len(lines) else len(source)
    units = text_units(source)
    local = [(text, positions) for text, positions in units]
    normalized, points = _normalized_points(local)
    indices = [i for i, p in enumerate(points) if p[0] >= lo and p[1] <= hi]
    if not indices:
        raise ValueError('Selection has no mapped report text')
    local_text = ''.join(normalized[i] for i in indices)
    quote = re.sub(r'[\s\ufeff]+', '', anchor['quote'])
    start, end = anchor.get('selection_start'), anchor.get('selection_end')
    if start is None and end is None:
        prefix = re.sub(r'[\s\ufeff]+', '', anchor.get('prefix', ''))
        suffix = re.sub(r'[\s\ufeff]+', '', anchor.get('suffix', ''))
        needle = prefix + quote + suffix
        at = local_text.find(needle)
        if at < 0 or local_text.find(needle, at + 1) >= 0:
            raise ValueError('Comment anchor must identify a unique passage in this revision')
        start, end = at + len(prefix), at + len(prefix) + len(quote)
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(local_text) or local_text[start:end] != quote:
        raise ValueError('Exact selection does not match the selected report text')
    chosen = [points[indices[i]] for i in range(start, end)]
    global_start, global_end = indices[start], indices[end - 1] + 1
    if (global_start and points[global_start - 1][:2] == chosen[0][:2]) or (global_end < len(points) and points[global_end][:2] == chosen[-1][:2]):
        raise ValueError('Select the complete encoded character before replacing it')
    return {'selection_start': start, 'selection_end': end, 'source_ranges': _ranges(source, chosen)}, (normalized, global_start, global_end)


def _escape(value, kind):
    if kind == 'svg':
        return html.escape(value, quote=False)
    if kind in {'code', 'code_block'}:
        return value
    value = html.escape(value, quote=False)
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', value)


def literal_edits(report, operation, comments):
    """Expand a literal intent to exact source edits, or fail atomically."""
    from src.reports.blocks import apply_resolved
    source = report['markdown']
    after = operation.get('after')
    if not isinstance(after, str):
        raise TypeError('Provide replacement text')
    mode = operation['mode']
    matches = []
    title_edits = []
    if mode == 'selection':
        comment = next((c for c in comments if c['id'] == operation.get('comment_id')), None)
        if comment is None or comment['revision'] != report['revision'] or comment.get('anchor_stale'):
            raise ValueError('Selection replacement requires a current submitted comment')
        if comment['anchor'].get('kind') == 'block':
            raise ValueError('Use block edits to revise a selected figure or whole Markdown block')
        selected, (normalized, start, end) = selection_anchor(source, comment['anchor'])
        matches.append(selected['source_ranges'])
        expected = normalized[:start] + re.sub(r'[\s\ufeff]+', '', after) + normalized[end:]
    else:
        before = operation.get('before')
        if not isinstance(before, str) or not before or before == after:
            raise ValueError('Provide distinct nonempty original and replacement text')
        units = text_units(source)
        expected_parts = []
        for value, points in units:
            cursor, replacements = 0, []
            while (at := value.find(before, cursor)) >= 0:
                end = at + len(before)
                picked = points[at:end]
                if picked and not any(p[2] in {'url', 'code_block'} for p in picked):
                    if (at and points[at - 1][:2] == picked[0][:2]) or (end < len(points) and points[end][:2] == picked[-1][:2]):
                        raise ValueError('Replacement would split an encoded character')
                    matches.append(_ranges(source, picked))
                    replacements.append((at, end))
                cursor = end
            for a, b in reversed(replacements):
                value = value[:a] + after + value[b:]
            expected_parts.append(value)
        expected = re.sub(r'[\s\ufeff]+', '', ''.join(expected_parts))
        title, cursor = report.get('title', ''), 0
        while (at := title.find(before, cursor)) >= 0:
            title_edits.append({'field': 'title', 'start': at, 'end': at + len(before), 'before': before, 'after': after})
            cursor = at + len(before)
    count = len(matches) + len(title_edits)
    if not count or (operation.get('expected_count') is not None and operation['expected_count'] != count):
        raise ValueError(f'Visible replacement matched {count} occurrences; read the current report and check the intended scope')
    raw = []
    all_points = [p for _, positions in text_units(source) for p in positions]
    kinds = {p[0]: p[2] for p in all_points}
    for match in matches:
        first = match[0]
        kind = kinds.get(first['start'], 'text')
        for index, span in enumerate(match):
            raw.append({**span, 'after': _escape(after, kind) if index == 0 else ''})
    if len(raw) + len(title_edits) > MAX_LITERAL_EDITS:
        raise ValueError('This replacement exceeds 2000 source edits; use verified block edits for large rewrites')
    from src.reports.models import MAX_MARKDOWN
    if len(source) + sum(len(e['after']) - len(e['before']) for e in raw) > MAX_MARKDOWN:
        raise ValueError('Replacement exceeds the report content limit')
    body = apply_resolved(source, raw)
    actual, _ = _normalized_points(text_units(body))
    if actual != expected:
        raise ValueError('Literal replacement cannot preserve the Markdown structure; read the relevant blocks and submit explicit block edits')
    if body == source and not title_edits:
        raise ValueError('Edits must change the report')
    return raw + title_edits, count
