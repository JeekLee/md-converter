"""HWP5 (binary OLE) → Markdown converter.

Requires: pip install olefile  (or: pip install "md-converter[hwp5]")

HWP5 structure (OLE compound file):
  FileHeader         — version + property flags (bit 0: compressed)
  DocInfo            — document metadata including BinData table
  BodyText/Section0  — HWPF record stream for each section
  BodyText/Section1
  ...
  BinData/BIN*.{ext}   — embedded images

HWPF record header (32-bit LE):
  bits  0-9  : tag_id  (10 bits)
  bits 10-19 : level   (10 bits, 0 = top-level)
  bits 20-31 : size    (12 bits; if 0xFFF → read next 4 bytes as actual size)

BodyText/DocInfo streams are compressed with raw deflate (wbits=-15).
"""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Generator

try:
    import olefile
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "HWP5 support requires 'olefile'. "
        "Install with: pip install 'md-converter[hwp5]'"
    ) from exc

# ── HWPF tag IDs (HWPTAG_BEGIN=0x10, offsets from spec) ──────────────────────
_TAG_BIN_DATA          = 0x12  # 18  — DocInfo: binary data entry
_TAG_PARA_HEADER       = 0x42  # 66  — paragraph header
_TAG_PARA_TEXT         = 0x43  # 67  — paragraph text body (UTF-16LE)
_TAG_CTRL_HEADER       = 0x47  # 71  — inline control (table, picture, …)
_TAG_LIST_HEADER       = 0x48  # 72  — cell / list context header
_TAG_TABLE             = 0x54  # 84  — table property record
_TAG_SHAPE_COMPONENT   = 0x4C  # 76  — shape component (container)
_TAG_SHAPE_PICTURE     = 0x55  # 85  — picture-specific shape data

# Control IDs stored as LE DWORD in CTRL_HEADER payload bytes 0-3.
# ctrl_id(s) = big-endian ASCII → stored as LE bytes in the file.
# e.g. ctrl_id("tbl ") = 0x74626C20 → file bytes [0x20,0x6C,0x62,0x74] = b" lbt"
_CTRL_TABLE = b" lbt"   # ctrl_id(b"tbl ")
_CTRL_GSO   = b" osg"   # ctrl_id(b"gso ") — General Shape Object (picture)

# Offset of bin_data_id (u16) inside a TAG_SHAPE_PICTURE payload
_PICTURE_BIN_DATA_ID_OFFSET = 71


# ── image item ────────────────────────────────────────────────────────────────

@dataclass
class ImageItem:
    idx: int        # 1-based — matches [[RHWP_IMAGE:{idx}]] token
    data: bytes
    mime: str
    ext: str


# ── record iterator ────────────────────────────────────────────────────────────

def _iter_records(data: bytes) -> Generator[tuple[int, int, bytes], None, None]:
    """Yield (tag_id, level, payload) for each HWPF record in *data*.

    Record header layout (32-bit LE):
      bits  0-9  : tag_id
      bits 10-19 : level
      bits 20-31 : size  (0xFFF = extended; next 4 bytes = actual size)
    """
    offset = 0
    length = len(data)
    while offset + 4 <= length:
        header = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        tag_id = header & 0x3FF
        level  = (header >> 10) & 0x3FF
        size   = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if offset + 4 > length:
                break
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        payload = data[offset : offset + size]
        offset += size
        yield tag_id, level, payload


# ── decompression ─────────────────────────────────────────────────────────────

def _decompress(raw: bytes) -> bytes:
    """Raw deflate (HWP5 standard), fallback to standard zlib."""
    try:
        return zlib.decompress(raw, -15)
    except zlib.error:
        return zlib.decompress(raw)


def _decode_stream(raw: bytes, compressed: bool) -> bytes:
    if not compressed:
        return raw
    return _decompress(raw)


# ── markdown helpers (same logic as hwpx.py) ──────────────────────────────────

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


# ── DocInfo BinData table ─────────────────────────────────────────────────────

def _read_hwp_string(data: bytes, offset: int) -> tuple[str, int]:
    """Read a HWP length-prefixed UTF-16LE string. Returns (text, new_offset)."""
    if offset + 2 > len(data):
        return "", offset
    length = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    byte_len = length * 2
    if offset + byte_len > len(data):
        return "", offset + byte_len
    text = data[offset : offset + byte_len].decode("utf-16-le", errors="replace")
    return text, offset + byte_len


@dataclass
class _BinEntry:
    storage_id: int
    ext: str


