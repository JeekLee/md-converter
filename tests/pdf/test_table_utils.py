"""Unit tests for pdf._table_utils."""
import pytest
from md_converter.pdf._table_utils import (
    _cell_text,
    _clean_table_block,
    _col_count,
    _header_cells,
    merge_overflow_tables,
    table_to_md,
)


# ── _cell_text ────────────────────────────────────────────────────────────────

def test_cell_text_none():
    assert _cell_text(None) == ""


def test_cell_text_pipe_escaped():
    assert _cell_text("a|b") == r"a\|b"


def test_cell_text_newline_replaced():
    assert _cell_text("hello\nworld") == "hello world"


def test_cell_text_cjk_char_spacing_removed():
    # PDF char-level spacing artifact: individual syllables separated by spaces
    assert _cell_text("보 험 인 정") == "보험인정"
    assert _cell_text("- 다 음 -") == "- 다음 -"


def test_cell_text_word_boundary_space_preserved():
    # Word-boundary spaces between multi-char words must be kept
    assert " " in _cell_text("성인 시상면")
    assert " " in _cell_text("척추 변형으로 인한 통증")


# ── table_to_md ───────────────────────────────────────────────────────────────

def test_table_to_md_basic():
    rows = [["번호", "제목", "날짜"], ["1", "고시 1호", "20260101"]]
    md = table_to_md(rows)
    lines = md.splitlines()
    assert lines[0] == "| 번호 | 제목 | 날짜 |"
    assert lines[1] == "| --- | --- | --- |"
    assert lines[2] == "| 1 | 고시 1호 | 20260101 |"


def test_table_to_md_none_cells():
    rows = [["A", "B"], [None, "val"]]
    md = table_to_md(rows)
    assert "|  |" in md


def test_table_to_md_empty():
    assert table_to_md([]) == ""


def test_table_to_md_ragged_rows():
    rows = [["A", "B", "C"], ["x", "y"]]
    md = table_to_md(rows)
    lines = md.splitlines()
    # Data row should be padded to 3 cols
    assert lines[2].count("|") == 4


# ── _col_count / _header_cells ────────────────────────────────────────────────

def test_col_count():
    assert _col_count("| A | B | C |\n| --- | --- | --- |") == 3


def test_col_count_empty():
    assert _col_count("") == 0


def test_header_cells():
    assert _header_cells("| A | B | C |") == ["A", "B", "C"]


# ── _clean_table_block ────────────────────────────────────────────────────────

def test_clean_table_block_removes_duplicate_subheader():
    block = [
        "| A | B |",
        "| --- | --- |",
        "| x | y |",
        "| x | y |",   # duplicate of first data row
        "| a | b |",
    ]
    result = _clean_table_block(block)
    assert result.count("| x | y |") == 1


def test_clean_table_block_merges_wrapped_cells():
    block = [
        "| A | B |",
        "| --- | --- |",
        "| val1 | line1 |",
        "|  | line2 |",    # first cell empty → continuation
    ]
    result = _clean_table_block(block)
    assert len(result) == 3
    assert "line1 line2" in result[2]


# ── merge_overflow_tables ─────────────────────────────────────────────────────

_TABLE_A = """\
| 항목 | 내용 |
| --- | --- |
| 1 | 가나다 |"""

_TABLE_B_CONTINUATION = """\
| 항목 | 내용 |
| --- | --- |
| 2 | 라마바 |"""

_TABLE_B_EXACT = """\
| 항목 | 내용 |
| --- | --- |
| 1 | 가나다 |"""


def test_merge_continuation():
    md = _TABLE_A + "\n\n" + _TABLE_B_CONTINUATION
    result = merge_overflow_tables(md)
    assert "라마바" in result
    # Should not repeat header
    assert result.count("| 항목 | 내용 |") == 1


def test_merge_exact_duplicate():
    md = _TABLE_A + "\n\n" + _TABLE_B_EXACT
    result = merge_overflow_tables(md)
    assert result.count("| 1 | 가나다 |") == 1


def test_non_table_text_passthrough():
    md = "# 제목\n\n일반 텍스트\n\n" + _TABLE_A
    result = merge_overflow_tables(md)
    assert "# 제목" in result
    assert "일반 텍스트" in result
    assert "가나다" in result


def test_empty_pdf_table_artifact_dropped():
    md = "앞 문단\n\n|  |  |\n| --- | --- |\n\n뒤 문단"
    result = merge_overflow_tables(md)
    assert "|  |  |" not in result
    assert "앞 문단" in result
    assert "뒤 문단" in result


def test_sparse_content_table_preserved():
    md = "|  | 내용 |\n| --- | --- |\n|  |  |"
    result = merge_overflow_tables(md)
    assert "|  | 내용 |" in result


# ── nested-containment helpers ────────────────────────────────────────────────

from md_converter.pdf._table_utils import (
    _clean_cell,
    bbox_area,
    bbox_in_cell,
    bbox_near_equal,
    serialize_nt,
)


def test_clean_cell_no_escape():
    assert _clean_cell("a|b") == "a|b"          # NO pipe escaping
    assert _clean_cell(None) == ""


def test_cell_text_unchanged_regression():
    # refactoring _cell_text on top of _clean_cell must not change its output
    assert _cell_text("a|b") == r"a\|b"
    assert _cell_text("보 험 인 정") == "보험인정"
    assert _cell_text(None) == ""


def test_serialize_nt_basic():
    assert serialize_nt([["항목", "금액"], ["외래", "1000"]]).startswith("[[NT64:")


def test_serialize_nt_empty():
    assert serialize_nt([[None, ""], ["  ", None]]) == ""


def test_serialize_nt_cleans_cells():
    # CJK char-spacing collapsed inside the marker, no escaping
    marker = serialize_nt([["보 험", "인 정"]])
    assert " " not in marker
    assert marker.startswith("[[NT64:")


def test_bbox_in_cell():
    cell = (10, 10, 100, 100)
    assert bbox_in_cell((20, 20, 80, 80), cell) is True
    assert bbox_in_cell((9, 20, 80, 80), cell, tol=2) is True    # within tolerance
    assert bbox_in_cell((5, 20, 80, 80), cell, tol=2) is False   # x0 too far outside
    assert bbox_in_cell((20, 20, 120, 80), cell) is False        # x1 outside


def test_table_to_md_keeps_nt_marker():
    marker = serialize_nt([["x", "y"], ["z", "w"]])
    md = table_to_md([["a", f"pre {marker} post"]])
    assert f"pre {marker} post" in md      # marker cell passed through intact


def test_table_to_md_escapes_normal_cell():
    md = table_to_md([["a|b", "c"]])
    assert r"a\|b" in md


def test_bbox_area():
    assert bbox_area((0, 0, 10, 20)) == 200
    assert bbox_area((10, 10, 10, 10)) == 0


def test_bbox_near_equal():
    assert bbox_near_equal((0, 0, 100, 100), (1, 1, 101, 99)) is True    # within margin
    assert bbox_near_equal((0, 0, 100, 100), (0, 0, 100, 110)) is False  # 10 off
