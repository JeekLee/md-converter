# Diagram Utils Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HWPX/HWP5 도형 블록에서 연결 그래프를 파싱해 Mermaid를 결정적으로 생성하고, PDF 벡터 다이어그램은 pymupdf 렌더링 + vision LLM으로 처리한다.

**Architecture:** `_diagram.py`에 공유 타입(DiagramGraph)과 `graph_to_mermaid()`를 정의한다. HWPX는 XML에서 shape ID + `hp:connectLine`을 파싱해 DiagramGraph를 만들고, 파서에서 mermaid 블록을 직접 emit한다. HWP5는 커넥터 오프셋 스펙 검증이 필요해 초기 버전은 None 반환(기존 동작 유지)으로 프레임만 설치한다. PDF는 `pdf/diagram_utils.py`가 rect 클러스터를 감지해 pymupdf로 PNG 렌더링하고, `vision_to_mermaid()`가 LLM에 이미지를 보내 Mermaid를 추출한다.

**Tech Stack:** Python stdlib (xml.etree, struct, base64, urllib), pymupdf(fitz), pdfplumber, OpenAI-compatible LLM API

---

## 파일 맵

| 액션 | 경로 |
|---|---|
| Create | `src/md_converter/_diagram.py` |
| Create | `src/md_converter/hwp/hwpx/diagram_utils.py` |
| Create | `src/md_converter/hwp/hwp5/diagram_utils.py` |
| Create | `src/md_converter/pdf/diagram_utils.py` |
| Create | `tests/test_diagram.py` |
| Create | `tests/hwp/test_hwpx_diagram.py` |
| Create | `tests/hwp/test_hwp5_diagram.py` |
| Create | `tests/pdf/test_diagram_utils.py` |
| Create | `tests/test_llm_vision.py` |
| Modify | `src/md_converter/_common.py` |
| Modify | `src/md_converter/hwp/hwp5/_records.py` |
| Modify | `src/md_converter/hwp/hwpx/_parser.py` |
| Modify | `src/md_converter/hwp/hwp5/_parser.py` |
| Modify | `src/md_converter/llm.py` |
| Modify | `src/md_converter/pdf/_pdf.py` |
| Modify | `src/md_converter/__init__.py` |
| Modify | `pyproject.toml` |

---

## Task 1: `_diagram.py` — DiagramGraph 타입 + graph_to_mermaid()

**Files:**
- Create: `src/md_converter/_diagram.py`
- Create: `tests/test_diagram.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_diagram.py`:
```python
from md_converter._diagram import ShapeNode, ShapeEdge, DiagramGraph, graph_to_mermaid


def test_simple_chain():
    graph = DiagramGraph(
        nodes=[
            ShapeNode(id="1", shape_type="ellipse", label="시작"),
            ShapeNode(id="2", shape_type="rect",    label="처리"),
            ShapeNode(id="3", shape_type="ellipse", label="종료"),
        ],
        edges=[
            ShapeEdge(from_id="1", to_id="2", label="", arrow=True),
            ShapeEdge(from_id="2", to_id="3", label="", arrow=True),
        ],
    )
    result = graph_to_mermaid(graph)
    assert result is not None
    assert result.startswith("graph TD")
    assert "n1([시작]) --> n2[처리]" in result
    assert "n2 --> n3([종료])" in result


def test_diamond_with_edge_labels():
    graph = DiagramGraph(
        nodes=[
            ShapeNode(id="1", shape_type="diamond", label="오류?"),
            ShapeNode(id="2", shape_type="rect",    label="처리"),
            ShapeNode(id="3", shape_type="rect",    label="에러"),
        ],
        edges=[
            ShapeEdge(from_id="1", to_id="2", label="아니오", arrow=True),
            ShapeEdge(from_id="1", to_id="3", label="예",    arrow=True),
        ],
    )
    result = graph_to_mermaid(graph)
    assert result is not None
    assert "n1{오류?}" in result
    assert "|아니오|" in result
    assert "|예|" in result


def test_no_arrow_uses_line():
    graph = DiagramGraph(
        nodes=[
            ShapeNode(id="1", shape_type="rect", label="A"),
            ShapeNode(id="2", shape_type="rect", label="B"),
        ],
        edges=[ShapeEdge(from_id="1", to_id="2", label="", arrow=False)],
    )
    result = graph_to_mermaid(graph)
    assert result is not None
    assert "---" in result
    assert "-->" not in result


def test_empty_graph_returns_none():
    assert graph_to_mermaid(DiagramGraph(nodes=[], edges=[])) is None


def test_isolated_node_appears():
    graph = DiagramGraph(
        nodes=[
            ShapeNode(id="1", shape_type="rect",    label="고립"),
            ShapeNode(id="2", shape_type="ellipse", label="연결됨"),
            ShapeNode(id="3", shape_type="rect",    label="종착"),
        ],
        edges=[ShapeEdge(from_id="2", to_id="3", label="", arrow=True)],
    )
    result = graph_to_mermaid(graph)
    assert result is not None
    assert "n1[고립]" in result
    assert "n2([연결됨]) --> n3[종착]" in result
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
cd /home/jeek_lee/work/personal/md-converter
uv run pytest tests/test_diagram.py -v
```
Expected: `ModuleNotFoundError: No module named 'md_converter._diagram'`

