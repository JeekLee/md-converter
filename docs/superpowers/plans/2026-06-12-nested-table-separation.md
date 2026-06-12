# 중첩 표 분리(Nested Table Separation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 중첩 표를 LLM 호출 없이 별도 GFM 표(`**[표 N]**`)로 분리하고 부모 셀에는 `→ 표 N` 참조만 남긴다.

**Architecture:** 파서(HWPX/HWP5)는 중첩 표를 공유 마커 `[[NT:행;행]]`으로 내보내고, 새 파이프라인 단계 `extract_nested_tables()`가 번호 부여·참조 치환·분리 표 배치를 전담한다. HWPX는 이미 마커를 생성하므로 파서 변경이 없고, HWP5는 단일 테이블 상태를 컨텍스트 스택으로 바꿔 중첩을 감지(바깥 표 행 유실 버그 동시 수정)한다.

**Tech Stack:** Python 3.11+, pytest. stdlib only (신규 모듈은 외부 의존성 없음).

**Spec:** `docs/superpowers/specs/2026-06-12-nested-table-separation-design.md`

---

## 배경 (구현자용 필수 컨텍스트)

**공유 마커 포맷:** `[[NT:r0c0|r0c1;r1c0|r1c1]]` — `;`는 행 구분, `|`는 같은 행의 셀 구분.

**현재 동작:**
- HWPX `src/md_converter/hwp/hwpx/_table_utils.py`의 `_cell_text()`가 셀 내부 중첩 표를 이 마커로 평탄화한다 (이번 작업에서 **변경 없음**).
- `MdConverter.convert()` (`src/md_converter/__init__.py:96`)가 마지막에 `restructure_nested_tables(md, self._llm)`를 호출해 마커마다 LLM을 부른다 — 이게 제거 대상.
- HWP5 `src/md_converter/hwp/hwp5/_parser.py`의 `_parse_section()`은 테이블 레벨을 하나만 추적해 중첩 표를 만나면 바깥 표 상태를 덮어쓴다 (행 유실 버그).

**HWP5 레코드 구조:** 각 레코드는 `(tag_id, level, payload)`. 표는 `CTRL_HEADER`(tag `0x47`, payload[:4]==`b" lbt"`)로 시작하고, 그 안의 `LIST_HEADER`(tag `0x48`, 셀 경계)와 `PARA_TEXT`(tag `0x43`, 셀 텍스트)는 한 레벨 깊다. 중첩 표는 셀 내부에서 더 깊은 레벨의 `CTRL_HEADER`로 나타난다. `LIST_HEADER` payload offset 10의 u16이 `rowAddr`(행 주소; 값이 바뀌면 새 행).

**전제:** 현재 작업 브랜치는 `feat/nested-table-separation`. 테스트는 저장소 루트에서 `python -m pytest`로 실행.

---

## Task 1: `extract_nested_tables` 파이프라인 단계 (신규 모듈)

순수 문자열 처리 함수. 파서 변경과 독립적으로 단위 테스트 가능.

**Files:**
- Create: `src/md_converter/nested_tables.py`
- Test: `tests/test_nested_tables.py`

- [ ] **Step 1: 실패하는 첫 테스트 작성**

Create `tests/test_nested_tables.py`:

```python
"""Unit tests for extract_nested_tables (nested-table separation)."""
from __future__ import annotations

from md_converter.nested_tables import extract_nested_tables


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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_nested_tables.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'md_converter.nested_tables'`

- [ ] **Step 3: 모듈 구현**

Create `src/md_converter/nested_tables.py`:

