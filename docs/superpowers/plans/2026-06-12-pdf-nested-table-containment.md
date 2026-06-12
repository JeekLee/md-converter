# PDF 중첩 표 분리 (containment 기반) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PDF 파서가 셀 안에 든 중첩 표를 `[[NT:...]]` 마커로 부모 셀에 넣어, 기존 `extract_nested_tables`가 HWPX와 동일하게 `→ 표 N` + `**[표 N]**`로 분리하게 한다 (VLM 없음).

**Architecture:** pdfplumber가 괘선 있는 중첩 표를 이미 별도 `Table`로 추출하나 후처리가 버리거나 누수시킨다. 페이지 단위로 "sub-table bbox ⊂ 다른 table의 셀 bbox" 포함 관계를 감지해, 포함된 sub는 standalone에서 제외하고 부모 셀을 `prefix + [[NT:rows]] + suffix`(crop 기반)로 재구성한다. 공유 마커와 기존 파이프라인을 재사용한다.

**Tech Stack:** Python 3.11+, pdfplumber(표/crop), pymupdf=fitz(테스트 PDF 합성), pytest.

**Spec:** `docs/superpowers/specs/2026-06-12-pdf-nested-table-containment-design.md`

**전제:** 작업 브랜치 `feat/pdf-nested-table-containment`. 테스트는 `.venv/bin/python -m pytest <path> -v` (bare python/pytest는 rtk 훅 때문에 실패).

---

## 배경 (구현자 필수 컨텍스트)

**공유 마커:** `[[NT:r0c0|r0c1;r1c0|r1c1]]` — `;`=행, `|`=셀. HWP/HWPX와 동일. `MdConverter.convert()`가 마지막에 `extract_nested_tables(md)`를 호출해 이 마커를 `→ 표 N` + `**[표 N]**`로 바꾼다 (PDF도 같은 파이프라인 통과).

**pdfplumber Table API** (실측 확인): `t.bbox = (x0, top, x1, bottom)`; `t.rows[i].cells[j]`는 셀 bbox 튜플 `(x0, top, x1, bottom)` 또는 병합/빈 셀이면 `None`; `t.extract()`는 `list[list[str|None]]`로 `t.rows`와 행/열 정렬됨. `page.crop((x0,top,x1,bottom)).extract_text(x_tolerance=3, y_tolerance=3)`로 영역 텍스트 추출.

**현재 `_pdf.py` 흐름:** `parse()` → 페이지마다 `tables = page.find_tables()` → `_page_items_ordered(page, tables, img_tokens)`가 각 table을 `table.extract()` → `table_to_md(rows)`로 렌더. 좌표계는 fitz/pdfplumber 모두 좌상단 원점, y는 아래로 증가(일치).

---

## Task 1: `pdf/_table_utils.py` 순수 헬퍼

포함 판정·마커 직렬화·마커 보존 이스케이프. 실 PDF 없이 단위 테스트 가능.

**Files:**
- Modify: `src/md_converter/pdf/_table_utils.py`
- Test: `tests/pdf/test_table_utils.py`

- [ ] **Step 1: 실패 테스트 작성** — Append to `tests/pdf/test_table_utils.py`:

