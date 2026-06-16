"""Tests for PDF nested-table containment resolution."""
from __future__ import annotations

import pytest

from md_converter.pdf._pdf import resolve_nested
from md_converter.pdf._table_utils import table_to_md
from md_converter.nested_tables import extract_nested_tables


# ── fakes: duck-typed pdfplumber Table / Row / Page ───────────────────────────

class _Row:
    def __init__(self, cells):
        self.cells = cells


class _Tbl:
    def __init__(self, bbox, rows_cells, extracted):
        self.bbox = bbox
        self.rows = [_Row(rc) for rc in rows_cells]
        self._extracted = extracted

    def extract(self):
        return self._extracted


class _Crop:
    def __init__(self, text):
        self._t = text

    def extract_text(self, **kwargs):
        return self._t


class _Page:
    """crop() returns text keyed by the rounded top coordinate of the request."""
    def __init__(self, by_top):
        self.by_top = by_top

    def crop(self, bbox):
        return _Crop(self.by_top.get(int(round(bbox[1])), ""))


def _fixture():
    # parent: 1 row x 2 cols; col1 (the big cell) contains the sub-table
    parent = _Tbl(
        bbox=(0, 0, 400, 300),
        rows_cells=[[(0, 0, 100, 300), (100, 0, 400, 300)]],
        extracted=[["본인부담", "PARENT_FLAT_TEXT"]],
    )
    # sub-table inside parent cell (0,1), vertical band y=100..180
    sub = _Tbl(
        bbox=(120, 100, 380, 180),
        rows_cells=[[(120, 100, 250, 180), (250, 100, 380, 180)]],
        extracted=[["항목", "금액"], ["외래", "1000"]],
    )
    # prefix band top = ct = 0  → "기재형식"; suffix band top = sub.bottom = 180 → "예시"
    page = _Page({0: "기재형식", 180: "예시"})
    return page, parent, sub


def test_resolve_nested_containment_and_override():
    page, parent, sub = _fixture()
    suppressed, overrides = resolve_nested(page, [parent, sub])
    assert suppressed == {1}                       # sub (index 1) is contained
    assert 0 in overrides                          # parent (index 0) rebuilt
    rebuilt_cell = overrides[0][0][1]
    assert rebuilt_cell.startswith("기재형식 [[NT64:")
    assert rebuilt_cell.endswith("]] 예시")


def test_resolve_nested_end_to_end():
    page, parent, sub = _fixture()
    suppressed, overrides = resolve_nested(page, [parent, sub])
    mds = []
    for ti, t in enumerate([parent, sub]):
        if ti in suppressed:
            continue
        rows = overrides[ti] if ti in overrides else t.extract()
        mds.append(table_to_md(rows))
    out = extract_nested_tables("\n\n".join(mds))
    assert "→ 표 1" in out
    assert "**[표 1]**" in out
    assert "| 항목 | 금액 |" in out
    assert "| 외래 | 1000 |" in out
    assert "PARENT_FLAT_TEXT" not in out          # parent cell flat text was replaced


def test_resolve_nested_no_tables():
    assert resolve_nested(_Page({}), []) == (set(), {})


def test_resolve_nested_none_cell_skipped():
    # parent cell (0,0) is None (merged) — must not crash; sub not contained anywhere
    parent = _Tbl(bbox=(0, 0, 400, 300),
                  rows_cells=[[None, (100, 0, 400, 300)]],
                  extracted=[["x", "y"]])
    sub = _Tbl(bbox=(500, 500, 560, 560),   # far outside parent
               rows_cells=[[(500, 500, 560, 560)]],
               extracted=[["z"]])
    suppressed, overrides = resolve_nested(_Page({}), [parent, sub])
    assert suppressed == set()
    assert overrides == {}


def _make_nested_pdf() -> bytes:
    """Build a PDF with an outer 2-col table whose data cell holds a nested grid.

    ASCII content avoids CJK font issues; structure (ruling lines) is what matters.
    """
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    def line(x0, y0, x1, y1):
        page.draw_line((x0, y0), (x1, y1), width=1.0)

    def text(x, y, s):
        page.insert_text((x, y), s, fontsize=11)

    # ── outer table: cols split at x=170; header row 100-140, data row 140-400 ──
    for y in (100, 140, 400):
        line(50, y, 545, y)
    for x in (50, 170, 545):
        line(x, 100, x, 400)
    text(60, 125, "Kind"); text(180, 125, "Detail")
    text(60, 165, "self-pay")
    text(180, 165, "FORMAT")                      # prefix text (above nested)

    # ── nested table inside outer data cell (x 170-545, y 140-400) ──────────────
    #    region x 200-520, cols split at x=360; header 190-240, data 240-290
    for y in (190, 240, 290):
        line(200, y, 520, y)
    for x in (200, 360, 520):
        line(x, 190, x, 290)
    text(210, 225, "Item"); text(370, 225, "Amount")
    text(210, 275, "Outpatient"); text(370, 275, "1000")

    text(180, 360, "EXAMPLE")                     # suffix text (below nested)
    return doc.tobytes()