```python
"""Separate nested tables ([[NT:...]] markers) into standalone GFM tables.

A nested table inside a parent cell is emitted by the HWPX/HWP5 parsers as a
flat marker:  [[NT:r0c0|r0c1;r1c0|r1c1]]  (';' = row, '|' = cell).

extract_nested_tables() replaces each marker with a human-readable reference
"→ 표 N" in the parent cell and appends the nested table as a standalone GFM
table ("**[표 N]**" + table) right after the parent block.  No LLM involved.
"""
from __future__ import annotations

import re

_NT_OPEN = "[[NT:"


def _escape(cell: str) -> str:
    """Collapse whitespace and escape pipes for a GFM cell (matches _escape_cell)."""
    return re.sub(r"\s+", " ", cell.replace("|", "\\|")).strip()


def _parse_nt(content: str) -> list[list[str]]:
    """Parse marker inner text 'r0c0|r0c1;r1c0|r1c1' into rows of cells."""
    return [row.split("|") for row in content.split(";")]


def _is_empty(rows: list[list[str]]) -> bool:
    return not any(cell.strip() for row in rows for cell in row)


def _to_gfm(rows: list[list[str]]) -> str:
    """Render parsed rows as a GFM table (first row = header)."""
    col_count = max((len(r) for r in rows), default=0)
    lines: list[str] = []
    for i, row in enumerate(rows):
        padded = row + [""] * (col_count - len(row))
        lines.append("| " + " | ".join(_escape(c) for c in padded) + " |")
        if i == 0:
            lines.append("|" + "|".join(" --- " for _ in padded) + "|")
    return "\n".join(lines)


def extract_nested_tables(md: str) -> str:
    """Replace [[NT:...]] markers with '→ 표 N' refs + standalone tables.

    Standalone tables are inserted as separate blocks right after the block
    that contained the marker.  Numbering is a single document-wide counter.
    """
    if _NT_OPEN not in md:
        return md

    out: list[str] = []
    counter = 0
    for block in md.split("\n\n"):
        if _NT_OPEN not in block:
            out.append(block)
            continue
        result: list[str] = []
        extracted: list[str] = []
        remaining = block
        while _NT_OPEN in remaining:
            start = remaining.find(_NT_OPEN)
            result.append(remaining[:start])
            after = remaining[start + len(_NT_OPEN):]
            end = after.find("]]")
            if end == -1:                       # malformed: keep verbatim, stop
                result.append(remaining[start:])
                remaining = ""
                break
            rows = _parse_nt(after[:end])
            if not _is_empty(rows):
                counter += 1
                result.append(f"→ 표 {counter}")
                extracted.append(f"**[표 {counter}]**\n\n{_to_gfm(rows)}")
            remaining = after[end + 2:]
        result.append(remaining)
        out.append("".join(result))
        out.extend(extracted)
    return "\n\n".join(out)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_nested_tables.py -v`
Expected: PASS

- [ ] **Step 5: 엣지 케이스 테스트 추가**

Append to `tests/test_nested_tables.py`:

```python
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
```

- [ ] **Step 6: 전체 테스트 통과 확인**

Run: `python -m pytest tests/test_nested_tables.py -v`
Expected: PASS (8 tests)

- [ ] **Step 7: 커밋**

```bash
git add src/md_converter/nested_tables.py tests/test_nested_tables.py
git commit -m "feat: [[NT:]] 마커를 분리 표 + '→ 표 N' 참조로 변환하는 extract_nested_tables 추가"
```

---

## Task 2: 파이프라인 배선 + 중첩 표 LLM 코드 제거

`MdConverter.convert()`가 `extract_nested_tables`를 쓰도록 바꾸고, `llm.py`의 중첩 표 LLM 경로를 제거한다.

**Files:**
- Modify: `src/md_converter/__init__.py:22` (import), `src/md_converter/__init__.py:96` (호출)
- Modify: `src/md_converter/llm.py` (`restructure_nested_tables`, `_call_llm`, `_PROMPT_TEMPLATE` 제거)
- Test: `tests/hwp/test_hwpx.py` (HWPX 중첩 표 → convert → 분리 표 통합 테스트 추가)

- [ ] **Step 1: 실패하는 통합 테스트 작성**

Append to `tests/hwp/test_hwpx.py` (파일에 `_make_hwpx`, `_sec` 헬퍼가 이미 있음):