- [ ] **Step 3: 구현 작성**

`src/md_converter/_diagram.py`:
```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ShapeNode:
    id: str
    shape_type: str  # "rect" | "ellipse" | "diamond" | "other"
    label: str


@dataclass
class ShapeEdge:
    from_id: str
    to_id: str
    label: str
    arrow: bool  # True = -->, False = ---


@dataclass
class DiagramGraph:
    nodes: list[ShapeNode]
    edges: list[ShapeEdge]


_MERMAID_SHAPE: dict[str, str] = {
    "rect":    "[{label}]",
    "ellipse": "([{label}])",
    "diamond": "{{{label}}}",
    "other":   "[{label}]",
}


def graph_to_mermaid(graph: DiagramGraph) -> str | None:
    if not graph.nodes:
        return None

    node_map = {n.id: n for n in graph.nodes}
    emitted: set[str] = set()
    lines = ["graph TD"]

    def _node_ref(node_id: str) -> str:
        node = node_map.get(node_id)
        if node is None:
            return f"n{node_id}"
        label = node.label.replace('"', "'")
        if node_id in emitted:
            return f"n{node_id}"
        emitted.add(node_id)
        shape = _MERMAID_SHAPE.get(node.shape_type, "[{label}]").format(label=label)
        return f"n{node_id}{shape}"

    for edge in graph.edges:
        from_ref = _node_ref(edge.from_id)
        to_ref   = _node_ref(edge.to_id)
        connector = "-->" if edge.arrow else "---"
        label_part = f"|{edge.label}|" if edge.label else ""
        lines.append(f"  {from_ref} {connector}{label_part} {to_ref}")

    for node in graph.nodes:
        if node.id not in emitted:
            label = node.label.replace('"', "'")
            shape = _MERMAID_SHAPE.get(node.shape_type, "[{label}]").format(label=label)
            lines.append(f"  n{node.id}{shape}")

    return "\n".join(lines)
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
uv run pytest tests/test_diagram.py -v
```
Expected: 5 tests PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/md_converter/_diagram.py tests/test_diagram.py
git commit -m "feat: DiagramGraph 타입 + graph_to_mermaid() 추가"
```

---

## Task 2: `hwpx/diagram_utils.py` — XML 연결 그래프 추출

**Files:**
- Create: `src/md_converter/hwp/hwpx/diagram_utils.py`
- Create: `tests/hwp/test_hwpx_diagram.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/hwp/test_hwpx_diagram.py`:
```python
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
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
uv run pytest tests/hwp/test_hwpx_diagram.py -v
```
Expected: `ImportError`

- [ ] **Step 3: 구현 작성**

`src/md_converter/hwp/hwpx/diagram_utils.py`:
```python
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
            from_id  = child.get("startConnectShapeId", "")
            to_id    = child.get("endConnectShapeId", "")
            label    = child.get("edgeLabel", "")
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
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
uv run pytest tests/hwp/test_hwpx_diagram.py -v
```
Expected: 5 tests PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/md_converter/hwp/hwpx/diagram_utils.py tests/hwp/test_hwpx_diagram.py
git commit -m "feat(hwpx): diagram_utils — XML 연결 그래프 추출"
```