def _parse_doc_info_bin_data(ole: "olefile.OleFileIO", compressed: bool) -> dict[int, _BinEntry]:
    """Parse DocInfo stream → {1-based bin_data_id: _BinEntry(storage_id, ext)}."""
    if not ole.exists("DocInfo"):
        return {}
    raw = ole.openstream("DocInfo").read()
    data = _decode_stream(raw, compressed)

    entries: dict[int, _BinEntry] = {}
    seq = 1
    for tag_id, _level, payload in _iter_records(data):
        if tag_id != _TAG_BIN_DATA:
            continue
        if len(payload) < 4:
            seq += 1
            continue
        attr = struct.unpack_from("<H", payload, 0)[0]
        data_type = attr & 0x0F  # bits 0-3: 0=Link, 1=Embedding, 2=Storage
        if data_type in (1, 2):  # Embedding or Storage
            storage_id = struct.unpack_from("<H", payload, 2)[0]
            ext, _ = _read_hwp_string(payload, 4)
            entries[seq] = _BinEntry(storage_id=storage_id, ext=ext.lower())
        seq += 1
    return entries


# ── BinData stream loader ─────────────────────────────────────────────────────

def _load_bin_data(ole: "olefile.OleFileIO", compressed: bool) -> dict[int, tuple[bytes, str]]:
    """Read all BinData streams → {storage_id: (data, ext)}.

    Streams are named BIN{N:04d}.{ext} or BIN{N:04X}.{ext}.
    When compressed=True the streams are raw-deflated (same flag as BodyText).
    """
    result: dict[int, tuple[bytes, str]] = {}
    for entry in ole.listdir(streams=True):
        if entry[0] != "BinData":
            continue
        name = entry[1]  # e.g. "BIN0001.png"
        m = re.match(r"BIN([0-9A-Fa-f]{4})\.(\w+)$", name, re.IGNORECASE)
        if not m:
            continue
        stream_id = int(m.group(1), 16)  # covers both decimal (0001→1) and hex
        ext = m.group(2).lower()
        path = f"BinData/{name}"
        try:
            raw = ole.openstream(path).read()
            data = _decode_stream(raw, compressed)
            result[stream_id] = (data, ext)
        except Exception:
            pass
    return result


# ── MIME helpers ──────────────────────────────────────────────────────────────

