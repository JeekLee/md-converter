import struct

import pytest

from md_converter.hwp.hwp5.diagram_utils import extract_diagram
from md_converter.hwp.hwp5._records import (
    _TAG_CTRL_HEADER,
    _TAG_PARA_TEXT,
    _TAG_SHAPE_COMPONENT_LINE,
    _TAG_SHAPE_COMPONENT,
)


# ── Payload builders ──────────────────────────────────────────────────────────

def _make_ctrl_header(ctrl_bytes: bytes, instance_id: int) -> bytes:
    """CTRL_HEADER payload: ctrl_id(4) + zeros(28) + instance_id(4) + zeros(4).

    Layout (offsets in payload):
      0-3:  ctrl_id / attr
      4-31: other common fields (28 bytes, all zero)
      32-35: instance_id (u32 LE)
      36-39: padding
    """
    return ctrl_bytes + b"\x00" * 28 + struct.pack("<I", instance_id) + b"\x00" * 4


def _make_connector_line(start_id: int, end_id: int, link_type: int = 1) -> bytes:
    """SHAPE_COMPONENT_LINE payload for a connector.

    Layout:
      0-15:  coords (start.x, start.y, end.x, end.y) — all zero
      16-19: link_type (u32 LE)
      20-23: start_subject_id (u32 LE)
      24-27: start_subject_index (u32 LE) = 0
      28-31: end_subject_id (u32 LE)
      32-35: end_subject_index (u32 LE) = 0
    """
    coords = b"\x00" * 16
    connector = struct.pack("<IIIII", link_type, start_id, 0, end_id, 0)
    return coords + connector


def _para_text_payload(text: str) -> bytes:
    return text.encode("utf-16-le")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_empty_returns_none():
    assert extract_diagram([]) is None


def test_no_connector_returns_none():
    """두 rect가 있어도 연결선 없으면 None을 반환한다."""
    records = [
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"cer$", 1)),  # rect 1
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"cer$", 2)),  # rect 2
    ]
    assert extract_diagram(records) is None


def test_connector_with_two_shapes():
    """rect 두 개를 연결선으로 연결하면 edge 1개가 반환된다."""
    records = [
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"cer$", 100)),  # rect id=100
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"cer$", 200)),  # rect id=200
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"loc$", 300)),  # connector
        (_TAG_SHAPE_COMPONENT_LINE, 3, _make_connector_line(100, 200, link_type=1)),
    ]
    graph = extract_diagram(records)
    assert graph is not None
    assert len(graph.edges) == 1
    assert graph.edges[0].from_id == "100"
    assert graph.edges[0].to_id == "200"
    assert graph.edges[0].arrow is True


def test_no_arrow_link_type():
    """link_type=0 이면 arrow=False."""
    records = [
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"cer$", 1)),
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"cer$", 2)),
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"loc$", 3)),
        (_TAG_SHAPE_COMPONENT_LINE, 3, _make_connector_line(1, 2, link_type=0)),
    ]
    graph = extract_diagram(records)
    assert graph is not None
    assert graph.edges[0].arrow is False


def test_shape_label_from_para_text():
    """TAG_PARA_TEXT가 있으면 해당 shape의 레이블로 설정된다."""
    records = [
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"cer$", 10)),
        (_TAG_PARA_TEXT, 3, _para_text_payload("시작")),
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"cer$", 20)),
        (_TAG_PARA_TEXT, 3, _para_text_payload("종료")),
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"loc$", 30)),
        (_TAG_SHAPE_COMPONENT_LINE, 3, _make_connector_line(10, 20, link_type=1)),
    ]
    graph = extract_diagram(records)
    assert graph is not None
    node_by_id = {n.id: n for n in graph.nodes}
    assert node_by_id["10"].label == "시작"
    assert node_by_id["20"].label == "종료"


def test_ellipse_shape_type():
    """ctrl_id b'lle$' → shape_type == 'ellipse'."""
    records = [
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"lle$", 1)),
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"cer$", 2)),
        (_TAG_CTRL_HEADER, 2, _make_ctrl_header(b"loc$", 3)),
        (_TAG_SHAPE_COMPONENT_LINE, 3, _make_connector_line(1, 2)),
    ]
    graph = extract_diagram(records)
    assert graph is not None
    node = next(n for n in graph.nodes if n.id == "1")
    assert node.shape_type == "ellipse"