---

## Task 3: HWPX 파서 통합

**Files:**
- Modify: `src/md_converter/hwp/hwpx/_parser.py`
- Modify: `tests/hwp/test_hwpx.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/hwp/test_hwpx.py` 파일 끝에 추가:
```python
def test_drawing_with_connection_emits_mermaid():
    """hp:connectLine이 있으면 hwp-drawing 대신 mermaid 블록을 emit한다."""
    xml = _sec(
        '<hp:p>'
        '<hp:rect id="1"><hp:drawText><hp:subList>'
        '<hp:p><hp:run><hp:t>시작</hp:t></hp:run></hp:p>'
        '</hp:subList></hp:drawText></hp:rect>'
        '<hp:rect id="2"><hp:drawText><hp:subList>'
        '<hp:p><hp:run><hp:t>종료</hp:t></hp:run></hp:p>'
        '</hp:subList></hp:drawText></hp:rect>'
        '<hp:connectLine startConnectShapeId="1" endConnectShapeId="2" endArrow="arrow"/>'
        '</hp:p>'
    )
    md, _ = parse(_make_hwpx(xml))
    assert "```mermaid" in md
    assert "```hwp-drawing" not in md
    assert "시작" in md
    assert "종료" in md


def test_drawing_without_connection_keeps_hwp_drawing():
    """hp:connectLine 없으면 기존 hwp-drawing 블록을 emit한다."""
    xml = _sec(
        f'<hp:p>{_rect("레이블A")}{_rect("레이블B")}</hp:p>',
    )
    md, _ = parse(_make_hwpx(xml))
    assert "```hwp-drawing" in md
    assert "```mermaid" not in md
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
uv run pytest tests/hwp/test_hwpx.py::test_drawing_with_connection_emits_mermaid -v
```
Expected: FAILED (mermaid 블록 대신 hwp-drawing 블록이 emit됨)

- [ ] **Step 3: `hwpx/_parser.py` 수정**

`src/md_converter/hwp/hwpx/_parser.py` 상단 import에 추가:
```python
from ..._diagram import graph_to_mermaid
from .diagram_utils import extract_diagram
```

같은 파일의 drawing shapes 처리 블록을 교체:
```python
# 기존:
# drawing_labels = _drawing_texts(p)
# if drawing_labels:
#     parts.append("```hwp-drawing\n" + "\n".join(drawing_labels) + "\n```")
#     continue

# 교체 후:
diagram_graph = extract_diagram(p)
if diagram_graph is not None:
    mermaid = graph_to_mermaid(diagram_graph)
    if mermaid:
        parts.append(f"```mermaid\n{mermaid}\n```")
    continue

drawing_labels = _drawing_texts(p)
if drawing_labels:
    parts.append("```hwp-drawing\n" + "\n".join(drawing_labels) + "\n```")
    continue
```

- [ ] **Step 4: 테스트 실행 — 전체 통과 확인**

```bash
uv run pytest tests/hwp/test_hwpx.py -v
```
Expected: 모든 테스트 PASSED (기존 테스트 포함)

- [ ] **Step 5: 커밋**

```bash
git add src/md_converter/hwp/hwpx/_parser.py tests/hwp/test_hwpx.py
git commit -m "feat(hwpx): 연결 데이터 있을 때 mermaid 블록 직접 emit"
```

---

## Task 4: `_records.py` + `_common.py` 소규모 추가

**Files:**
- Modify: `src/md_converter/hwp/hwp5/_records.py`
- Modify: `src/md_converter/_common.py`

- [ ] **Step 1: `_records.py`에 TAG_SHAPE_COMPONENT 추가**

`src/md_converter/hwp/hwp5/_records.py`의 태그 상수 목록에 추가:
```python
_TAG_SHAPE_COMPONENT = 0x58  # 88 — shape component (type at byte 0)
```

- [ ] **Step 2: `_common.py`에 `is_diagram` 필드 추가**

`src/md_converter/_common.py`의 `ImageItem` 수정:
```python
@dataclass
class ImageItem:
    idx: int
    data: bytes
    mime: str
    ext: str
    is_diagram: bool = False  # True = PDF 렌더링된 다이어그램 영역
