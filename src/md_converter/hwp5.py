"""HWP5 (binary OLE) → Markdown converter.

Requires: pip install olefile  (or: pip install "md-converter[hwp5]")

HWP5 structure (OLE compound file):
  FileHeader         — version + property flags (bit 0: compressed)
  BodyText/Section0  — HWPF record stream for each section
  BodyText/Section1
  ...
  BinData/BIN*.{jpg,png,...}   — embedded images

Each BodyText stream is optionally zlib-compressed and consists of a sequence
of HWPF records:

  [4-byte header: tag_id(10) | level(2) | size(20)]   ← little-endian uint32
  [payload: size bytes]
  (if size == 0xFFFFF → read next 4 bytes as actual size)

Mirrors rhwp's extract_document_markdown_with_images_native logic.
"""
from __future__ import annotations

import struct
import zlib
from typing import Generator

try:
    import olefile
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "HWP5 support requires 'olefile'. "
        "Install with: pip install 'md-converter[hwp5]'"
    ) from exc

# ── HWPF tag IDs (from HWP5 open format spec v5.0) ───────────────────────
_TAG_PARA_HEADER = 0x42   # 66  — paragraph header (char count, control count…)
_TAG_PARA_TEXT = 0x43     # 67  — paragraph text body (UTF-16LE)
_TAG_CTRL_HEADER = 0x47   # 71  — inline control (table, picture, …)
_TAG_LIST_HEADER = 0x48   # 72  — cell / list context header
_TAG_TABLE = 0x54         # 84  — table property record (inside CTRL_HEADER group)

# Control ID for a table object stored in CTRL_HEADER payload bytes 0-3
_CTRL_TABLE = b"tble"

# ── record iterator ────────────────────────────────────────────────────────

def _iter_records(data: bytes) -> Generator[tuple[int, int, bytes], None, None]:
    """Yield (tag_id, level, payload) for each HWPF record in *data*."""
    offset = 0
    length = len(data)
    while offset + 4 <= length:
        header = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        tag_id = header & 0x3FF
        level = (header >> 10) & 0x3
        size = header >> 12  # 20-bit field; max value 0xFFFFF
        if size == 0xFFFFF:
            if offset + 4 > length:
                break
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        payload = data[offset : offset + size]
        offset += size
        yield tag_id, level, payload


# ── section-stream decoder ─────────────────────────────────────────────────

def _decode_stream(raw: bytes, compressed: bool) -> bytes:
    if not compressed:
        return raw
    return zlib.decompress(raw)


# ── markdown helpers (same logic as hwpx.py) ──────────────────────────────

import re


def _escape_cell(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("|", "\\|")).strip()


