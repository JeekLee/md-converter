import struct
from md_converter.hwp.hwp5.diagram_utils import extract_diagram
from md_converter.hwp.hwp5._records import _TAG_SHAPE_COMPONENT, _TAG_PARA_TEXT


def _record(tag_id: int, level: int, payload: bytes) -> tuple[int, int, bytes]:
    return (tag_id, level, payload)


def test_empty_records_returns_none():
    assert extract_diagram([]) is None


def test_no_connector_returns_none():
    records = [
        _record(_TAG_SHAPE_COMPONENT, 2, struct.pack("B7x", 1)),  # rect
        _record(_TAG_PARA_TEXT, 3, "시작".encode("utf-16-le")),
    ]
    assert extract_diagram(records) is None


def test_extract_diagram_signature():
    """extract_diagram은 list[tuple[int,int,bytes]]를 받아 DiagramGraph|None을 반환한다."""
    from md_converter._diagram import DiagramGraph
    result = extract_diagram([])
    assert result is None or isinstance(result, DiagramGraph)