```

- [ ] **Step 3: 기존 테스트 실행 — 깨진 테스트 없음 확인**

```bash
uv run pytest -v
```
Expected: 모든 기존 테스트 PASSED (is_diagram은 기본값 False로 하위 호환)

- [ ] **Step 4: 커밋**

```bash
git add src/md_converter/hwp/hwp5/_records.py src/md_converter/_common.py
git commit -m "feat: TAG_SHAPE_COMPONENT 추가, ImageItem.is_diagram 필드 추가"
```

---

## Task 5: `hwp5/diagram_utils.py` — HWP5 프레임 설치

**Files:**
- Create: `src/md_converter/hwp/hwp5/diagram_utils.py`
- Create: `tests/hwp/test_hwp5_diagram.py`

> **Note:** HWP5 커넥터 shape의 fromConnId/toConnId 필드 오프셋은 HWP Document Format v5.x 스펙 확인이 필요하다. 초기 버전은 항상 `None`을 반환해 기존 동작을 유지한다. 스펙 확인 후 커넥터 추출 로직을 채울 수 있도록 인터페이스와 shape type 추출만 구현한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/hwp/test_hwp5_diagram.py`:
```python
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
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
uv run pytest tests/hwp/test_hwp5_diagram.py -v
```
Expected: `ImportError`

- [ ] **Step 3: 구현 작성**

`src/md_converter/hwp/hwp5/diagram_utils.py`:
```python
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
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
uv run pytest tests/hwp/test_hwp5_diagram.py -v
```
Expected: 3 tests PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/md_converter/hwp/hwp5/diagram_utils.py tests/hwp/test_hwp5_diagram.py
git commit -m "feat(hwp5): diagram_utils 인터페이스 설치 (커넥터 추출 스펙 검증 후 구현 예정)"
```

---

## Task 6: HWP5 파서 통합

**Files:**
- Modify: `src/md_converter/hwp/hwp5/_parser.py`

- [ ] **Step 1: `_parser.py` 수정 — GSO 레코드 누적 + diagram 시도**

`src/md_converter/hwp/hwp5/_parser.py` 상단 import에 추가:
```python
from ..._diagram import graph_to_mermaid
from .diagram_utils import extract_diagram
```

`_parse_section()` 함수 내 GSO 상태 변수 선언부에 `gso_records` 추가:
```python
# 기존:
in_gso         = False
gso_level      = -1
gso_text_parts: list[str] = []
gso_had_image  = False

# 수정:
in_gso          = False
gso_level       = -1
gso_text_parts: list[str] = []
gso_had_image   = False
gso_records:    list[tuple[int, int, bytes]] = []
```

GSO 진입 처리 블록에 `gso_records` 초기화 추가:
```python
if ctrl == _CTRL_GSO:
    in_gso = True
    gso_level = level
    gso_text_parts = []
    gso_had_image = False
    gso_records = []       # 추가
```

레코드 루프 안, CTRL_HEADER 디스패치 블록 **직후**에 누적 코드 추가:
```python
# GSO 내부 레코드 누적 (level > gso_level인 레코드만)
if in_gso and level > gso_level:
    gso_records.append((tag_id, level, payload))
```

GSO 종료 블록 수정:
```python
# 기존:
if in_gso and level <= gso_level and tag_id != _TAG_CTRL_HEADER:
    if not gso_had_image and gso_text_parts:
        drawing_text = "\n".join(gso_text_parts)
        parts.append(f"```hwp-drawing\n{drawing_text}\n```")
    in_gso = False
    gso_text_parts = []
    gso_had_image = False

# 수정:
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
    gso_records = []       # 추가