```python
def test_nested_table_separated_via_converter():
    from md_converter import MdConverter, LlmConfig

    def _tc(text: str) -> str:
        return (
            "<hp:tc><hp:subList><hp:p><hp:run>"
            f"<hp:t>{text}</hp:t>"
            "</hp:run></hp:p></hp:subList></hp:tc>"
        )

    nested = (
        "<hp:tbl>"
        f"<hp:tr>{_tc('항목')}{_tc('금액')}</hp:tr>"
        f"<hp:tr>{_tc('외래')}{_tc('1000')}</hp:tr>"
        "</hp:tbl>"
    )
    nested_cell = (
        f"<hp:tc><hp:subList><hp:p><hp:run>{nested}</hp:run></hp:p></hp:subList></hp:tc>"
    )
    outer = (
        "<hp:p><hp:run><hp:tbl>"
        f"<hp:tr>{_tc('구분')}{_tc('세부내용')}</hp:tr>"
        f"<hp:tr>{_tc('본인부담')}{nested_cell}</hp:tr>"
        "</hp:tbl></hp:run></hp:p>"
    )
    xml = _sec(outer)

    # LLM은 중첩 표 처리에 더 이상 쓰이지 않으므로 더미 설정(미사용)으로 충분.
    converter = MdConverter(llm=LlmConfig(url="http://unused.invalid", api_key="x", model="x"))
    md = converter.convert(_make_hwpx(xml), suffix=".hwpx")

    assert "[[NT:" not in md
    assert "→ 표 1" in md
    assert "**[표 1]**" in md
    assert "| 항목 | 금액 |" in md
    assert "| 외래 | 1000 |" in md
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/hwp/test_hwpx.py::test_nested_table_separated_via_converter -v`
Expected: FAIL — `assert "→ 표 1" in md` (현재 `convert()`는 `restructure_nested_tables`를 호출하고, 더미 LLM URL이라 실패→평탄 텍스트 유지하므로 `→ 표 1`이 없음)

- [ ] **Step 3: `convert()` 배선 교체**

In `src/md_converter/__init__.py`, change the import on line 22 from:

```python
from .llm import LlmConfig, drawing_to_mermaid, restructure_nested_tables, vision_to_mermaid, vision_to_text
```

to:

```python
from .llm import LlmConfig, drawing_to_mermaid, vision_to_mermaid, vision_to_text
from .nested_tables import extract_nested_tables
```

In the same file, change the pipeline call on line 96 from:

```python
        md = restructure_nested_tables(md, self._llm)
```

to:

```python
        md = extract_nested_tables(md)
```

- [ ] **Step 4: `llm.py`에서 중첩 표 LLM 코드 제거**

In `src/md_converter/llm.py`, delete these three top-level definitions (keep everything else, including `drawing_to_mermaid`, `vision_to_mermaid`, `vision_to_text` and their prompts):