```python
# ── nested-containment helpers ────────────────────────────────────────────────

from md_converter.pdf._table_utils import (
    _clean_cell,
    bbox_in_cell,
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
    assert serialize_nt([["항목", "금액"], ["외래", "1000"]]) == "[[NT:항목|금액;외래|1000]]"


def test_serialize_nt_empty():
    assert serialize_nt([[None, ""], ["  ", None]]) == ""


def test_serialize_nt_cleans_cells():
    # CJK char-spacing collapsed inside the marker, no escaping
    assert serialize_nt([["보 험", "인 정"]]) == "[[NT:보험|인정]]"


def test_bbox_in_cell():
    cell = (10, 10, 100, 100)
    assert bbox_in_cell((20, 20, 80, 80), cell) is True
    assert bbox_in_cell((9, 20, 80, 80), cell, tol=2) is True    # within tolerance
    assert bbox_in_cell((5, 20, 80, 80), cell, tol=2) is False   # x0 too far outside
    assert bbox_in_cell((20, 20, 120, 80), cell) is False        # x1 outside


def test_table_to_md_keeps_nt_marker():
    md = table_to_md([["a", "pre [[NT:x|y;z|w]] post"]])
    assert "pre [[NT:x|y;z|w]] post" in md      # marker cell passed through, pipes intact


def test_table_to_md_escapes_normal_cell():
    md = table_to_md([["a|b", "c"]])
    assert r"a\|b" in md
```

Note: `tests/pdf/test_table_utils.py` already imports `_cell_text` and `table_to_md` at the top (`from md_converter.pdf._table_utils import (_cell_text, _clean_table_block, _col_count, _header_cells, merge_overflow_tables, table_to_md)`). Reuse those; only add the new imports shown.

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/pdf/test_table_utils.py -k "clean_cell or serialize_nt or bbox_in_cell or nt_marker or unchanged_regression" -v`
Expected: FAIL with `ImportError: cannot import name '_clean_cell'` (and `bbox_in_cell`, `serialize_nt`).

- [ ] **Step 3: 헬퍼 구현** — In `src/md_converter/pdf/_table_utils.py`, replace the existing `_cell_text` function:

```python
def _cell_text(cell: str | None) -> str:
    if cell is None:
        return ""
    text = _join_lines(cell)
    # Remove character-level spacing artifacts: "다 음" → "다음"
    # Only fires when each CJK char is individually space-separated (no multi-char words involved)
    text = re.sub(
        r"(?<![가-힣])([가-힣])( [가-힣])+(?![가-힣])",
        lambda m: m.group(0).replace(" ", ""),
        text,
    )
    return text.replace("|", "\\|").strip()
```

with this (factor out `_clean_cell`, add helpers):

```python
def _clean_cell(cell: str | None) -> str:
    """Cell text cleaning WITHOUT pipe escaping: join PDF line-wraps + remove
    CJK char-spacing artifacts. Used by _cell_text and by serialize_nt."""
    if cell is None:
        return ""
    text = _join_lines(cell)
    # Remove character-level spacing artifacts: "다 음" → "다음"
    # Only fires when each CJK char is individually space-separated (no multi-char words involved)
    text = re.sub(
        r"(?<![가-힣])([가-힣])( [가-힣])+(?![가-힣])",
        lambda m: m.group(0).replace(" ", ""),
        text,
    )
    return text.strip()


def _cell_text(cell: str | None) -> str:
    return _clean_cell(cell).replace("|", "\\|")


def serialize_nt(rows: list[list[str | None]]) -> str:
    """Serialize a nested sub-table's rows to the shared [[NT:row;row]] marker.

    Cells are cleaned (line-join + CJK spacing) but NOT pipe-escaped, since
    '|' and ';' are the marker's own separators. Returns '' if all cells blank.
    """
    if not any((c or "").strip() for row in rows for c in row):
        return ""
    return "[[NT:" + ";".join("|".join(_clean_cell(c) for c in row) for row in rows) + "]]"


def bbox_in_cell(
    sub_bbox: tuple[float, float, float, float],
    cell_bbox: tuple[float, float, float, float],
    tol: float = 2.0,
) -> bool:
    """True if sub_bbox is fully inside cell_bbox within tol. bbox = (x0, top, x1, bottom)."""
    sx0, st, sx1, sb = sub_bbox
    cx0, ct, cx1, cb = cell_bbox
    return sx0 >= cx0 - tol and sx1 <= cx1 + tol and st >= ct - tol and sb <= cb + tol


