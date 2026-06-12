# HWP Drawing → Diagram Utils 설계

**날짜:** 2026-06-12  
**브랜치:** refactor/pipeline-file-structure

---

## 배경 및 목표

현재 HWP5/HWPX 파서는 도형 블록에서 텍스트 레이블만 추출해 `hwp-drawing` fenced 블록으로 emit한다. 이후 `MdConverter._process_drawings()`가 해당 블록을 텍스트 LLM에 보내 Mermaid로 변환하는데, 텍스트 레이블만으로는 "어떤 도형이 어떤 화살표로 연결되는지"를 알 수 없어 결과가 부정확하다.

**목표:**
- HWPX/HWP5: 포맷에 명시적으로 인코딩된 연결 관계 + 도형 종류를 파싱해 Mermaid를 **결정적으로 생성** (LLM 불필요)
- PDF: 벡터 다이어그램 영역을 감지해 pymupdf로 PNG 렌더링 후 **vision LLM** → Mermaid
- LibreOffice 의존성 없음
- 출력은 사람이 읽을 수 있는 Mermaid 코드 (렌더링 선택적)

---

## 신규 파일 구조

```
src/md_converter/
  _diagram.py                   # NEW: 공유 타입 + graph_to_mermaid()
  llm.py                        # ADD: vision_to_mermaid()
  hwp/
    hwp5/
      diagram_utils.py          # NEW: GSO 레코드 → DiagramGraph
    hwpx/
      diagram_utils.py          # NEW: XML → DiagramGraph
  pdf/
    diagram_utils.py            # NEW: rect 클러스터 감지 + pymupdf 렌더링
  __init__.py                   # UPDATE: 파이프라인 변경
```

---

## 섹션 1: `_diagram.py` — 공유 타입 + Mermaid 생성

### 타입 정의

```python
@dataclass
class ShapeNode:
    id: str
    shape_type: str   # "rect" | "ellipse" | "diamond" | "other"
    label: str

@dataclass
class ShapeEdge:
    from_id: str
    to_id: str
    label: str        # 연결선 레이블 (빈 문자열 가능)
    arrow: bool       # True = 화살표, False = 단순 선

@dataclass
class DiagramGraph:
    nodes: list[ShapeNode]
    edges: list[ShapeEdge]
```

### shape_type → Mermaid 문법 매핑

| shape_type | Mermaid 문법 | 비고 |
|---|---|---|
| `rect` | `[label]` | 기본 직사각형 |
| `ellipse` | `([label])` | 시작/종료 노드 |
| `diamond` | `{label}` | 분기/판단 |
| `other` | `[label]` | polygon, arc 등 폴백 |

### `graph_to_mermaid(graph: DiagramGraph) -> str | None`

- `graph TD` 헤더 고정
- 노드 ID는 `n{id}` (숫자 충돌 방지)
- 엣지 레이블 있을 경우 `-->|레이블|` 형식
- 노드가 없으면 `None` 반환

출력 예시:
```
graph TD
  n1([시작]) --> n2[데이터 검증]
  n2 --> n3{오류?}
  n3 -->|예| n4[에러 로그]
  n3 -->|아니오| n5([종료])
```

---

## 섹션 2: `hwpx/diagram_utils.py`

### `extract_diagram(p: ET.Element) -> DiagramGraph | None`

**파싱 대상:**
- `hp:rect`, `hp:ellipse`, `hp:polygon` 등 drawing shape 태그 → `ShapeNode`
  - `id` 속성 → `ShapeNode.id`
  - `hp:drawText > hp:subList > hp:p` 텍스트 → `ShapeNode.label`
  - 태그명 → `shape_type` 매핑 (`rect`→`rect`, `ellipse`→`ellipse`, `polygon`→`other` 등)
- `hp:connectLine` → `ShapeEdge`
  - `startConnectShapeId` → `from_id`
  - `endConnectShapeId` → `to_id`
  - `endArrow` 속성 (`none` / `arrow`) → `arrow`

**구현 주의:** `hp:connectLine` element 이름과 attribute 이름은 실제 HWPX 샘플 파일로 검증 필요. HWP XML 버전에 따라 다를 수 있음.

**폴백 조건:** `hp:connectLine` 없으면 `None` 반환 → 기존 `_drawing_texts()` 경로 유지

---

## 섹션 3: `hwp5/diagram_utils.py`

### `extract_diagram(gso_records: list[tuple[int, int, bytes]]) -> DiagramGraph | None`

`gso_records`는 GSO CTRL_HEADER 이하의 `(tag_id, level, payload)` 레코드 목록.

**파싱 대상:**
- `TAG_SHAPE_COMPONENT` (0x58)
  - payload offset 0: shape type enum → `shape_type` 매핑
  - payload의 object ID → `ShapeNode.id`