```

- [ ] **Step 2: 기존 HWP5 테스트 실행 — 깨진 테스트 없음 확인**

```bash
uv run pytest tests/hwp/test_hwp5.py -v
```
Expected: 모든 기존 테스트 PASSED (extract_diagram이 None을 반환하므로 동작 변화 없음)

- [ ] **Step 3: 커밋**

```bash
git add src/md_converter/hwp/hwp5/_parser.py
git commit -m "feat(hwp5): GSO 레코드 누적 + diagram 추출 시도 (현재 폴백 유지)"
```

---

## Task 7: `llm.py` — `vision_to_mermaid()` 추가

**Files:**
- Modify: `src/md_converter/llm.py`
- Create: `tests/test_llm_vision.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_llm_vision.py`:
```python
import json
from unittest.mock import patch, MagicMock
from md_converter.llm import LlmConfig, vision_to_mermaid


def _cfg() -> LlmConfig:
    return LlmConfig(url="http://localhost:10080/v1", api_key="test", model="test-vision")


def _mock_resp(content: str):
    m = MagicMock()
    m.read.return_value = json.dumps(
        {"choices": [{"message": {"content": content}}]}
    ).encode()
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def test_returns_mermaid():
    with patch("urllib.request.urlopen", return_value=_mock_resp("graph TD\n  A --> B")):
        result = vision_to_mermaid(b"fake-png", _cfg())
    assert result == "graph TD\n  A --> B"


def test_strips_mermaid_fences():
    with patch("urllib.request.urlopen",
               return_value=_mock_resp("```mermaid\ngraph TD\n  A --> B\n```")):
        result = vision_to_mermaid(b"fake-png", _cfg())
    assert result == "graph TD\n  A --> B"


def test_returns_none_on_exception():
    with patch("urllib.request.urlopen", side_effect=Exception("conn failed")):
        result = vision_to_mermaid(b"fake-png", _cfg())
    assert result is None


def test_sends_base64_image():
    import base64
    png = b"png-data"
    expected_b64 = base64.b64encode(png).decode()
    captured: dict = {}

    def _urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _mock_resp("graph TD\n  A --> B")

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        vision_to_mermaid(png, _cfg())

    content = captured["body"]["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert expected_b64 in content[0]["image_url"]["url"]
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
uv run pytest tests/test_llm_vision.py -v
```
Expected: `ImportError: cannot import name 'vision_to_mermaid'`

- [ ] **Step 3: `llm.py`에 구현 추가**

`src/md_converter/llm.py` 기존 `drawing_to_mermaid()` 아래에 추가:
```python
_DIAGRAM_VISION_PROMPT = """\
이 다이어그램 이미지를 Mermaid 코드로 변환해 주세요.

지침:
- graph TD, flowchart LR, sequenceDiagram 등 가장 적합한 유형 선택
- 변환이 불가능하면 graph TD 안에 텍스트를 노드로 배치
- Mermaid 코드만 출력, 설명 없이 (```mermaid 래퍼 없이)"""


def vision_to_mermaid(png_bytes: bytes, cfg: LlmConfig) -> str | None:
    """다이어그램 PNG 이미지를 vision LLM으로 Mermaid 코드로 변환한다."""
    import base64
    b64 = base64.b64encode(png_bytes).decode()
    body = json.dumps({
        "model": cfg.model,
        "temperature": 0.0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": _DIAGRAM_VISION_PROMPT},
            ],
        }],
    }).encode()
    endpoint = f"{cfg.url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())
        mermaid: str = result["choices"][0]["message"]["content"].strip()
        mermaid = re.sub(r"^```(?:mermaid)?\s*", "", mermaid)
        mermaid = re.sub(r"\s*```\s*$", "", mermaid)
        return mermaid.strip() or None
    except Exception as exc:
        sys.stderr.write(f"  vision → mermaid failed: {exc}\n")
        return None
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
uv run pytest tests/test_llm_vision.py -v
```
Expected: 4 tests PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/md_converter/llm.py tests/test_llm_vision.py
git commit -m "feat(llm): vision_to_mermaid() 추가"
```

---

## Task 8: `pdf/diagram_utils.py` + pyproject.toml

