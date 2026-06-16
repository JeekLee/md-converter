"""Unit tests for HWP5 (binary OLE) parser."""
from __future__ import annotations

import io
import struct
import zlib

import pytest

pytest.importorskip("olefile")

import struct as _struct

from md_converter.hwp.hwp5._records import _decompress, _iter_records
from md_converter.hwp.hwp5._parser import _para_text_from_payload, _parse_section
from md_converter.hwp.hwp5 import parse


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_header(tag_id: int, level: int, size: int) -> bytes:
    header = (tag_id & 0x3FF) | ((level & 0x3FF) << 10) | ((size & 0xFFF) << 20)
    return struct.pack("<I", header)


def _make_record(tag_id: int, level: int, payload: bytes) -> bytes:
    size = len(payload)
    if size < 0xFFF:
        return _make_header(tag_id, level, size) + payload
    return _make_header(tag_id, level, 0xFFF) + struct.pack("<I", size) + payload


# ── _iter_records ─────────────────────────────────────────────────────────────

def test_iter_records_basic():
    rec = _make_record(0x43, 0, b"hello")
    records = list(_iter_records(rec))
    assert len(records) == 1
    tag_id, level, payload = records[0]
    assert tag_id == 0x43
    assert level == 0
    assert payload == b"hello"


def test_iter_records_multiple():
    data = _make_record(0x42, 0, b"") + _make_record(0x43, 1, b"ab")
    records = list(_iter_records(data))
    assert len(records) == 2
    assert records[0][0] == 0x42
    assert records[1][0] == 0x43
    assert records[1][1] == 1
    assert records[1][2] == b"ab"


def test_iter_records_extended_size():
    payload = b"x" * 0xFFF  # exactly 4095 — triggers extended size
    rec = _make_record(0x43, 0, payload)
    records = list(_iter_records(rec))
    assert len(records) == 1
    assert records[0][2] == payload


def test_iter_records_level_bits():
    # level uses bits 10-19 (10 bits); test max level 1023
    rec = _make_record(0x43, 1023, b"")
    _, level, _ = list(_iter_records(rec))[0]
    assert level == 1023


# ── _decompress ───────────────────────────────────────────────────────────────

def test_decompress_raw_deflate():
    original = b"hello world " * 100
    compressed = zlib.compress(original)[2:-4]  # strip zlib header/checksum
    assert _decompress(compressed) == original


def test_decompress_fallback_zlib():
    original = b"hello world " * 100
    compressed = zlib.compress(original)  # standard zlib
    assert _decompress(compressed) == original


# ── _para_text_from_payload ───────────────────────────────────────────────────

def _utf16(s: str) -> bytes:
    return s.encode("utf-16-le")


def test_para_text_plain():
    payload = _utf16("hello")
    assert _para_text_from_payload(payload) == "hello"


def test_para_text_strips_whitespace():
    payload = _utf16("  hello  ")
    assert _para_text_from_payload(payload) == "hello"


def test_para_text_skips_inline_control():
    # A control char U+0001 followed by 7 parameter chars, then real text.
    # The 7 param chars happen to look like ordinary chars — they must be skipped.
    ctrl_block = "\x01" + "AAAAAAA"  # 8 chars total
    payload = _utf16(ctrl_block + "real text")
    assert _para_text_from_payload(payload) == "real text"


def test_para_text_multiple_controls():
    ctrl = "\x02" + "B" * 7
    payload = _utf16(ctrl + "foo" + ctrl + "bar")
    assert _para_text_from_payload(payload) == "foobar"


def test_para_text_control_at_end_no_crash():
    # Control char with fewer than 7 remaining chars — should not crash
    payload = _utf16("\x01AB")  # only 2 trailing chars, not 7
    # Should return empty (all consumed by skip) without raising
    result = _para_text_from_payload(payload)
    assert isinstance(result, str)


def test_para_text_odd_length_payload():
    payload = _utf16("hi") + b"\x00"  # odd byte appended
    assert _para_text_from_payload(payload) == "hi"


# ── _parse_section: merged-cell table ────────────────────────────────────────

def _list_header_payload(row_addr: int, col_addr: int) -> bytes:
    # u16[0..3] = reserved zeros; u16[4] = colAddr; u16[5] = rowAddr
    return _struct.pack("<6H", 0, 0, 0, 0, col_addr, row_addr)


