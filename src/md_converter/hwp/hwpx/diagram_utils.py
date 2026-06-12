from __future__ import annotations
from xml.etree import ElementTree as ET
from ..._diagram import DiagramGraph, ShapeNode, ShapeEdge
from ._xml import _q, _para_text

_SHAPE_TYPE_MAP: dict[str, str] = {
    _q("rect"):      "rect",
    _q("ellipse"):   "ellipse",
    _q("polygon"):   "other",
    _q("arc"):       "other",
    _q("curve"):     "other",
    _q("line"):      "other",
    _q("container"): "other",
}

_CONNECT_TAG = _q("connectLine")


def _shape_id(el: ET.Element) -> str | None:
    sid = el.get("id")
    if sid:
        return sid
    sc = el.find(_q("shapeComponent"))
    if sc is not None:
        return sc.get("objectId")
    return None


def _shape_label(el: ET.Element) -> str:
    texts: list[str] = []
    for draw_text in el.findall(f".//{_q('drawText')}"):
        for sub in draw_text.findall(_q("subList")):
            for para in sub.findall(_q("p")):
                t = _para_text(para).strip()
                if t:
                    texts.append(t)
    return " ".join(texts)


def extract_diagram(p: ET.Element) -> DiagramGraph | None:
    """Extract a DiagramGraph from a paragraph with drawing shapes.

    Returns None if no hp:connectLine elements are found.

    Note: hp:connectLine attribute names (startConnectShapeId, endConnectShapeId,
    endArrow) should be verified against actual HWPX sample files, as they may
    differ between HWP XML versions.
    """
    nodes: list[ShapeNode] = []
    edges: list[ShapeEdge] = []

    for child in p:
        shape_type = _SHAPE_TYPE_MAP.get(child.tag)
        if shape_type is not None:
            sid = _shape_id(child)
            if sid is not None:
                nodes.append(ShapeNode(
                    id=sid,
                    shape_type=shape_type,
                    label=_shape_label(child),
                ))
        elif child.tag == _CONNECT_TAG:
            from_id   = child.get("startConnectShapeId", "")
            to_id     = child.get("endConnectShapeId", "")
            label     = child.get("edgeLabel", "")
            end_arrow = child.get("endArrow", "arrow")
            if from_id and to_id:
                edges.append(ShapeEdge(
                    from_id=from_id,
                    to_id=to_id,
                    label=label,
                    arrow=(end_arrow != "none"),
                ))

    if not edges:
        return None

    return DiagramGraph(nodes=nodes, edges=edges)
