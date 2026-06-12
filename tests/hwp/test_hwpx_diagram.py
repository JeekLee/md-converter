from __future__ import annotations
from xml.etree import ElementTree as ET
from md_converter.hwp.hwpx.diagram_utils import extract_diagram

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _make_p(inner: str) -> ET.Element:
    return ET.fromstring(f'<hp:p xmlns:hp="{HP}">{inner}</hp:p>')


def _rect(id_: str, label: str) -> str:
    return (
        f'<hp:rect id="{id_}">'
        f'<hp:drawText><hp:subList>'
        f'<hp:p><hp:run><hp:t>{label}</hp:t></hp:run></hp:p>'
        f'</hp:subList></hp:drawText>'
        f'</hp:rect>'
    )


def _ellipse(id_: str, label: str) -> str:
    return (
        f'<hp:ellipse id="{id_}">'
        f'<hp:drawText><hp:subList>'
        f'<hp:p><hp:run><hp:t>{label}</hp:t></hp:run></hp:p>'
        f'</hp:subList></hp:drawText>'
        f'</hp:ellipse>'
    )


def _connect(from_id: str, to_id: str, arrow: str = "arrow") -> str:
    return (
        f'<hp:connectLine startConnectShapeId="{from_id}" '
        f'endConnectShapeId="{to_id}" endArrow="{arrow}"/>'
    )


def test_connection_extracted():
    p = _make_p(_rect("1", "시작") + _rect("2", "처리") + _connect("1", "2"))
    graph = extract_diagram(p)
    assert graph is not None
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.edges[0].from_id == "1"
    assert graph.edges[0].to_id == "2"
    assert graph.edges[0].arrow is True


def test_no_connection_returns_none():
    p = _make_p(_rect("1", "고립"))
    assert extract_diagram(p) is None


def test_shape_types():
    p = _make_p(_rect("1", "박스") + _ellipse("2", "타원") + _connect("1", "2"))
    graph = extract_diagram(p)
    assert graph is not None
    r = next(n for n in graph.nodes if n.id == "1")
    e = next(n for n in graph.nodes if n.id == "2")
    assert r.shape_type == "rect"
    assert e.shape_type == "ellipse"


def test_no_arrow():
    p = _make_p(_rect("1", "A") + _rect("2", "B") + _connect("1", "2", arrow="none"))
    graph = extract_diagram(p)
    assert graph is not None
    assert graph.edges[0].arrow is False


def test_labels_extracted():
    p = _make_p(_rect("1", "시작 노드") + _rect("2", "처리 단계") + _connect("1", "2"))
    graph = extract_diagram(p)
    assert graph is not None
    labels = {n.label for n in graph.nodes}
    assert "시작 노드" in labels
    assert "처리 단계" in labels