- `TAG_PARA_TEXT` (0x43) — 현재 shape 컨텍스트의 텍스트 → `ShapeNode.label`
- 커넥터 레코드 (TAG_CTRL_HEADER 내 connect ctrl)
  - `fromConnId` / `toConnId` 필드 → `ShapeEdge`

**HWP5 shape type enum → shape_type 매핑:**

| enum 값 | shape_type |
|---|---|
| 0 (line) | `other` |
| 1 (rect) | `rect` |
| 2 (ellipse) | `ellipse` |
| 3 (arc) | `other` |
| 4 (polygon) | `other` |
| 5 (curve) | `other` |

**폴백 조건:** 커넥터 레코드 없으면 `None` → 기존 GSO 텍스트 레이블 경로 유지

**참고:** HWP5 바이너리 오프셋은 HWP Document Format v5.1 스펙 기준. 구현 중 오프셋 불일치 시 스펙 재확인 필요.

---

## 섹션 4: `pdf/diagram_utils.py`

### `detect_diagram_regions(page, table_bboxes, pdf_bytes, page_idx) -> list[tuple[float, bytes]]`

반환: `(y_pos, png_bytes)` 리스트

**감지 알고리즘:**
1. `page.rects`에서 표 bbox와 겹치지 않는 rect 목록 필터링
2. y 좌표 기준으로 근접한 rect 클러스터링 (gap threshold: 20pt)
3. 클러스터 크기 ≥ 3 rect, 해당 영역의 텍스트 밀도 < 임계값 → 다이어그램 후보
4. 클러스터 bbox에 padding 10pt 추가
5. pymupdf(fitz)로 해당 bbox 렌더링 → PNG bytes

**의존성:** `pymupdf` (optional extra, `pip install 'md-converter[pdf]'`에 포함)

---

## 섹션 5: `llm.py` 변경 — `vision_to_mermaid()`

### 추가 함수

```python
def vision_to_mermaid(png_bytes: bytes, cfg: LlmConfig) -> str | None:
```

`LlmConfig` 구조 변경 없음. 동일한 OpenAI-compatible endpoint 사용.

메시지 구조:
```json
{
  "messages": [{
    "role": "user",
    "content": [
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
      {"type": "text", "text": "<프롬프트>"}
    ]
  }]
}
```

프롬프트 방향: "이 다이어그램 이미지를 Mermaid graph TD 코드로 변환하세요. 코드만 출력, 설명 없이."

실패 시 `None` 반환 (stderr 로그 출력).

---

## 섹션 6: `MdConverter` 파이프라인 변경

### HWPX/HWP5 경로

파서에서 연결 데이터가 있을 경우 `hwp-drawing` 블록 대신 `mermaid` 블록을 직접 emit:

```
연결 데이터 있음 → graph_to_mermaid() → ```mermaid\n...\n```
연결 데이터 없음 → ```hwp-drawing\n레이블\n``` (기존 경로)
```

`_process_drawings()`는 기존 `hwp-drawing` 처리 로직 그대로 유지.

### PDF 경로

`_pdf.py`의 `_page_items_ordered()`에 `pdf_bytes: bytes`와 `page_idx: int`를 추가로 전달하고, 내부에서 `detect_diagram_regions()` 호출.

감지된 PNG는 `ImageItem`으로 저장하되 `is_diagram: bool` 플래그 추가:

```python
@dataclass
class ImageItem:
    idx: int
    data: bytes
    mime: str
    ext: str
    is_diagram: bool = False   # ADD
```

`MdConverter._process_images()` 에서 `is_diagram=True`인 항목은:
1. `vision_to_mermaid()` 시도 → 성공 시 ` ```mermaid ... ``` ` 블록으로 대체
2. 실패 시 기존 이미지 링크(`![image N](...)`)로 폴백

---

## 폴백 요약

| 상황 | 출력 |
|---|---|
| HWPX/HWP5 연결 데이터 있음 | `mermaid` 블록 (결정적) |
| HWPX/HWP5 연결 데이터 없음 | 기존 `hwp-drawing` → 텍스트 레이블 |
| PDF 다이어그램 감지 + vision LLM 성공 | `mermaid` 블록 |
| PDF 다이어그램 감지 + vision LLM 실패 | 이미지 링크 |
| PDF 다이어그램 미감지 | 현재와 동일 (텍스트 + 표) |

---

## 의존성

| 추가 패키지 | 용도 | 필수 여부 |
|---|---|---|
| `pymupdf` | PDF 페이지 렌더링 | pdf extra에 추가 |

기존 `pdfplumber`, `olefile`, OpenAI-compatible LLM endpoint 그대로 사용.
