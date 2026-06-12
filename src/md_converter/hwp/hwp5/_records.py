"""HWP5 low-level record parsing: tag iteration and stream decompression.

HWPF record header (32-bit LE):
  bits  0-9  : tag_id
  bits 10-19 : level   (0 = top-level)
  bits 20-31 : size    (0xFFF → read next 4 bytes as actual size)

BodyText and DocInfo streams use raw deflate (wbits=-15).
"""
from __future__ import annotations

import struct
import zlib
from typing import Generator

# ── HWPF tag IDs (HWPTAG_BEGIN=0x10) ─────────────────────────────────────────
_TAG_BIN_DATA      = 0x12  # 18  — DocInfo binary data entry
_TAG_PARA_HEADER   = 0x42  # 66  — paragraph header
_TAG_PARA_TEXT     = 0x43  # 67  — paragraph text (UTF-16LE)
_TAG_CTRL_HEADER   = 0x47  # 71  — inline control (table, GSO, …)
_TAG_LIST_HEADER   = 0x48  # 72  — cell boundary inside a table
_TAG_TABLE_BODY    = 0x4D  # 77  — table body (row/col counts at bytes 4-7)
_TAG_SHAPE_PICTURE        = 0x55  # 85  — picture shape (bin_data_id at offset 71)
_TAG_SHAPE_COMPONENT      = 0x4C  # 76  — shape component (ctrl_id at byte 0-3)
_TAG_SHAPE_COMPONENT_LINE = 0x4E  # 78  — line/connector shape data

# Control IDs: LE DWORD in CTRL_HEADER payload[0:4].
# ctrl_id is big-endian ASCII stored as little-endian bytes.
_CTRL_TABLE = b" lbt"   # ctrl_id("tbl ")
_CTRL_GSO   = b" osg"   # ctrl_id("gso ") — General Shape Object


def _iter_records(data: bytes) -> Generator[tuple[int, int, bytes], None, None]:
    """Yield (tag_id, level, payload) for each HWPF record in *data*."""
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


def _decompress(raw: bytes) -> bytes:
    """Raw deflate (HWP5 default), fallback to standard zlib."""
    try:
        return zlib.decompress(raw, -15)
    except zlib.error:
        return zlib.decompress(raw)


def _decode_stream(raw: bytes, compressed: bool) -> bytes:
    return _decompress(raw) if compressed else raw