def _escape_cell_for_table(s: str | None) -> str:
    """Leave [[NT:...]] marker cells intact (so the marker survives); escape others."""
    if s is not None and "[[NT:" in s:
        return s
    return _cell_text(s)
```

- [ ] **Step 4: `table_to_md`가 마커 보존 이스케이프 사용** — In the same file, in `table_to_md`, change the per-cell mapping. Replace:

```python
    norm = []
    for row in rows:
        cells = [_cell_text(c) for c in row]
        while len(cells) < col_count:
            cells.append("")
        norm.append(cells)
```

with:

```python
    norm = []
    for row in rows:
        cells = [_escape_cell_for_table(c) for c in row]
        while len(cells) < col_count:
            cells.append("")
        norm.append(cells)
```

- [ ] **Step 5: 통과 확인** — Run: `.venv/bin/python -m pytest tests/pdf/test_table_utils.py -v`
Expected: PASS (new tests + all pre-existing table_utils tests — `_cell_text`/`table_to_md`/`merge_overflow_tables` behavior unchanged).

- [ ] **Step 6: 커밋**

```bash
git add src/md_converter/pdf/_table_utils.py tests/pdf/test_table_utils.py
git commit -m "feat(pdf): 중첩표용 순수 헬퍼 — _clean_cell 분리, serialize_nt, bbox_in_cell, 마커 보존 이스케이프"
```

---

## Task 2: `pdf/_pdf.py` containment 통합

포함 감지 + crop 기반 부모 셀 재구성 + standalone 제외.

**Files:**
- Modify: `src/md_converter/pdf/_pdf.py`
- Test: `tests/pdf/test_nested_containment.py` (신규)

- [ ] **Step 1: 결정적(fake) 실패 테스트 작성** — Create `tests/pdf/test_nested_containment.py`:

```python
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
    assert rebuilt_cell == "기재형식 [[NT:항목|금액;외래|1000]] 예시"


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
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/pdf/test_nested_containment.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_nested' from 'md_converter.pdf._pdf'`.

- [ ] **Step 3: `resolve_nested` + crop 재구성 구현** — In `src/md_converter/pdf/_pdf.py`, update the import line:

```python
from ._table_utils import merge_overflow_tables, table_to_md
```

to:

```python
from ._table_utils import (
    bbox_in_cell,
    merge_overflow_tables,
    serialize_nt,
    table_to_md,
    _cell_text,
)
```

Then add these three functions just above `def _page_items_ordered(` :

