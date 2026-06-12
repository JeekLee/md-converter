"""HWP5 section content parser and public API."""
from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass, field

from .._common import ImageItem, _detect_mime, _mime_to_ext
from ..._diagram import graph_to_mermaid
from ._image_utils import _BinEntry, load_bin_data, parse_doc_info_bin_data
from ._table_utils import _serialize_flat, _serialize_nt, table_to_md
from .diagram_utils import extract_diagram
from ._records import (
    _CTRL_GSO,
    _CTRL_TABLE,
    _TAG_CTRL_HEADER,
    _TAG_LIST_HEADER,
    _TAG_PARA_TEXT,
    _TAG_SHAPE_PICTURE,
    _decode_stream,
    _iter_records,
)

# Offset of bin_data_id (u16) inside a SHAPE_PICTURE payload
_PICTURE_BIN_DATA_ID_OFFSET = 71


@dataclass
class _TableCtx:
    """One open table while parsing; the stack tracks nesting depth."""
    ctrl_lvl: int
    rows: list[list[str]] = field(default_factory=list)
    current_row: list[str] = field(default_factory=list)
    current_cell_parts: list[str] = field(default_factory=list)
    in_cell: bool = False
    row_addr: int = -1


def _para_text_from_payload(payload: bytes) -> str:
    """Decode PARA_TEXT payload: UTF-16LE, skip 8-char inline control blocks."""
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
            i += 8  # control char + 7 parameter chars
        else:
            result.append(c)
            i += 1
    return "".join(result).strip()


def _emit_image(
    bin_data_id: int,
    bin_entries: dict[int, _BinEntry],
    bin_streams: dict[int, tuple[bytes, str]],
    images: list[ImageItem],
    parts: list[str],
) -> None:
    entry = bin_entries.get(bin_data_id)
    if entry is not None:
        stream_data = bin_streams.get(entry.storage_id)
    else:
        stream_data = bin_streams.get(bin_data_id)

    if stream_data is None:
        return

    raw_data, ext_from_name = stream_data
    mime = _detect_mime(raw_data)
    ext = _mime_to_ext(mime) if mime != "application/octet-stream" else ext_from_name

    idx = len(images) + 1
    images.append(ImageItem(idx=idx, data=raw_data, mime=mime, ext=ext))
    parts.append(f"[[RHWP_IMAGE:{idx}]]")


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


def _read_flags(ole: object) -> int:
    data = ole.openstream("FileHeader").read()
    if len(data) < 40:
        return 0
    return struct.unpack_from("<I", data, 36)[0]


def _section_streams(ole: object) -> list[str]:
    streams = [
        "/".join(entry)
        for entry in ole.listdir(streams=True)
        if len(entry) == 2 and entry[0] == "BodyText" and entry[1].startswith("Section")
    ]
    return sorted(streams, key=lambda s: int(re.search(r"\d+", s.split("/")[1]).group()))


def parse(data: bytes) -> tuple[str, list[ImageItem]]:
    """Convert HWP5 bytes to (markdown_with_placeholders, image_list).

    Requires the 'olefile' package.
    """
    try:
        import olefile
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "HWP5 support requires 'olefile'. "
            "Install with: pip install 'md-converter[hwp5]'"
        ) from exc

    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        flags      = _read_flags(ole)
        compressed = bool(flags & 0x1)

        bin_entries = parse_doc_info_bin_data(ole, compressed)
        bin_streams = load_bin_data(ole, compressed)

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

    For full pipeline with image upload and nested-table separation,
    use md_converter.MdConverter instead.
    """
    md, _ = parse(data)
    md = re.sub(r"\[\[RHWP_IMAGE:\d+\]\]\n?\n?", "", md)
    return md.strip()