1. The `_PROMPT_TEMPLATE` string constant (the block starting `_PROMPT_TEMPLATE = """\` and ending at its closing `"""`).
2. The `_call_llm(content: str, cfg: LlmConfig) -> str:` function.
3. The `restructure_nested_tables(markdown: str, cfg: LlmConfig) -> str:` function (the last function in the file).

After deletion the module keeps: `LlmConfig`, `_DRAWING_PROMPT`, `drawing_to_mermaid`, `_DIAGRAM_VISION_PROMPT`, `vision_to_mermaid`, `_OCR_PROMPT`, `vision_to_text`.

- [ ] **Step 5: 통합 테스트 통과 + 회귀 확인**

Run: `python -m pytest tests/hwp/test_hwpx.py tests/test_llm_vision.py -v`
Expected: PASS — 신규 테스트 통과, 기존 HWPX/vision 테스트 회귀 없음

- [ ] **Step 6: import 무결성 확인**

Run: `python -c "import md_converter; from md_converter.llm import vision_to_mermaid, drawing_to_mermaid, vision_to_text; print('ok')"`
Expected: `ok` (제거된 심볼을 어디서도 import하지 않음)

- [ ] **Step 7: 커밋**

```bash
git add src/md_converter/__init__.py src/md_converter/llm.py tests/hwp/test_hwpx.py
git commit -m "refactor: 중첩 표를 LLM 대신 extract_nested_tables로 처리 (LLM 병목 제거)"
```

---

## Task 3: HWP5 중첩 표 감지 (테이블 컨텍스트 스택)

HWP5 파서가 중첩 표를 감지해 `[[NT:]]` 마커를 부모 셀에 넣도록 한다. 단일 테이블 상태를 스택으로 바꾸고, `[[NT:]]` 셀의 파이프가 이스케이프되지 않도록 `table_to_md`를 보정한다.

**Files:**
- Modify: `src/md_converter/hwp/hwp5/_table_utils.py` (`_escape_cell_for_table`, `_serialize_nt`, `_serialize_flat` 추가)
- Modify: `src/md_converter/hwp/hwp5/_parser.py` (테이블 상태 → 컨텍스트 스택)
- Test: `tests/hwp/test_hwp5.py` (중첩 표 픽스처 + serialize 단위 테스트 추가)

- [ ] **Step 1: serialize 헬퍼 + 이스케이프 보정 테스트 작성**

Append to `tests/hwp/test_hwp5.py`:

```python
# ── nested-table serialization helpers ────────────────────────────────────────

from md_converter.hwp.hwp5._table_utils import (
    _serialize_flat,
    _serialize_nt,
    table_to_md,
)


def test_serialize_nt_basic():
    assert _serialize_nt([["a", "b"], ["c", "d"]]) == "[[NT:a|b;c|d]]"


def test_serialize_nt_empty_returns_blank():
    assert _serialize_nt([["", ""]]) == ""


def test_serialize_flat_joins_cells():
    assert _serialize_flat([["a", "b"], ["c"]]) == "a b c"


def test_table_to_md_keeps_nt_marker_pipes_intact():
    # A cell holding an [[NT:]] marker must NOT have its internal pipes escaped,
    # otherwise extract_nested_tables can't parse it.
    md = table_to_md([["[[NT:a|b;c|d]]"]])
    assert "[[NT:a|b;c|d]]" in md
    assert "\\|" not in md


def test_table_to_md_escapes_normal_cell_pipes():
    md = table_to_md([["a|b"]])
    assert "a\\|b" in md
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/hwp/test_hwp5.py -k "serialize or nt_marker or normal_cell" -v`
Expected: FAIL with `ImportError: cannot import name '_serialize_flat'` (및 `_serialize_nt`)

- [ ] **Step 3: `_table_utils.py` 구현**

Replace the entire contents of `src/md_converter/hwp/hwp5/_table_utils.py` with:

```python
"""HWP5 table: records → GFM conversion."""
from __future__ import annotations

from .._common import _escape_cell


def _escape_cell_for_table(s: str) -> str:
    """Escape | for GFM, but leave [[NT:...]] markers intact.

    Mirrors hwpx/_table_utils._escape_cell_for_table so a nested-table marker
    embedded in a cell survives for extract_nested_tables() to parse.
    """
    if "[[NT:" in s:
        return s
    return _escape_cell(s)


def table_to_md(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    col_count = max(len(r) for r in rows)
    lines: list[str] = []
    for i, row in enumerate(rows):
        padded = row + [""] * (col_count - len(row))
        escaped = [_escape_cell_for_table(c) for c in padded]
        lines.append("| " + " | ".join(escaped) + " |")
        if i == 0:
            lines.append("|" + "|".join(" --- " for _ in padded) + "|")
    return "\n".join(lines)


def _serialize_nt(rows: list[list[str]]) -> str:
    """Serialize a depth-1 nested table to the shared [[NT:...]] marker.

    Returns "" when the table has no non-blank content.
    """
    if not any(cell.strip() for row in rows for cell in row):
        return ""
    return "[[NT:" + ";".join("|".join(row) for row in rows) + "]]"


def _serialize_flat(rows: list[list[str]]) -> str:
    """Flatten a deeply-nested (depth >= 2) table to plain space-joined text."""
    return " ".join(cell.strip() for row in rows for cell in row if cell.strip())
```

- [ ] **Step 4: serialize 테스트 통과 확인**

Run: `python -m pytest tests/hwp/test_hwp5.py -k "serialize or nt_marker or normal_cell" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 중첩 표 파서 테스트 작성 (실패 예상)**

Append to `tests/hwp/test_hwp5.py` (기존 헬퍼 `_make_record`, `_list_header_payload` 재사용):

```python
# ── _parse_section: nested table ──────────────────────────────────────────────

def _u16(s: str) -> bytes:
    return s.encode("utf-16-le")


def _table_ctrl(level: int) -> bytes:
    # CTRL_HEADER with ctrl_id b" lbt" + 8 padding bytes
    return _make_record(0x47, level, b" lbt" + b"\x00" * 8)


def test_nested_table_serialized_into_parent_cell():
    """A table inside a cell becomes an [[NT:]] marker; outer rows are NOT lost."""
    rec = b""
    rec += _table_ctrl(0)                                   # outer table @0
    rec += _make_record(0x4D, 1, b"\x00" * 4 + _struct.pack("<2H", 2, 2))  # TABLE_BODY
    rec += _make_record(0x48, 1, _list_header_payload(0, 0)) + _make_record(0x43, 2, _u16("구분"))
    rec += _make_record(0x48, 1, _list_header_payload(0, 1)) + _make_record(0x43, 2, _u16("세부"))
    rec += _make_record(0x48, 1, _list_header_payload(1, 0)) + _make_record(0x43, 2, _u16("본인부담"))
    # outer cell (1,1) holds text + a nested 2x2 table
    rec += _make_record(0x48, 1, _list_header_payload(1, 1)) + _make_record(0x43, 2, _u16("상세"))
    rec += _table_ctrl(2)                                   # nested table @2
    rec += _make_record(0x4D, 3, b"\x00" * 4 + _struct.pack("<2H", 2, 2))
    rec += _make_record(0x48, 3, _list_header_payload(0, 0)) + _make_record(0x43, 4, _u16("항목"))
    rec += _make_record(0x48, 3, _list_header_payload(0, 1)) + _make_record(0x43, 4, _u16("금액"))
    rec += _make_record(0x48, 3, _list_header_payload(1, 0)) + _make_record(0x43, 4, _u16("외래"))
    rec += _make_record(0x48, 3, _list_header_payload(1, 1)) + _make_record(0x43, 4, _u16("1000"))
    rec += _make_record(0x42, 0, b"")                       # PARA_HEADER @0 closes all

    parts = _parse_section(rec, {}, {}, [])

    assert len(parts) == 1, f"expected 1 outer table, got {len(parts)}: {parts}"
    table = parts[0]
    # nested table serialized as [[NT:]] with pipes intact (not escaped)
    assert "[[NT:항목|금액;외래|1000]]" in table
    # outer content preserved — bug fix: outer rows not overwritten
    assert "구분" in table and "세부" in table and "본인부담" in table
    data_rows = [l for l in table.splitlines() if l.startswith("|") and "---" not in l]
    assert len(data_rows) == 2, f"outer table should keep 2 rows:\n{table}"


def test_nested_table_end_to_end_separation():
    """parser → extract_nested_tables yields a referenced standalone table."""
    from md_converter.nested_tables import extract_nested_tables

    rec = b""
    rec += _table_ctrl(0)
    rec += _make_record(0x48, 1, _list_header_payload(0, 0)) + _make_record(0x43, 2, _u16("머리"))
    rec += _make_record(0x48, 1, _list_header_payload(1, 0)) + _make_record(0x43, 2, _u16("본문"))
    rec += _table_ctrl(2)
    rec += _make_record(0x48, 3, _list_header_payload(0, 0)) + _make_record(0x43, 4, _u16("k"))
    rec += _make_record(0x48, 3, _list_header_payload(1, 0)) + _make_record(0x43, 4, _u16("v"))
    rec += _make_record(0x42, 0, b"")

    md = extract_nested_tables("\n\n".join(_parse_section(rec, {}, {}, [])))
    assert "→ 표 1" in md
    assert "**[표 1]**" in md
    assert "[[NT:" not in md


def test_deeply_nested_table_flattened():
    """A table nested 2 levels deep is flattened to text (only depth-1 → [[NT:]])."""
    rec = b""
    rec += _table_ctrl(0)                                            # outer @0
    rec += _make_record(0x48, 1, _list_header_payload(0, 0)) + _make_record(0x43, 2, _u16("L0"))
    rec += _table_ctrl(2)                                            # mid @2 (depth 1)
    rec += _make_record(0x48, 3, _list_header_payload(0, 0)) + _make_record(0x43, 4, _u16("L1"))
    rec += _table_ctrl(4)                                            # inner @4 (depth 2)
    rec += _make_record(0x48, 5, _list_header_payload(0, 0)) + _make_record(0x43, 6, _u16("L2"))
    rec += _make_record(0x42, 0, b"")                                # close all

    parts = _parse_section(rec, {}, {}, [])
    table = parts[0]
    # only ONE marker — the depth-2 table was flattened into the depth-1 marker
    assert table.count("[[NT:") == 1
    assert "[[NT:L1 L2]]" in table
```

- [ ] **Step 6: 파서 테스트 실패 확인**

Run: `python -m pytest tests/hwp/test_hwp5.py -k "nested or deeply" -v`
Expected: FAIL — 현재 파서는 중첩 표 미지원이라 `[[NT:...]]`가 출력에 없고 바깥 표 행이 유실됨

- [ ] **Step 7: `_parser.py` import + 컨텍스트 스택 dataclass 추가**

In `src/md_converter/hwp/hwp5/_parser.py`, update the imports near the top of the file.

Change:

```python
import io
import re
import struct
```

to:

```python
import io
import re
import struct
from dataclasses import dataclass, field
```

Change:

```python
from ._table_utils import table_to_md
```

to:

```python
from ._table_utils import _serialize_flat, _serialize_nt, table_to_md
```

Add this dataclass immediately after the `_PICTURE_BIN_DATA_ID_OFFSET = 71` line (before `_para_text_from_payload`):

```python
@dataclass
class _TableCtx:
    """One open table while parsing; the stack tracks nesting depth."""
    ctrl_lvl: int
    rows: list[list[str]] = field(default_factory=list)
    current_row: list[str] = field(default_factory=list)
    current_cell_parts: list[str] = field(default_factory=list)
    in_cell: bool = False
    row_addr: int = -1
```

- [ ] **Step 8: `_parse_section` 전체 교체 (단일 상태 → 스택)**

In `src/md_converter/hwp/hwp5/_parser.py`, replace the **entire** `_parse_section` function (from `def _parse_section(` through its `return parts`) with:

```python
def _parse_section(
    data: bytes,
    bin_entries: dict[int, _BinEntry],
    bin_streams: dict[int, tuple[bytes, str]],
    images: list[ImageItem],
) -> list[str]:
    """Parse one BodyText section stream into a list of markdown blocks.

    Tables are tracked with a stack so nested tables are handled: the outermost
    table renders to GFM; a depth-1 nested table is serialized into its parent
    cell as an [[NT:...]] marker; depth-2+ tables are flattened to plain text.
    """
    parts: list[str] = []
    table_stack: list[_TableCtx] = []

    # ── GSO (drawing / picture) state ─────────────────────────────────────────
    in_gso          = False
    gso_level       = -1
    gso_text_parts: list[str] = []
    gso_had_image   = False
    gso_records:    list[tuple[int, int, bytes]] = []

    def _close_top() -> None:
        """Pop the innermost table and route it to its parent cell or to parts."""
        ctx = table_stack.pop()
        if ctx.in_cell:
            ctx.current_row.append(" ".join(ctx.current_cell_parts))
        if ctx.current_row:
            ctx.rows.append(ctx.current_row)
        if not table_stack:
            md = table_to_md(ctx.rows)
            if md:
                parts.append(md)
        elif len(table_stack) == 1:
            nt = _serialize_nt(ctx.rows)
            if nt:
                table_stack[-1].current_cell_parts.append(nt)
        else:
            flat = _serialize_flat(ctx.rows)
            if flat:
                table_stack[-1].current_cell_parts.append(flat)

    for tag_id, level, payload in _iter_records(data):
        # Any record at or above the innermost table's level closes that table.
        while table_stack and level <= table_stack[-1].ctrl_lvl:
            _close_top()

        # ── control header dispatch ───────────────────────────────────────────
        if tag_id == _TAG_CTRL_HEADER:
            ctrl = payload[:4] if len(payload) >= 4 else b""
            if ctrl == _CTRL_GSO:
                in_gso = True
                gso_level = level
                gso_text_parts = []
                gso_had_image = False
                gso_records = []
            elif ctrl == _CTRL_TABLE:
                table_stack.append(_TableCtx(ctrl_lvl=level))

        # GSO 내부 레코드 누적
        if in_gso and level > gso_level:
            gso_records.append((tag_id, level, payload))

        # ── picture inside GSO ────────────────────────────────────────────────
        if in_gso and tag_id == _TAG_SHAPE_PICTURE:
            if len(payload) >= _PICTURE_BIN_DATA_ID_OFFSET + 2:
                bin_data_id = struct.unpack_from("<H", payload, _PICTURE_BIN_DATA_ID_OFFSET)[0]
                _emit_image(bin_data_id, bin_entries, bin_streams, images, parts)
            gso_had_image = True
            in_gso = False
            gso_text_parts = []

        # ── GSO exit: level returns to or above the GSO opener ────────────────
        if in_gso and level <= gso_level and tag_id != _TAG_CTRL_HEADER:
            if not gso_had_image:
                diagram_graph = extract_diagram(gso_records)
                if diagram_graph is not None:
                    mermaid = graph_to_mermaid(diagram_graph)
                    if mermaid:
                        parts.append(f"```mermaid\n{mermaid}\n```")
                elif gso_text_parts:
                    drawing_text = "\n".join(gso_text_parts)
                    parts.append(f"```hwp-drawing\n{drawing_text}\n```")
            in_gso = False
            gso_text_parts = []
            gso_had_image = False
            gso_records = []

        # ── LIST_HEADER: cell boundary in the innermost table ─────────────────
        if (
            table_stack
            and tag_id == _TAG_LIST_HEADER
            and level == table_stack[-1].ctrl_lvl + 1
        ):
            top = table_stack[-1]
            row_addr = struct.unpack_from("<H", payload, 10)[0] if len(payload) >= 12 else 0
            if top.in_cell:
                top.current_row.append(" ".join(top.current_cell_parts))
                top.current_cell_parts = []
                if row_addr != top.row_addr:
                    top.rows.append(top.current_row)
                    top.current_row = []
            top.row_addr = row_addr
            top.in_cell = True

        # ── paragraph text ────────────────────────────────────────────────────
        elif tag_id == _TAG_PARA_TEXT:
            text = _para_text_from_payload(payload)
            if text:
                if table_stack and table_stack[-1].in_cell:
                    table_stack[-1].current_cell_parts.append(text)
                elif in_gso:
                    gso_text_parts.append(text)
                elif not table_stack:
                    parts.append(text)

    while table_stack:
        _close_top()

    return parts
```

Note: the `_TAG_TABLE_BODY` branch is intentionally dropped — its only effect was reading an unused column count. Row/column shape is derived from the rows in `table_to_md`.

- [ ] **Step 9: 중첩 표 + 회귀 테스트 통과 확인**

Run: `python -m pytest tests/hwp/test_hwp5.py -v`
Expected: PASS — 신규 중첩/깊은중첩 테스트 통과 **그리고** 기존 `test_merged_cell_row_detection` 회귀 통과

- [ ] **Step 10: HWP5 다이어그램 회귀 확인**

Run: `python -m pytest tests/hwp/test_hwp5_diagram.py -v`
Expected: PASS (GSO/도형 처리 미변경 확인)

- [ ] **Step 11: 커밋**

```bash
git add src/md_converter/hwp/hwp5/_parser.py src/md_converter/hwp/hwp5/_table_utils.py tests/hwp/test_hwp5.py
git commit -m "feat(hwp5): 테이블 컨텍스트 스택으로 중첩 표 감지 — [[NT:]] 직렬화 + 바깥 표 행 유실 버그 수정"
```

---

## Task 4: 전체 테스트 스위트 회귀 확인

**Files:** 없음 (검증 전용)

- [ ] **Step 1: 전체 테스트 실행**

Run: `python -m pytest -v`
Expected: PASS (실파일 의존 테스트 `test_real_hwp5_with_images`는 샘플 부재 시 SKIP — 정상)

- [ ] **Step 2: 제거 심볼 잔존 참조 점검**

Run: `grep -rn "restructure_nested_tables\|_call_llm\b\|_PROMPT_TEMPLATE" src/ tests/`
Expected: 출력 없음 (완전 제거 확인)

---

## Manual Validation (구현 후, 자동화 아님)

스펙의 검증 항목 — 코퍼스 접근이 가능한 환경에서 수동 확인:

- **01_image HWPX 재변환:** 중첩 표 LLM 호출 31회 → **0회**, 변환 시간 261초 → 수 초. 출력에 `→ 표 N` 참조와 `**[표 N]**` 분리 표 존재.
- **HWP5:** 중첩 표를 포함한 샘플 문서(`clic` 버킷에서 탐색)로 분리 동작 확인.

---

## Self-Review (작성자 점검 결과)

**1. Spec coverage:**
- 출력 형태(`→ 표 N` + `**[표 N]**`, 부모 블록 뒤 배치) → Task 1.
- 공유 마커 `[[NT:]]` → Task 1(소비), Task 3(HWP5 생성), HWPX(기존 생성, 무변경).
- HWP5 컨텍스트 스택 + depth 분기(0=GFM, 1=`[[NT:]]`, ≥2=평탄) → Task 3.
- `convert()` 배선 교체 + LLM 코드 제거 → Task 2.
- 번호/라벨/빈 표/멀티/이스케이프/malformed 엣지 → Task 1 테스트.
- HWP5 `[[NT:]]` 파이프 이스케이프 보정(`_escape_cell_for_table`) → Task 3 (스펙엔 암묵, 구현상 필수라 명시 추가).
- 테스트(단위/회귀/HWP5 픽스처/깊은 중첩) → Task 1·2·3.

**2. Placeholder scan:** TBD/TODO/"적절히 처리" 없음. 모든 코드 스텝에 완전한 코드 포함.

**3. Type consistency:** `extract_nested_tables(md: str) -> str`, `_serialize_nt/_serialize_flat(rows: list[list[str]]) -> str`, `_TableCtx` 필드(`ctrl_lvl/rows/current_row/current_cell_parts/in_cell/row_addr`)가 Task 3 본문과 테스트에서 일관. 마커 문자열 `[[NT:`/`]]`, 참조 `→ 표 N`, 헤더 `**[표 N]**`가 생성부(Task 3)·소비부(Task 1)·테스트에서 동일.