def test_nested_pdf_via_fitz():
    pytest.importorskip("fitz")
    import io
    import pdfplumber
    from md_converter.pdf import parse

    data = _make_nested_pdf()

    # Guard: this test only validates when pdfplumber detects the nested grid as a
    # separate table contained in the outer cell (the real-world case we target).
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages[0].find_tables()) < 2:
            pytest.skip("pdfplumber did not detect the nested grid as a separate table")

    md, _ = parse(data)
    out = extract_nested_tables(md)

    assert "[[NT64:" in md        # parser produced a nested-table marker
    assert "→ 표 1" in out        # separated by the shared pipeline
    assert "**[표 1]**" in out
    assert "1000" in out          # nested leaf preserved
    assert "FORMAT" in out and "EXAMPLE" in out   # surrounding cell text preserved


def test_resolve_nested_mutual_containment_no_loss():
    # two identical single-cell tables must NOT suppress each other (no data loss)
    a = _Tbl(bbox=(0, 0, 200, 100), rows_cells=[[(0, 0, 200, 100)]], extracted=[["A"]])
    b = _Tbl(bbox=(0, 0, 200, 100), rows_cells=[[(0, 0, 200, 100)]], extracted=[["B"]])
    suppressed, overrides = resolve_nested(_Page({}), [a, b])
    assert suppressed == set()
    assert overrides == {}


def test_resolve_nested_near_duplicate_no_nesting():
    # jittered near-duplicate (within margin) is not treated as nesting
    a = _Tbl(bbox=(0, 0, 200, 100), rows_cells=[[(0, 0, 200, 100)]], extracted=[["A"]])
    b = _Tbl(bbox=(0.5, 0.4, 200.3, 100.2), rows_cells=[[(0.5, 0.4, 200.3, 100.2)]], extracted=[["A"]])
    suppressed, overrides = resolve_nested(_Page({}), [a, b])
    assert suppressed == set()
    assert overrides == {}


def test_resolve_nested_multi_child_cell():
    # one big parent cell containing two stacked sub-tables -> two markers in y-order
    parent = _Tbl(bbox=(0, 0, 400, 400), rows_cells=[[(0, 0, 400, 400)]], extracted=[["FLAT"]])
    sub1 = _Tbl(bbox=(50, 50, 350, 120), rows_cells=[[(50, 50, 350, 120)]], extracted=[["a1", "b1"]])
    sub2 = _Tbl(bbox=(50, 200, 350, 270), rows_cells=[[(50, 200, 350, 270)]], extracted=[["a2", "b2"]])
    page = _Page({0: "top", 120: "mid", 270: "bot"})
    suppressed, overrides = resolve_nested(page, [parent, sub1, sub2])
    assert suppressed == {1, 2}
    cell = overrides[0][0][0]
    assert cell.count("[[NT64:") == 2
    assert cell.index("[[NT64:") < cell.rindex("[[NT64:")


def test_resolve_nested_depth2_chain():
    # A contains B contains C -> only one marker level (B in A); C rides as B's flat text
    A = _Tbl(bbox=(0, 0, 400, 400), rows_cells=[[(0, 0, 400, 400)]], extracted=[["A_FLAT"]])
    B = _Tbl(bbox=(50, 50, 350, 350), rows_cells=[[(50, 50, 350, 350)]], extracted=[["B has C_FLAT"]])
    C = _Tbl(bbox=(100, 100, 300, 200), rows_cells=[[(100, 100, 300, 200)]], extracted=[["c1", "c2"]])
    page = _Page({0: "", 50: "", 350: ""})
    suppressed, overrides = resolve_nested(page, [A, B, C])
    assert suppressed == {1, 2}
    assert set(overrides.keys()) == {0}
    cell = overrides[0][0][0]
    assert cell.count("[[NT64:") == 1
    assert "C_FLAT" in extract_nested_tables(cell)