def _table_to_md(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    col_count = max(len(r) for r in rows)
    lines: list[str] = []
    for i, row in enumerate(rows):
        padded = row + [""] * (col_count - len(row))
        escaped = [_escape_cell(c) for c in padded]
        lines.append("| " + " | ".join(escaped) + " |")
        if i == 0:
            lines.append("|" + "|".join(" --- " for _ in padded) + "|")
    return "\n".join(lines)


# ── section parser ─────────────────────────────────────────────────────────

def _parse_section(data: bytes) -> list[str]:
    """Parse one BodyText section stream into a list of markdown blocks."""
    parts: list[str] = []

    # State machine: we track nesting level to associate PARA_TEXT with
    # their context (top-level paragraph vs table cell).
    #
    # level 0: section root
    # level 1: top-level paragraphs / controls
    # level 2: inside a control (table rows, etc.)
    # level 3: table cells (LIST_HEADER contexts)
    # level 4: paragraphs inside cells
    #
    # We collect PARA_TEXT at top level as paragraph blocks,
    # and accumulate table structure when inside a table control.

    in_table = False
    table_rows: list[list[str]] = []
    current_row: list[str] = []
    current_cell_parts: list[str] = []
    cell_depth = 0  # how many LIST_HEADER levels deep we are

    prev_level = 0

    for tag_id, level, payload in _iter_records(data):
        if tag_id == _TAG_CTRL_HEADER and level == 1:
            ctrl_id = payload[:4] if len(payload) >= 4 else b""
            if ctrl_id == _CTRL_TABLE:
                in_table = True
                table_rows = []
                current_row = []
                current_cell_parts = []
                cell_depth = 0

        elif tag_id == _TAG_LIST_HEADER and in_table:
            if level == 3:
                # New row: flush previous row when we move from one row to the next
                # (rows start at level 2 via a separate tr record; cells at level 3)
                # Simplified: treat each LIST_HEADER at level 3 as a new cell.
                current_cell_parts = []
                cell_depth += 1
            elif level == 2:
                # Row-level list header — flush previous row's last cell and start row
                if current_cell_parts or current_row:
                    if current_cell_parts:
                        current_row.append(" ".join(current_cell_parts))
                        current_cell_parts = []
                    if current_row:
                        table_rows.append(current_row)
                current_row = []
                cell_depth = 0

        elif tag_id == _TAG_PARA_TEXT:
            text = _para_text_from_payload(payload)
            if not text:
                continue
            if in_table and cell_depth > 0:
                current_cell_parts.append(text)
            elif in_table and cell_depth == 0:
                pass  # para before first cell — skip
            else:
                # Top-level paragraph
                parts.append(text)

        elif tag_id == _TAG_PARA_HEADER and level == 1 and in_table:
            # Entering a new cell paragraph at the cell level
            pass

        # Detect end of table: when we return to level 1 with a non-table tag
        # after having been in a table.
        if in_table and level == 1 and tag_id not in (
            _TAG_CTRL_HEADER,
            _TAG_LIST_HEADER,
            _TAG_PARA_HEADER,
            _TAG_PARA_TEXT,
            _TAG_TABLE,
        ):
            # Flush last cell and row
            if current_cell_parts:
                current_row.append(" ".join(current_cell_parts))
                current_cell_parts = []
            if current_row:
                table_rows.append(current_row)
            md = _table_to_md(table_rows)
            if md:
                parts.append(md)
            in_table = False
            table_rows = []
            current_row = []

        prev_level = level

    # Flush in case the table is the last element in the section
    if in_table:
        if current_cell_parts:
            current_row.append(" ".join(current_cell_parts))
        if current_row:
            table_rows.append(current_row)
        md = _table_to_md(table_rows)
        if md:
            parts.append(md)

    return parts


def _para_text_from_payload(payload: bytes) -> str:
    """Decode PARA_TEXT payload: UTF-16LE, filter HWP control chars."""
    if len(payload) % 2 != 0:
        payload = payload[:-1]
    try:
        text = payload.decode("utf-16-le")
    except UnicodeDecodeError:
        return ""
    # Filter control chars ≤ U+001F (HWP inline-object placeholders)
    return "".join(c for c in text if c > "").strip()


# ── file header ────────────────────────────────────────────────────────────

_HWP5_SIGNATURE = b"HWP Document File\x00"
_HWP5_SIGNATURE_LEN = 32  # padded to 32 bytes in the file


def _read_flags(ole: "olefile.OleFileIO") -> int:
    """Read the 4-byte property flags from FileHeader (offset 36)."""
    data = ole.openstream("FileHeader").read()
    if len(data) < 40:
        return 0
    return struct.unpack_from("<I", data, 36)[0]


def _section_streams(ole: "olefile.OleFileIO") -> list[str]:
    """Return sorted BodyText/Section* stream paths."""
    streams = [
        "/".join(entry)
        for entry in ole.listdir(streams=True)
        if len(entry) == 2 and entry[0] == "BodyText" and entry[1].startswith("Section")
    ]
    return sorted(streams, key=lambda s: int(re.search(r"\d+", s.split("/")[1]).group()))


# ── public entries ─────────────────────────────────────────────────────────

def parse(data: bytes) -> tuple[str, list]:
    """Convert HWP5 bytes to (markdown_with_placeholders, image_list).

    HWP5 image extraction is not yet implemented; image_list is always empty.
    Requires the 'olefile' package.
    """
    import io as _io

    ole = olefile.OleFileIO(_io.BytesIO(data))
    try:
        flags = _read_flags(ole)
        compressed = bool(flags & 0x1)

        parts: list[str] = []
        for stream_name in _section_streams(ole):
            raw = ole.openstream(stream_name).read()
            decoded = _decode_stream(raw, compressed)
            parts.extend(_parse_section(decoded))
    finally:
        ole.close()

    return "\n\n".join(parts), []


def convert(data: bytes) -> str:
    """Convert HWP5 bytes to Markdown (text + tables; images dropped).

    For full pipeline with image upload and LLM table restructuring,
    use md_converter.convert() instead.
    Requires the 'olefile' package.
    """
    md, _ = parse(data)
    return md
