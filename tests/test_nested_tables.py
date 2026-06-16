"""Unit tests for extract_nested_tables (nested-table separation)."""
from __future__ import annotations

from md_converter.nested_tables import extract_nested_tables, serialize_nested_table


def test_single_nested_table_separated():
    md = (
        "| 구분 | 세부내용 |\n"
        "| --- | --- |\n"
        "| 본인부담 | [[NT:항목|금액;외래|1,000원]] |\n"
        "| 수가 | 5,000원 |"
    )
    out = extract_nested_tables(md)
    # marker gone, replaced by a readable reference
    assert "[[NT:" not in out
    assert "→ 표 1" in out
    # standalone table emitted after the parent block
    assert "**[표 1]**" in out
    assert "| 항목 | 금액 |" in out
    assert "| 외래 | 1,000원 |" in out
    assert out.index("**[표 1]**") > out.index("본인부담")


def test_two_nested_tables_in_one_parent_block():
    md = (
        "| A | B |\n"
        "| --- | --- |\n"
        "| [[NT:x1;x2]] | [[NT:y1;y2]] |"
    )
    out = extract_nested_tables(md)
    assert "→ 표 1" in out and "→ 표 2" in out
    assert out.index("→ 표 1") < out.index("→ 표 2")  # encounter order
    assert "**[표 1]**" in out and "**[표 2]**" in out
    assert "| → 표 1 | → 표 2 |" in out


def test_counter_spans_multiple_blocks():
    md = (
        "| A |\n| --- |\n| [[NT:p;q]] |\n\n"
        "중간 문단\n\n"
        "| C |\n| --- |\n| [[NT:m;n]] |"
    )
    out = extract_nested_tables(md)
    assert "→ 표 1" in out
    assert "→ 표 2" in out
    assert "중간 문단" in out
    assert out.index("→ 표 1") < out.index("중간 문단") < out.index("→ 표 2")


def test_empty_nested_table_dropped():
    md = "| A | B |\n| --- | --- |\n| x | [[NT:]] |"
    out = extract_nested_tables(md)
    assert "[[NT:" not in out
    assert "표 1" not in out      # no reference created
    assert "**[표" not in out      # no standalone table


def test_malformed_marker_preserved():
    md = "| A |\n| --- |\n| [[NT:x;y |"
    out = extract_nested_tables(md)  # must not raise
    assert "[[NT:x;y" in out


def test_cell_whitespace_collapsed():
    md = "| A |\n| --- |\n| [[NT:hello   world;b]] |"
    out = extract_nested_tables(md)
    assert "hello world" in out      # internal whitespace collapsed


def test_text_plus_marker_in_same_cell():
    md = "| A |\n| --- |\n| 상세 <br> [[NT:k;v]] |"
    out = extract_nested_tables(md)
    assert "상세 <br> → 표 1" in out
    assert "**[표 1]**" in out


def test_no_marker_passthrough():
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    assert extract_nested_tables(md) == md


def test_two_markers_in_same_cell():
    md = "| 항목 | 값 |\n| --- | --- |\n| 합계 | [[NT:x;y]] [[NT:z;w]] |"
    out = extract_nested_tables(md)
    assert "| 합계 | → 표 1 → 표 2 |" in out
    assert "**[표 1]**" in out and "**[표 2]**" in out


def test_empty_nested_table_pipe_shape_dropped():
    # The HWPX parser emits [[NT:|]] (empty cells joined by |) for an empty
    # nested table; it must be treated as empty and dropped.
    md = "| a | [[NT:|]] |"
    out = extract_nested_tables(md)
    assert "[[NT:" not in out
    assert "표 1" not in out


def test_safe_marker_preserves_delimiter_text():
    marker = serialize_nested_table([["A|B", "C;D"], ["literal ]] marker", "값"]])
    md = f"| parent |\n| --- |\n| {marker} |"
    out = extract_nested_tables(md)
    assert "→ 표 1" in out
    assert "A\\|B" in out
    assert "C;D" in out
    assert "literal ]] marker" in out


def test_safe_marker_empty_rows_dropped():
    assert serialize_nested_table([["", None]]) == ""