**Files:**
- Create: `src/md_converter/pdf/diagram_utils.py`
- Create: `tests/pdf/test_diagram_utils.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/pdf/test_diagram_utils.py`:
```python
from md_converter.pdf.diagram_utils import detect_diagram_bboxes, render_bbox_to_png


class _MockCrop:
    def extract_text(self):
        return ""


class _MockPage:
    def __init__(self, rects, width=595.0, height=842.0):
        self.rects = rects
        self.width = width
        self.height = height

    def crop(self, bbox):
        return _MockCrop()


def _r(x0, top, x1, bottom):
    return {"x0": x0, "top": top, "x1": x1, "bottom": bottom}


def test_no_rects_returns_empty():
    page = _MockPage(rects=[])
    result = detect_diagram_bboxes(page, table_bboxes=[])
    assert result == []


def test_cluster_under_threshold_ignored():
    # 2개 rect — 임계값(3) 미만
    page = _MockPage(rects=[_r(10, 10, 100, 60), _r(10, 70, 100, 120)])
    result = detect_diagram_bboxes(page, table_bboxes=[])
    assert result == []


def test_cluster_at_threshold_detected():
    # 3개 rect가 y-근접 (gap < 20pt)
    page = _MockPage(rects=[
        _r(10, 10,  100, 60),
        _r(10, 70,  100, 120),
        _r(10, 130, 100, 180),
    ])
    result = detect_diagram_bboxes(page, table_bboxes=[])
    assert len(result) == 1
    y_pos, bbox = result[0]
    assert y_pos < 30   # padding 감안해서 cluster top 근처
    x0, top, x1, bottom = bbox
    assert x0 <= 10
    assert x1 >= 100


def test_table_overlapping_rects_excluded():
    # rect가 표 bbox와 겹치면 제외
    page = _MockPage(rects=[
        _r(10, 10,  100, 60),
        _r(10, 70,  100, 120),
        _r(10, 130, 100, 180),
    ])
    table_bboxes = [(0.0, 0.0, 200.0, 200.0)]  # 전체 영역 커버
    result = detect_diagram_bboxes(page, table_bboxes=table_bboxes)
    assert result == []


def test_render_bbox_to_png_requires_pymupdf(tmp_path):
    """pymupdf가 없으면 ImportError, 있으면 bytes 반환."""
    try:
        import fitz  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("pymupdf not installed")
    # 최소 PDF 바이트 (유효한 단순 PDF)
    minimal_pdf = (
        b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj "
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
        b"3 0 obj<</Type/Page/MediaBox[0 0 100 100]/Parent 2 0 R>>endobj "
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
    )
    result = render_bbox_to_png(minimal_pdf, page_idx=0, bbox=(0.0, 0.0, 100.0, 100.0))
    assert isinstance(result, bytes)
    assert result[:4] == b"\x89PNG"  # PNG 매직 바이트
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
uv run pytest tests/pdf/test_diagram_utils.py -v
```
Expected: `ImportError`

- [ ] **Step 3: 구현 작성**

