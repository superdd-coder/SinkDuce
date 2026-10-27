"""Unit tests for hot-words CSV / Excel import parsing."""

from __future__ import annotations

from src.hot_words.import_util import (
    build_template_csv,
    build_template_xlsx,
    parse_csv_bytes,
    parse_hot_words_file,
    parse_xlsx_bytes,
)


def test_parse_csv_with_headers():
    raw = b"text,weight,lang\nFoo,5,zh\nBar,3,en\n"
    words, name, desc = parse_csv_bytes(raw)
    assert name == ""
    assert desc == ""
    assert len(words) == 2
    assert words[0].text == "Foo"
    assert words[0].weight == 5
    assert words[0].lang == "zh"
    assert words[1].text == "Bar"
    assert words[1].weight == 3
    assert words[1].lang == "en"


def test_parse_csv_header_aliases_and_defaults():
    raw = "word,W,language\nAlpha,,\n".encode("utf-8")
    words, _, _ = parse_csv_bytes(raw)
    assert len(words) == 1
    assert words[0].text == "Alpha"
    assert words[0].weight == 4
    assert words[0].lang == ""


def test_parse_csv_skips_empty_text():
    raw = b"text,weight,lang\n,4,zh\nKeep,4,en\n"
    words, _, _ = parse_csv_bytes(raw)
    assert [w.text for w in words] == ["Keep"]


def test_parse_csv_weight_clamped():
    raw = b"text,weight,lang\nA,99,zh\nB,0,en\n"
    words, _, _ = parse_csv_bytes(raw)
    assert words[0].weight == 10
    assert words[1].weight == 1


def test_parse_csv_with_name_column():
    raw = b"text,weight,lang,name\nTerm,4,zh,My Lib\n"
    words, name, _ = parse_csv_bytes(raw)
    assert name == "My Lib"
    assert words[0].text == "Term"


def test_template_csv_roundtrip():
    data = build_template_csv()
    words, _, _ = parse_csv_bytes(data)
    assert len(words) >= 2
    assert words[0].text == "ExampleTerm"


def test_template_xlsx_roundtrip():
    data = build_template_xlsx()
    words, _, _ = parse_xlsx_bytes(data)
    assert len(words) >= 2
    assert words[0].text == "ExampleTerm"


def test_parse_hot_words_file_dispatch():
    csv_data = build_template_csv()
    words, _, _ = parse_hot_words_file("demo.csv", csv_data)
    assert words

    xlsx_data = build_template_xlsx()
    words2, _, _ = parse_hot_words_file("demo.xlsx", xlsx_data)
    assert words2


def test_legacy_xls_rejected():
    try:
        parse_hot_words_file("old.xls", b"not-real")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "xls" in str(e).lower()


def test_export_xlsx_roundtrip():
    from src.hot_words.import_util import build_export_xlsx
    from src.hot_words.models import HotWordItem

    words = [
        HotWordItem(text="Alpha", weight=5, lang="zh"),
        HotWordItem(text="Beta", weight=3, lang="en"),
    ]
    data = build_export_xlsx(words, name="My Lib", description="desc")
    parsed, name, desc = parse_xlsx_bytes(data)
    assert name == "My Lib"
    assert desc == "desc"
    assert len(parsed) == 2
    assert parsed[0].text == "Alpha"
    assert parsed[0].weight == 5
    assert parsed[1].text == "Beta"