def test_merged_cell_row_detection():
    """Header row with colspan=2 (one LIST_HEADER) must still produce 2 table rows."""
    records = b""
    # CTRL_HEADER for table at level 0
    records += _make_record(0x47, 0, b" lbt" + b"\x00" * 8)
    # TABLE_BODY at level 1: rowCount=2, colCount=2
    records += _make_record(0x4D, 1, b"\x00" * 4 + _struct.pack("<2H", 2, 2))
    # Merged header cell: rowAddr=0 (spans both columns — only ONE LIST_HEADER)
    records += _make_record(0x48, 1, _list_header_payload(0, 0))
    records += _make_record(0x43, 2, "헤더".encode("utf-16-le"))
    # Row 1, col 0
    records += _make_record(0x48, 1, _list_header_payload(1, 0))
    records += _make_record(0x43, 2, "col1".encode("utf-16-le"))
    # Row 1, col 1
    records += _make_record(0x48, 1, _list_header_payload(1, 1))
    records += _make_record(0x43, 2, "col2".encode("utf-16-le"))
    # PARA_HEADER at level 0 closes the table (level <= table_ctrl_lvl=0)
    records += _make_record(0x42, 0, b"")

    parts = _parse_section(records, {}, {}, [])

    assert len(parts) == 1, f"expected 1 table block, got {len(parts)}: {parts}"
    rows = [l for l in parts[0].splitlines() if l.startswith("|") and "---" not in l]
    assert len(rows) == 2, f"expected 2 data rows:\n{parts[0]}"
    assert "헤더" in rows[0]
    assert "col1" in rows[1] and "col2" in rows[1]


# ── integration with real file ────────────────────────────────────────────────

def test_real_hwp5_with_images(tmp_path):
    import os
    sample = "/tmp/img_test_20231229-6-0018/f.hwp"
    if not os.path.exists(sample):
        pytest.skip("real HWP5 sample not available")

    data = open(sample, "rb").read()
    md, images = parse(data)

    assert len(images) == 2
    for img in images:
        # MIME detection must succeed (not application/octet-stream)
        assert img.mime in ("image/png", "image/jpeg", "image/gif", "image/bmp")
        assert len(img.data) > 0
        # Image placeholders must appear in the output
        assert f"[[RHWP_IMAGE:{img.idx}]]" in md

    # Korean text must be clean (no garbage)
    assert "보건복지부" in md
    # No stray ASCII junk from control char parameter bytes
    assert "捤" not in md


# ── nested-table serialization helpers ────────────────────────────────────────

from md_converter.hwp.hwp5._table_utils import (
    _serialize_flat,
    _serialize_nt,
    table_to_md,
)


def test_serialize_nt_basic():
    assert _serialize_nt([["a", "b"], ["c", "d"]]).startswith("[[NT64:")


def test_serialize_nt_empty_returns_blank():
    assert _serialize_nt([["", ""]]) == ""


def test_serialize_flat_joins_cells():
    assert _serialize_flat([["a", "b"], ["c"]]) == "a b c"


def test_table_to_md_keeps_nt_marker_pipes_intact():
    # A cell holding an [[NT:]] marker must NOT have its internal pipes escaped,
    # otherwise extract_nested_tables can't parse it.
    marker = _serialize_nt([["a", "b"], ["c", "d"]])
    md = table_to_md([[marker]])
    assert marker in md
    assert "\\|" not in md


def test_table_to_md_escapes_normal_cell_pipes():
    md = table_to_md([["a|b"]])
    assert "a\\|b" in md


# ── _parse_section: nested table ──────────────────────────────────────────────

def _u16(s: str) -> bytes:
    return s.encode("utf-16-le")


def _table_ctrl(level: int) -> bytes:
    # CTRL_HEADER with ctrl_id b" lbt" + 8 padding bytes
    return _make_record(0x47, level, b" lbt" + b"\x00" * 8)


def test_nested_table_serialized_into_parent_cell():
    """A table inside a cell becomes a nested-table marker; outer rows are NOT lost."""
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
    assert "[[NT64:" in table
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
    assert "[[NT64:" not in md


def test_deeply_nested_table_flattened():
    """A table nested 2 levels deep is flattened to text (only depth-1 gets a marker)."""
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
    assert table.count("[[NT64:") == 1
    from md_converter.nested_tables import extract_nested_tables
    out = extract_nested_tables(table)
    assert "L1 L2" in out
