"""HWP5 image storage: DocInfo BinData registry and BinData stream loading."""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from ._records import _TAG_BIN_DATA, _decode_stream, _iter_records


@dataclass
class _BinEntry:
    storage_id: int
    ext: str


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


def parse_doc_info_bin_data(ole: object, compressed: bool) -> dict[int, _BinEntry]:
    """DocInfo stream → {1-based bin_data_id: _BinEntry(storage_id, ext)}."""
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
        if data_type in (1, 2):
            storage_id = struct.unpack_from("<H", payload, 2)[0]
            ext, _ = _read_hwp_string(payload, 4)
            entries[seq] = _BinEntry(storage_id=storage_id, ext=ext.lower())
        seq += 1
    return entries


def load_bin_data(ole: object, compressed: bool) -> dict[int, tuple[bytes, str]]:
    """BinData streams → {storage_id: (data, ext)}.

    Streams are named BIN{N:04d}.{ext} or BIN{N:04X}.{ext}.
    """
    result: dict[int, tuple[bytes, str]] = {}
    for entry in ole.listdir(streams=True):
        if entry[0] != "BinData":
            continue
        name = entry[1]
        m = re.match(r"BIN([0-9A-Fa-f]{4})\.(\w+)$", name, re.IGNORECASE)
        if not m:
            continue
        stream_id = int(m.group(1), 16)
        ext = m.group(2).lower()
        path = f"BinData/{name}"
        try:
            raw = ole.openstream(path).read()
            data = _decode_stream(raw, compressed)
            result[stream_id] = (data, ext)
        except Exception:
            pass
    return result