def _detect_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] in (b"GIF8", b"GIF9"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    return "application/octet-stream"


def _mime_to_ext(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg",
            "image/gif": "gif", "image/bmp": "bmp"}.get(mime, "bin")


# ── section parser ─────────────────────────────────────────────────────────────

def _parse_section(
    data: bytes,
    bin_entries: dict[int, _BinEntry],
    bin_streams: dict[int, tuple[bytes, str]],
    images: list[ImageItem],
) -> list[str]:
    """Parse one BodyText section stream into a list of markdown blocks."""
    parts: list[str] = []

    in_table = False
    table_rows: list[list[str]] = []
    current_row: list[str] = []
    current_cell_parts: list[str] = []
    cell_depth = 0

    # GSO (picture) state
    in_gso = False
    gso_level = -1

    for tag_id, level, payload in _iter_records(data):
        # ── picture detection ─────────────────────────────────────────────
        if tag_id == _TAG_CTRL_HEADER:
            ctrl = payload[:4] if len(payload) >= 4 else b""
            if ctrl == _CTRL_GSO:
                in_gso = True
                gso_level = level
            elif ctrl == _CTRL_TABLE and level == 0:
                in_table = True
                table_rows = []
                current_row = []
                current_cell_parts = []
                cell_depth = 0

        if in_gso and tag_id == _TAG_SHAPE_PICTURE:
            if len(payload) >= _PICTURE_BIN_DATA_ID_OFFSET + 2:
                bin_data_id = struct.unpack_from("<H", payload, _PICTURE_BIN_DATA_ID_OFFSET)[0]
                _emit_image(bin_data_id, bin_entries, bin_streams, images, parts)
            in_gso = False

        # Leaving GSO group: level <= gso_level means we're back at the parent level
        if in_gso and level <= gso_level and tag_id != _TAG_CTRL_HEADER:
            in_gso = False

        # ── table parsing ─────────────────────────────────────────────────
        if tag_id == _TAG_LIST_HEADER and in_table:
            if level == 2:
                if current_cell_parts or current_row:
                    if current_cell_parts:
                        current_row.append(" ".join(current_cell_parts))
                        current_cell_parts = []
                    if current_row:
                        table_rows.append(current_row)
                current_row = []
                cell_depth = 0
            elif level == 3:
                current_cell_parts = []
                cell_depth += 1

        elif tag_id == _TAG_PARA_TEXT:
            text = _para_text_from_payload(payload)
            if not text:
                pass
            elif in_table and cell_depth > 0:
                current_cell_parts.append(text)
            elif not in_table and not in_gso:
                parts.append(text)

        # ── end of table ──────────────────────────────────────────────────
        if in_table and level == 0 and tag_id not in (
            _TAG_CTRL_HEADER, _TAG_LIST_HEADER, _TAG_PARA_HEADER,
            _TAG_PARA_TEXT, _TAG_TABLE,
        ):
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

    # flush open table
    if in_table:
        if current_cell_parts:
            current_row.append(" ".join(current_cell_parts))
        if current_row:
            table_rows.append(current_row)
        md = _table_to_md(table_rows)
        if md:
            parts.append(md)

    return parts


def _emit_image(
    bin_data_id: int,
    bin_entries: dict[int, _BinEntry],
    bin_streams: dict[int, tuple[bytes, str]],
    images: list[ImageItem],
    parts: list[str],
) -> None:
    """Resolve bin_data_id → BinData stream → append ImageItem and placeholder."""
    # Resolve via DocInfo BinData table
    entry = bin_entries.get(bin_data_id)
    if entry is not None:
        stream_data = bin_streams.get(entry.storage_id)
    else:
        # Fallback: storage_id == bin_data_id directly
        stream_data = bin_streams.get(bin_data_id)

    if stream_data is None:
        return

    raw_data, ext_from_name = stream_data
    mime = _detect_mime(raw_data)
    ext = _mime_to_ext(mime) if mime != "application/octet-stream" else ext_from_name

    idx = len(images) + 1
    images.append(ImageItem(idx=idx, data=raw_data, mime=mime, ext=ext))
    parts.append(f"[[RHWP_IMAGE:{idx}]]")


# ── text decoder ──────────────────────────────────────────────────────────────

def _para_text_from_payload(payload: bytes) -> str:
    """Decode PARA_TEXT payload: UTF-16LE, skip inline controls.

    HWPF inline controls occupy exactly 8 UTF-16 chars: the control char
    (U+0001–U+001F) followed by 7 parameter chars that must be skipped.
    """
    if len(payload) % 2 != 0:
        payload = payload[:-1]
    try:
        chars = list(payload.decode("utf-16-le"))
    except UnicodeDecodeError:
        return ""
    result = []
    i = 0
    while i < len(chars):
        c = chars[i]
        if "\x01" <= c <= "\x1f":
            i += 8  # skip control char + 7 parameter chars
        else:
            result.append(c)
            i += 1
    return "".join(result).strip()


# ── file header ────────────────────────────────────────────────────────────────

def _read_flags(ole: "olefile.OleFileIO") -> int:
    data = ole.openstream("FileHeader").read()
    if len(data) < 40:
        return 0
    return struct.unpack_from("<I", data, 36)[0]


def _section_streams(ole: "olefile.OleFileIO") -> list[str]:
    streams = [
        "/".join(entry)
        for entry in ole.listdir(streams=True)
        if len(entry) == 2 and entry[0] == "BodyText" and entry[1].startswith("Section")
    ]
    return sorted(streams, key=lambda s: int(re.search(r"\d+", s.split("/")[1]).group()))


# ── public entries ─────────────────────────────────────────────────────────────

def parse(data: bytes) -> tuple[str, list[ImageItem]]:
    """Convert HWP5 bytes to (markdown_with_placeholders, image_list).

    Requires the 'olefile' package.
    """
    import io as _io

    ole = olefile.OleFileIO(_io.BytesIO(data))
    try:
        flags      = _read_flags(ole)
        compressed = bool(flags & 0x1)

        bin_entries = _parse_doc_info_bin_data(ole, compressed)
        bin_streams = _load_bin_data(ole, compressed)

        images: list[ImageItem] = []
        parts: list[str] = []
        for stream_name in _section_streams(ole):
            raw = ole.openstream(stream_name).read()
            decoded = _decode_stream(raw, compressed)
            parts.extend(_parse_section(decoded, bin_entries, bin_streams, images))
    finally:
        ole.close()

    return "\n\n".join(parts), images


def convert(data: bytes) -> str:
    """Convert HWP5 bytes to Markdown (text + tables; images dropped).

    For full pipeline with image upload and LLM restructuring,
    use md_converter.convert() instead.
    Requires the 'olefile' package.
    """
    md, _ = parse(data)
    md = re.sub(r"\[\[RHWP_IMAGE:\d+\]\]\n?\n?", "", md)
    return md.strip()