```python
def _crop_text(page, x0: float, top: float, x1: float, bottom: float) -> str:
    """Extract cleaned text from a sub-region of the page; '' on any failure."""
    if bottom - top <= 2 or x1 - x0 <= 2:
        return ""
    try:
        crop = page.crop((x0, top, x1, bottom))
        return (crop.extract_text(x_tolerance=3, y_tolerance=3) or "").strip()
    except Exception:
        return ""


def _rebuild_cell(page, cell_bbox, sub_tables) -> str:
    """Rebuild a parent cell as prefix + [[NT:...]] markers + suffix.

    The cell's text is split into vertical bands around each nested sub-table
    so surrounding text (above/below/between) is preserved, while each nested
    table becomes a marker. Text bands are pipe-escaped (_cell_text); markers
    are raw.
    """
    cx0, ct, cx1, cb = cell_bbox
    subs = sorted(sub_tables, key=lambda t: t.bbox[1])  # top-to-bottom
    parts: list[str] = []
    y = ct
    for sub in subs:
        st, sb = sub.bbox[1], sub.bbox[3]
        band = _crop_text(page, cx0, y, cx1, st)
        if band:
            parts.append(_cell_text(band))
        nt = serialize_nt(sub.extract())
        if nt:
            parts.append(nt)
        y = max(y, sb)
    tail = _crop_text(page, cx0, y, cx1, cb)
    if tail:
        parts.append(_cell_text(tail))
    return " ".join(p for p in parts if p)


def resolve_nested(page, tables):
    """Detect tables nested inside other tables' cells.

    Returns (suppressed, overrides):
      suppressed: set[int] — table indices contained in another table's cell
                  (excluded from standalone rendering).
      overrides:  dict[int, list[list[str]]] — for each top-level table that has
                  nested children, the rebuilt rows with each child-holding cell
                  replaced by prefix + [[NT:...]] + suffix.

    Only one nesting level becomes a marker: deeper tables are still suppressed
    and their content rides along as flattened text in the parent's extract().
    """
    n = len(tables)
    # sub_idx -> (parent_idx, row_idx, col_idx, cell_area) for the smallest containing cell
    contained: dict[int, tuple[int, int, int, float]] = {}
    for si in range(n):
        sub_bbox = tables[si].bbox
        best = None
        for pi in range(n):
            if pi == si:
                continue
            for ri, row in enumerate(tables[pi].rows):
                for ci, cell in enumerate(row.cells):
                    if cell is None:
                        continue
                    if bbox_in_cell(sub_bbox, cell):
                        area = (cell[2] - cell[0]) * (cell[3] - cell[1])
                        if best is None or area < best[3]:
                            best = (pi, ri, ci, area)
        if best is not None:
            contained[si] = best

    suppressed = set(contained.keys())

    # group children by (parent, cell), only where the parent is itself top-level
    children: dict[int, dict[tuple[int, int], list[int]]] = {}
    for si, (pi, ri, ci, _area) in contained.items():
        if pi in suppressed:
            continue  # parent is itself nested → this sub flattens into parent's extract()
        children.setdefault(pi, {}).setdefault((ri, ci), []).append(si)

    overrides: dict[int, list[list[str]]] = {}
    for pi, cellmap in children.items():
        rows = [list(r) for r in tables[pi].extract()]
        for (ri, ci), sub_idxs in cellmap.items():
            if ri >= len(rows) or ci >= len(rows[ri]):
                continue
            cell_bbox = tables[pi].rows[ri].cells[ci]
            if cell_bbox is None:
                continue
            rows[ri][ci] = _rebuild_cell(page, cell_bbox, [tables[s] for s in sub_idxs])
        overrides[pi] = rows
    return suppressed, overrides
```

- [ ] **Step 4: fake 테스트 통과 확인** — Run: `.venv/bin/python -m pytest tests/pdf/test_nested_containment.py -v`
Expected: PASS (4 fake-based tests).

- [ ] **Step 5: `_page_items_ordered` 배선** — In `src/md_converter/pdf/_pdf.py`, in `_page_items_ordered`, replace the table loop. Replace:

```python
    # Tables
    for table in tables:
        rows = table.extract()
        if rows:
            md = table_to_md(rows)
            if md:
                segments.append((table.bbox[1], table.bbox[3], md))
```

with:

```python
    # Tables (resolving nested tables into [[NT:...]] markers first)
    suppressed, overrides = resolve_nested(page, tables)
    for ti, table in enumerate(tables):
        if ti in suppressed:
            continue
        rows = overrides[ti] if ti in overrides else table.extract()
        if rows:
            md = table_to_md(rows)
            if md:
                segments.append((table.bbox[1], table.bbox[3], md))
```

- [ ] **Step 6: fitz 합성 통합 테스트 추가** — Append to `tests/pdf/test_nested_containment.py`:

```python
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

    assert "[[NT:" in md          # parser produced a nested-table marker
    assert "→ 표 1" in out        # separated by the shared pipeline
    assert "**[표 1]**" in out
    assert "1000" in out          # nested leaf preserved
    assert "FORMAT" in out and "EXAMPLE" in out   # surrounding cell text preserved
```