`src/md_converter/pdf/diagram_utils.py`:
```python
"""PDF 페이지에서 다이어그램 영역 감지 + pymupdf 렌더링."""
from __future__ import annotations


def detect_diagram_bboxes(
    page,
    table_bboxes: list[tuple[float, float, float, float]],
) -> list[tuple[float, tuple[float, float, float, float]]]:
    """표와 겹치지 않는 rect 클러스터를 다이어그램 후보로 감지한다.

    반환: (y_pos, (x0, top, x1, bottom)) 리스트
    """
    # 의미 있는 rect 필터링 (최소 크기 5pt, 표와 비겹침)
    rects: list[tuple[float, float, float, float]] = []
    for r in page.rects:
        rx0, rtop, rx1, rbottom = r["x0"], r["top"], r["x1"], r["bottom"]
        if (rx1 - rx0) < 5 or (rbottom - rtop) < 5:
            continue
        if any(
            rx0 < tx1 and rx1 > tx0 and rtop < tbottom and rbottom > ttop
            for tx0, ttop, tx1, tbottom in table_bboxes
        ):
            continue
        rects.append((rx0, rtop, rx1, rbottom))

    if not rects:
        return []

    rects.sort(key=lambda r: r[1])

    clusters: list[list[tuple[float, float, float, float]]] = [[rects[0]]]
    for r in rects[1:]:
        prev_bottom = max(prev[3] for prev in clusters[-1])
        if r[1] - prev_bottom <= 20:
            clusters[-1].append(r)
        else:
            clusters.append([r])

    results: list[tuple[float, tuple[float, float, float, float]]] = []
    for cluster in clusters:
        if len(cluster) < 3:
            continue

        x0     = max(0.0,          min(r[0] for r in cluster) - 10)
        top    = max(0.0,          min(r[1] for r in cluster) - 10)
        x1     = min(page.width,   max(r[2] for r in cluster) + 10)
        bottom = min(page.height,  max(r[3] for r in cluster) + 10)

        crop = page.crop((x0, top, x1, bottom))
        text = crop.extract_text() or ""
        area = (x1 - x0) * (bottom - top)
        if area > 0 and len(text) / area > 0.1:
            continue

        results.append((top, (x0, top, x1, bottom)))

    return results


def render_bbox_to_png(
    pdf_bytes: bytes,
    page_idx: int,
    bbox: tuple[float, float, float, float],
) -> bytes:
    """pymupdf로 PDF 페이지의 bbox 영역을 PNG bytes로 렌더링한다."""
    try:
        import fitz
    except ImportError as exc:
        raise ImportError(
            "PDF 다이어그램 렌더링은 pymupdf가 필요합니다. "
            "pip install 'md-converter[pdf]'"
        ) from exc

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    fitz_page = doc.load_page(page_idx)
    clip = fitz.Rect(*bbox)
    mat = fitz.Matrix(2.0, 2.0)
    pix = fitz_page.get_pixmap(matrix=mat, clip=clip)
    return pix.tobytes("png")
```

- [ ] **Step 4: `pyproject.toml`에 pymupdf 추가**

`pyproject.toml`의 `pdf` extra에 `pymupdf` 추가:
```toml
pdf     = ["pdfplumber>=0.11", "pypdf>=4.0", "Pillow>=10.0", "pymupdf>=1.23"]
pdf-ocr = ["pdfplumber>=0.11", "pypdf>=4.0", "Pillow>=10.0", "pytesseract>=0.3", "pymupdf>=1.23"]
dev     = ["pytest>=8", "olefile>=0.47", "Pillow>=10.0", "pdfplumber>=0.11", "pypdf>=4.0", "pymupdf>=1.23"]
```

- [ ] **Step 5: 테스트 실행 — 통과 확인**

```bash
uv run pytest tests/pdf/test_diagram_utils.py -v
```
Expected: PASSED (pymupdf 없으면 render 테스트는 skip)

- [ ] **Step 6: 커밋**

```bash
git add src/md_converter/pdf/diagram_utils.py tests/pdf/test_diagram_utils.py pyproject.toml
git commit -m "feat(pdf): diagram_utils — rect 클러스터 감지 + pymupdf 렌더링"
```

---

## Task 9: `pdf/_pdf.py` — 다이어그램 감지 통합

**Files:**
- Modify: `src/md_converter/pdf/_pdf.py`

- [ ] **Step 1: `_pdf.py` import 추가**

`src/md_converter/pdf/_pdf.py` 상단 import에 추가:
```python
from .diagram_utils import detect_diagram_bboxes, render_bbox_to_png
```

- [ ] **Step 2: `parse()` 함수 내 diagram 처리 추가**

`src/md_converter/pdf/_pdf.py`의 `parse()` 함수에서, `tables = page.find_tables()` 바로 다음에 삽입:

```python
tables = page.find_tables()

# ── 다이어그램 영역 감지 + 렌더링 ─────────────────────────────────────────
table_bboxes = [t.bbox for t in tables]
try:
    diagram_regions = detect_diagram_bboxes(page, table_bboxes)
except Exception as exc:
    import sys
    sys.stderr.write(f"  diagram detection failed (page {page_idx}): {exc}\n")
    diagram_regions = []

for diag_y, diag_bbox in diagram_regions:
    try:
        png_bytes = render_bbox_to_png(data, page_idx, diag_bbox)
    except Exception as exc:
        import sys
        sys.stderr.write(f"  diagram render failed (page {page_idx}): {exc}\n")
        continue
    from .._common import ImageItem  # 이미 import되어 있으면 생략
    item = ImageItem(
        idx=img_counter,
        data=png_bytes,
        mime="image/png",
        ext="png",
        is_diagram=True,
    )
    all_images.append(item)
    img_tokens.append((diag_y, f"[[RHWP_IMAGE:{img_counter}]]"))
    img_counter += 1
```

