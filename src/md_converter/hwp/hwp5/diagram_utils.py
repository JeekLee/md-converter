"""HWP5 GSO 레코드 → DiagramGraph 추출.

커넥터 shape의 fromConnId / toConnId 오프셋은 HWP Document Format v5.x 스펙 검증 필요.
현재는 항상 None 반환 (기존 hwp-drawing 텍스트 레이블 폴백 유지).

스펙 확인 후 구현 위치:
  - TAG_SHAPE_COMPONENT (0x58) payload byte 0: shape type enum
  - 커넥터 line shape: payload 내 fromConnId/toConnId UINT16 필드
  - HWP Document Format v5.0.4, HWPTAG_SHAPE_COMPONENT_LINE 섹션 참조
"""
from __future__ import annotations
from ..._diagram import DiagramGraph
from ._records import _TAG_SHAPE_COMPONENT


_SHAPE_TYPE_ENUM: dict[int, str] = {
    0: "other",    # line
    1: "rect",
    2: "ellipse",
    3: "other",    # arc
    4: "other",    # polygon
    5: "other",    # curve
    6: "other",    # picture
    7: "other",    # ole
    8: "other",    # container
}


def extract_diagram(gso_records: list[tuple[int, int, bytes]]) -> DiagramGraph | None:
    """GSO 블록 내 레코드에서 DiagramGraph를 추출한다.

    커넥터 오프셋 미구현으로 현재 항상 None 반환.
    구현 시 gso_records에서 TAG_SHAPE_COMPONENT 레코드를 순회하며
    shape type + 커넥터 from/to ID를 추출한다.
    """
    return None
