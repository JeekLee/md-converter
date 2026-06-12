"""HWP5 GSO 레코드 → DiagramGraph 추출.

CTRL_HEADER(0x47) payload 구조:
  offset  0: attr / ctrl_id (u32 LE)  — 첫 4바이트가 ctrl_id 식별자
  offset  4: vertical_offset (u32)
  offset  8: horizontal_offset (u32)
  offset 12: width (u32)
  offset 16: height (u32)
  offset 20: z_order (i32)
  offset 24: margin.left (i16)
  offset 26: margin.right (i16)
  offset 28: margin.top (i16)
  offset 30: margin.bottom (i16)
  offset 32: instance_id (u32)      ← shape 고유 ID

SHAPE_COMPONENT_LINE(0x4E) payload 구조 (커넥터):
  offset  0: start.x (i32)
  offset  4: start.y (i32)
  offset  8: end.x (i32)
  offset 12: end.y (i32)
  offset 16: link_type (u32)           — arrow = (link_type % 3) != 0
  offset 20: start_subject_id (u32)    ← from_id
  offset 24: start_subject_index (u32)
  offset 28: end_subject_id (u32)      ← to_id
  offset 32: end_subject_index (u32)
"""
from __future__ import annotations

import struct

from ..._diagram import DiagramGraph, ShapeEdge, ShapeNode
from ._records import _TAG_CTRL_HEADER, _TAG_PARA_TEXT, _TAG_SHAPE_COMPONENT_LINE  # noqa: F401

# ctrl_id bytes (LE-encoded, first 4 bytes of CTRL_HEADER payload)
_CTRL_CONNECTOR = b"loc$"  # ctrl_id("$col") — connector line

_SHAPE_CTRL_TYPE: dict[bytes, str] = {
    b"cer$": "rect",     # ctrl_id("$rec")
    b"lle$": "ellipse",  # ctrl_id("$ell")
    b"lop$": "other",    # ctrl_id("$pol") polygon
    b"nil$": "other",    # ctrl_id("$lin") non-connector line
    b"cra$": "other",    # ctrl_id("$arc") arc
    b"ruc$": "other",    # ctrl_id("$cur") curve
}


def _para_text(payload: bytes) -> str:
    """TAG_PARA_TEXT 페이로드를 문자열로 디코딩한다."""
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
            i += 8  # inline control code: 8 chars (1 code + 7 args)
        else:
            result.append(c)
            i += 1
    return "".join(result).strip()


def extract_diagram(gso_records: list[tuple[int, int, bytes]]) -> DiagramGraph | None:
    """GSO 블록 내 레코드에서 DiagramGraph를 추출한다.

    gso_records: list of (tag_id, level, payload) tuples from _iter_records.
    Returns DiagramGraph if at least one connector edge is found, otherwise None.
    """
    if not gso_records:
        return None

    min_level = min(level for _, level, _ in gso_records)

    # Accumulated state
    shapes: dict[str, ShapeNode] = {}   # instance_id (str) → ShapeNode
    edges: list[ShapeEdge] = []

    # Current context (reset at each top-level CTRL_HEADER)
    current_id: str | None = None
    current_is_connector: bool = False

    for tag_id, level, payload in gso_records:
        if tag_id == _TAG_CTRL_HEADER and level == min_level:
            # --- New top-level object ---
            current_id = None
            current_is_connector = False

            if len(payload) < 36:
                continue

            ctrl_bytes = payload[0:4]
            instance_id = struct.unpack_from("<I", payload, 32)[0]
            id_str = str(instance_id)

            if ctrl_bytes in _SHAPE_CTRL_TYPE:
                shape_type = _SHAPE_CTRL_TYPE[ctrl_bytes]
                shapes[id_str] = ShapeNode(id=id_str, shape_type=shape_type, label="")
                current_id = id_str
                current_is_connector = False

            elif ctrl_bytes == _CTRL_CONNECTOR:
                current_id = id_str
                current_is_connector = True

        elif tag_id == _TAG_PARA_TEXT and level > min_level:
            # Text label for the current non-connector shape
            if current_id is not None and not current_is_connector:
                text = _para_text(payload)
                if text and current_id in shapes:
                    existing = shapes[current_id]
                    if existing.label:
                        shapes[current_id] = ShapeNode(
                            id=existing.id,
                            shape_type=existing.shape_type,
                            label=existing.label + " " + text,
                        )
                    else:
                        shapes[current_id] = ShapeNode(
                            id=existing.id,
                            shape_type=existing.shape_type,
                            label=text,
                        )

        elif tag_id == _TAG_SHAPE_COMPONENT_LINE and level > min_level:
            # Connector geometry — only valid inside a connector context
            if not current_is_connector:
                continue
            if len(payload) < 32:
                continue

            link_type = struct.unpack_from("<I", payload, 16)[0]
            start_id  = struct.unpack_from("<I", payload, 20)[0]
            end_id    = struct.unpack_from("<I", payload, 28)[0]
            arrow = (link_type % 3) != 0

            edges.append(ShapeEdge(
                from_id=str(start_id),
                to_id=str(end_id),
                label="",
                arrow=arrow,
            ))

    if not edges:
        return None

    node_list = list(shapes.values())
    return DiagramGraph(nodes=node_list, edges=edges)