> `from .._common import ImageItem`는 이미 파일 상단에 있으면 중복 추가 불필요.

- [ ] **Step 3: 기존 PDF 테스트 실행 — 깨진 테스트 없음 확인**

```bash
uv run pytest tests/pdf/ -v
```
Expected: 모든 기존 테스트 PASSED

- [ ] **Step 4: 커밋**

```bash
git add src/md_converter/pdf/_pdf.py
git commit -m "feat(pdf): 다이어그램 영역 감지 + ImageItem(is_diagram=True) 생성"
```

---

## Task 10: `__init__.py` — MdConverter 다이어그램 이미지 파이프라인

**Files:**
- Modify: `src/md_converter/__init__.py`

- [ ] **Step 1: `llm.py`에서 `vision_to_mermaid` import 추가**

`src/md_converter/__init__.py` import 줄 수정:
```python
# 기존:
from .llm import LlmConfig, drawing_to_mermaid, restructure_nested_tables

# 수정:
from .llm import LlmConfig, drawing_to_mermaid, restructure_nested_tables, vision_to_mermaid
```

- [ ] **Step 2: `_process_diagram_images()` 메서드 추가**

`src/md_converter/__init__.py`의 `MdConverter` 클래스에 새 메서드 추가 (`_process_drawings()` 바로 아래):
```python
def _process_diagram_images(self, md: str, image_items: list[ImageItem]) -> str:
    """is_diagram=True 이미지를 vision LLM으로 Mermaid 변환 시도.

    성공 시 RHWP_IMAGE 토큰을 mermaid 블록으로 교체.
    실패 시 토큰을 그대로 두어 _process_images()에서 이미지로 처리.
    """
    for img in image_items:
        if not img.is_diagram:
            continue
        token = f"[[RHWP_IMAGE:{img.idx}]]"
        if token not in md:
            continue
        mermaid = vision_to_mermaid(img.data, self._llm)
        if mermaid:
            sys.stderr.write(f"  diagram image → mermaid (idx={img.idx})\n")
            md = md.replace(token, f"```mermaid\n{mermaid}\n```")
            img.is_diagram = False  # 처리 완료 표시
    return md
```

- [ ] **Step 3: `convert()` 파이프라인에 `_process_diagram_images()` 삽입**

`src/md_converter/__init__.py`의 `convert()` 메서드에서, 기존 순서 변경:
```python
# 기존:
md = self._process_images(md, image_items)
md = self._process_drawings(md)
md = restructure_nested_tables(md, self._llm)

# 수정:
md = self._process_diagram_images(md, image_items)  # diagram → Mermaid 먼저
md = self._process_images(md, image_items)           # 나머지 이미지 처리
md = self._process_drawings(md)
md = restructure_nested_tables(md, self._llm)
```

- [ ] **Step 4: 전체 테스트 실행 — 모든 테스트 통과 확인**

```bash
uv run pytest -v
```
Expected: 모든 테스트 PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/md_converter/__init__.py
git commit -m "feat: MdConverter에 diagram image → Mermaid 파이프라인 추가"
```

---

## 완료 체크리스트

- [ ] Task 1: `_diagram.py` + 테스트
- [ ] Task 2: `hwpx/diagram_utils.py` + 테스트
- [ ] Task 3: HWPX 파서 통합
- [ ] Task 4: `_records.py` + `_common.py` 소규모 추가
- [ ] Task 5: `hwp5/diagram_utils.py` (프레임)
- [ ] Task 6: HWP5 파서 통합
- [ ] Task 7: `llm.py` vision_to_mermaid
- [ ] Task 8: `pdf/diagram_utils.py` + pyproject.toml
- [ ] Task 9: `pdf/_pdf.py` 통합
- [ ] Task 10: `__init__.py` 파이프라인