- [ ] **Step 7: 통합 테스트 실행** — Run: `.venv/bin/python -m pytest tests/pdf/test_nested_containment.py -v`
Expected: PASS — `test_nested_pdf_via_fitz` either passes (marker produced end-to-end) or SKIPs if pdfplumber doesn't split the synthetic grid. If it FAILS with an assertion (marker missing despite 2 tables detected), inspect with: print `parse(data)[0]` and the table bboxes/cells; the most likely cause is the nested bbox not falling inside the outer cell bbox — adjust the synthetic coordinates so the nested grid (200-520, 190-290) sits inside the outer data cell (170-545, 140-400), which it does by construction.

- [ ] **Step 8: PDF 회귀 확인** — Run: `.venv/bin/python -m pytest tests/pdf/ -v`
Expected: PASS (existing `test_table_utils.py`, `test_diagram_utils.py` unaffected).

- [ ] **Step 9: 커밋**

```bash
git add src/md_converter/pdf/_pdf.py tests/pdf/test_nested_containment.py
git commit -m "feat(pdf): 셀 포함 중첩 표를 [[NT:]]로 재구성 — HWPX와 동일 분리"
```

---

## Task 3: 전체 회귀 + 수동 검증

**Files:** 없음 (검증 전용)

- [ ] **Step 1: 전체 스위트** — Run: `.venv/bin/python -m pytest -v`
Expected: PASS (신규 PDF 테스트 포함 전체 통과; 실파일 의존 테스트는 샘플 유무에 따라 pass/skip — 정상).

- [ ] **Step 2: (수동) clic 실문서 대조** — clic `01_image` PDF를 변환해 출력에 `→ 표 N` 참조와 `**[표 N]**` 분리 표가 등장하고, 코드/부위 격자가 `| 코드 | 부위 |...` GFM으로 복원되는지 HWPX 출력과 대조. `02_table` PDF는 과분리/표 누락 없는지 확인. (MinIO/네트워크 필요 — 환경 가능 시 수행.)

---

## Self-Review (작성자 점검 결과)

**1. Spec coverage:**
- 포함 감지(`bbox_in_cell`, `resolve_nested`) → Task 1·2.
- 부모 셀 crop 재구성(prefix/marker/suffix) → Task 2 `_rebuild_cell`/`_crop_text`.
- `[[NT:]]` 직렬화(`serialize_nt`, 셀 정제·미이스케이프) → Task 1.
- 마커 보존 이스케이프(`_escape_cell_for_table`, `table_to_md`) → Task 1.
- 포함 sub 제외 + 기존 파이프라인 재사용 → Task 2 `_page_items_ordered` 배선 (nested_tables.py 무변경).
- 2단계+ 평탄화(suppress + parent extract 흡수) → `resolve_nested`의 `pi in suppressed` 스킵 + 주석.
- None 셀 스킵, 빈 sub, 빈 crop, 표 0/1개 → Task 2 테스트 + 가드.
- 테스트: 순수 단위(Task 1) + fake 결정적(Task 2) + fitz 통합(Task 2) + 회귀/수동(Task 3).

**2. Placeholder scan:** TBD/TODO 없음. 모든 코드 스텝에 완전한 코드. Step 7의 디버그 지침은 구체적(좌표·출력 확인)이며 동작은 변경 안 함.

**3. Type consistency:** `bbox_in_cell(sub_bbox, cell_bbox, tol)`, `serialize_nt(rows)->str`, `_clean_cell(cell)->str`, `_escape_cell_for_table(s)->str`, `resolve_nested(page, tables)->(set, dict)`, `_rebuild_cell(page, cell_bbox, sub_tables)->str`, `_crop_text(page,x0,top,x1,bottom)->str` — Task 1·2 본문과 테스트에서 일관. bbox 튜플 순서 `(x0, top, x1, bottom)` 통일. 마커 문자열 `[[NT:`/`]]`, 참조 `→ 표 N`, 헤더 `**[표 N]**`는 기존 `nested_tables.py`(소비자)와 일치(변경 없음).
