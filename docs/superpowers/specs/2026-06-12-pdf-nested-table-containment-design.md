# PDF 중첩 표 분리 (containment 기반) 설계

**작성일:** 2026-06-12

## 배경 / 문제

`extract_nested_tables`(LLM 없는 분리 파이프라인)는 `[[NT:...]]` 마커만 처리하며, 현재 이 마커는 HWP/HWPX 파서만 생성한다. PDF는 마커를 만들지 않으므로 중첩 표가 분리되지 않는다 → 같은 문서라도 HWPX는 `→ 표 N` + 분리 표, PDF는 미분리로 출력이 갈린다.

**실측 발견 (이 설계의 핵심 근거):** PDF에서 구조가 소실된 게 아니다. clic `01_image` PDF 페이지 4를 pdfplumber로 탐침한 결과:

- `find_tables()`가 **표 2개**를 반환했다.
  - `table#0` (3열) — 바깥 Q&A 표. "답변" 셀 bbox ≈ (174, 82, 551, 500)가 거대하고, 그 안에 코드/부위 격자가 **텍스트로 평탄화**돼 있다(`코드 부위 코드 부위 A 뇌 H 남성생식기 …`). 이 셀 **내부에 ruling line 18개·rect 4개**가 존재 → 중첩 표가 괘선 있음.
  - `table#1` (8행×4열) — 코드/부위 격자가 **pdfplumber에 의해 이미 정확히 별도 표로 추출됨**. bbox ≈ (179, 119, 467, 228)로 `table#0`의 답변 셀 안에 들어있다.
- 그런데 최종 출력에는 깔끔한 `table#1`이 **없다**. `merge_overflow_tables`의 `is_subtable` 규칙("B 헤더가 A의 마지막 셀 텍스트에 들어있으면 B를 버림")이 `table#1`을 **버렸다**. 반대로 같은 문서의 다른 중첩 표(`코드 해부학적 부위 검사구분 …`)는 안 버려지고 **마커 없이 별도 표로 새어나왔다** → 처리가 들쭉날쭉.

즉 괘선 있는 중첩 표는 pdfplumber가 별도 Table로 잘 추출하지만, 후처리가 (a) 버리거나 (b) 마커 없이 흘려보내 HWPX 같은 "참조 + 분리 표"가 되지 못한다.

## 목표

PDF 파서가 중첩 표를 **`[[NT:...]]` 마커로 부모 셀에 넣게** 만든다. 그러면 이미 동작 중인 `extract_nested_tables`(`MdConverter.convert()` 마지막 단계)가 HWPX와 **동일한** `→ 표 N` + `**[표 N]**` 분리를 수행한다.

- **VLM/LLM 호출 없음.** 순수 기하(bbox 포함 관계) + pdfplumber crop.
- PDF 전용 분리 로직을 새로 만들지 않는다 — 공유 마커 `[[NT:...]]`와 기존 파이프라인을 재사용해 **포맷 간 일관성**을 자동 확보.

### 비목표 (YAGNI)

- **괘선 없는 정렬-텍스트 표**(pdfplumber가 별도 Table로 아예 감지 못 하는 경우)는 다루지 않는다. 이는 VLM 재구성이 필요한 별개 과제로 남긴다.
- **2단계 이상 중첩을 각각 분리하지 않는다.** 최상위 표의 셀에 직접 든 1단계 중첩만 `[[NT:]]`로 만들고, 더 깊은 중첩은 평탄 텍스트로 흡수한다 (HWP5/HWPX와 동일).
- 비중첩 일반 표의 출력 형태는 바꾸지 않는다.
- `extract_nested_tables`/`nested_tables.py`는 변경하지 않는다 (PDF는 같은 입력 포맷을 만들어 줄 뿐).

## 공유 마커 포맷 (변경 없음)

```
[[NT:r0c0|r0c1;r1c0|r1c1]]
```

`;` = 행 구분, `|` = 같은 행의 셀 구분. HWP/HWPX가 쓰는 것과 동일.

## 아키텍처

```
pdfplumber find_tables()  ──▶  containment 해소 (페이지 단위, 렌더 직전)
                                · sub.bbox ⊂ parent 셀 bbox 인 쌍 탐지
                                · 포함된 sub 는 standalone 목록에서 제외
                                · 최상위 부모 셀 = prefix + [[NT:sub rows]] + suffix 로 재구성
                                       │
                                       ▼
                                table_to_md (마커 셀은 이스케이프 건너뜀)
                                       │
                                       ▼  (parse() 가 반환한 md)
                                MdConverter.convert() → extract_nested_tables(md)
                                       │
                                       ▼
                                → 표 N  +  **[표 N]**   (HWPX와 동일)
```

### 컴포넌트

#### ① 포함 감지 (순수 기하) — `src/md_converter/pdf/_table_utils.py`

pdfplumber Table API (탐침으로 확인): `t.bbox = (x0, top, x1, bottom)`; `t.rows[i].cells[j]`는 셀 bbox 튜플 `(x0, top, x1, bottom)` 또는 병합/빈 셀이면 `None`; `t.extract()`는 `list[list[str|None]]`.

```python
def bbox_in_cell(sub_bbox, cell_bbox, tol=2.0) -> bool:
    """sub_bbox 가 cell_bbox 안에 (tol 여유로) 완전히 들어가는가."""
```

- 페이지의 표 목록에서, 각 표 `sub`에 대해 **다른** 표 `parent`의 셀들을 훑어 `sub.bbox`를 포함하는 셀을 찾는다(`cells[j]`가 `None`이면 스킵). 가장 작은 포함 셀을 가진 parent를 "직접 부모"로 본다.
- 포함 관계를 `contained: dict[sub_idx -> (parent_idx, cell_index)]`로 수집.

#### ② `[[NT:]]` 직렬화 + 셀 정제 분리 — `src/md_converter/pdf/_table_utils.py`

현재 `_cell_text(cell)`은 줄-병합 + CJK 간격보정 + **파이프 이스케이프**를 한 번에 한다. 마커 직렬화에는 이스케이프 전 정제 텍스트가 필요하므로 정제 단계를 분리한다.

```python
def _clean_cell(cell: str | None) -> str:
    """줄-병합 + CJK 간격보정 (파이프 이스케이프는 안 함)."""

def _cell_text(cell):                     # 기존 동작 유지
    return _clean_cell(cell).replace("|", "\\|").strip()  # (현행과 동일 결과)

def serialize_nt(rows: list[list[str|None]]) -> str:
    """sub-table rows 를 [[NT:행;행]] 로 직렬화. 전부 공백이면 '' 반환."""
    # 각 셀은 _clean_cell 로 정제(이스케이프 X); '|' 로 셀, ';' 로 행 결합
```

#### ③ 마커 보존 이스케이프 — `src/md_converter/pdf/_table_utils.py`

```python
def _escape_cell_for_table(s: str) -> str:
    """'[[NT:' 포함 셀은 그대로 두고(마커 보존), 아니면 _cell_text 적용."""
    return s if "[[NT:" in s else _cell_text(s)
```

`table_to_md`가 각 셀에 `_cell_text` 대신 `_escape_cell_for_table`를 쓰도록 바꾼다. 이러면 ①②에서 **미리 조립한 마커 셀**(이스케이프된 prefix/suffix + 생(raw) 마커)이 재가공되지 않는다. HWP5/HWPX의 `_escape_cell_for_table`와 동일한 패턴.

#### ④ 부모 셀 재구성 (crop 기반) — `src/md_converter/pdf/_pdf.py`

부모 셀을 통째로 마커로 바꾸면 셀 안의 주변 텍스트(예: "…(기재형식) 해부학적 구분코드…" 앞부분, "(예시1)…" 뒷부분)가 유실된다. 그래서 sub-table의 세로 위치로 셀을 잘라 재구성한다.

- 부모 셀 bbox `(cx0, ct, cx1, cb)`, 그 셀에 든 sub-table들을 `top` 기준 정렬.
- y-밴드로 분할: `(ct, sub0.top)`=prefix, `sub0` 자리=`[[NT:]]`, `(sub0.bottom, sub1.top)`=중간텍스트, …, `(subN.bottom, cb)`=suffix.
- 각 텍스트 밴드는 `page.crop((cx0, band_top, cx1, band_bottom)).extract_text(x_tolerance=3, y_tolerance=3)`로 추출 후 `_clean_cell`→이스케이프(즉 `_cell_text`)로 정제. 마커는 `serialize_nt(sub.extract())`로 생성(생 문자열, 이스케이프 X).
- 셀 값 = 비어있지 않은 [정제 텍스트 / 마커]들을 등장 순서대로 `" "`로 결합.

#### ⑤ 통합 — `src/md_converter/pdf/_pdf.py`

`_page_items_ordered`(또는 그 직전)에서:

1. `tables = page.find_tables()` 후 `resolve_nested(page, tables)` 호출 → `(suppressed: set[int], overrides: dict[int, rows])`.
   - `suppressed`: 다른 표 셀에 포함된 모든 sub-table 인덱스 (standalone 렌더에서 제외 → 누수/중복 방지).
   - `overrides`: 자식을 가진 **최상위(=다른 표에 포함되지 않은)** 표의 인덱스 → 재구성된 rows. 자식이 또 자식을 가지면(2단계+) 그 내용은 자식의 `extract()` 셀 텍스트에 이미 평탄 포함되므로 1단계만 마커가 된다.
2. 표 세그먼트 생성 시 `suppressed`는 건너뛰고, `overrides`에 있으면 그 rows를, 없으면 `table.extract()`를 `table_to_md`에 넘긴다.

`merge_overflow_tables`의 `is_subtable`은 이제 포함 sub-table이 사전 소비되어 도달하지 않으므로 충돌하지 않는다(규칙 자체는 보존 — 다른 케이스 안전망).

## 데이터 흐름 예시 (01_image PDF p.4)

```
find_tables → [table#0(3col, 답변셀 bbox⊃table#1), table#1(8x4)]
resolve_nested:
  table#1.bbox ⊂ table#0.cells[1][2]  → contained{1:(0, (1,2))}
  suppressed={1}
  overrides{0: rows where cell(1,2)= "…구체적 사유 [[NT:코드|부위|코드|부위;A|뇌|H|남성생식기…]] (예시1)…"}
table_to_md(table#0 override) → 답변 셀에 마커 포함된 GFM
parse() 반환 → convert() → extract_nested_tables →
  | 7 … | … | …구체적 사유 → 표 1 (예시1)… |

  **[표 1]**
  | 코드 | 부위 | 코드 | 부위 |
  | A | 뇌 | H | 남성생식기 | …
```

→ HWPX 출력과 동일 구조.

## 엣지 케이스

- **한 셀에 sub-table 여러 개:** y순으로 마커 여러 개 + 사이 텍스트 보존.
- **어느 셀에도 포함되지 않은 표:** 기존대로 standalone 렌더(변경 없음).
- **2단계+ 중첩:** 1단계만 마커, 더 깊은 것은 자식 `extract()`의 평탄 텍스트로 흡수(HWP5/HWPX와 동일). 깊은 sub-table Table도 `suppressed`에 넣어 누수 방지.
- **`cells[j]`가 `None`(병합/빈 셀):** 포함 후보에서 스킵.
- **빈 sub-table:** `serialize_nt`가 `''` 반환 → 마커 없이 prefix/suffix만(이후 `extract_nested_tables`가 처리할 마커 없음).
- **crop 텍스트가 비는 prefix/suffix:** 건너뜀(빈 조각 결합 안 함).
- **sub.bbox가 부모 셀과 거의 동일(셀 전체가 표):** prefix/suffix 비고 마커만 → 셀 값 = 마커. 정상.

## 테스트 계획

**단위 (`tests/pdf/test_table_utils.py`) — 실 PDF 불필요, 순수 함수:**

- `bbox_in_cell`: 완전 포함 True, 부분 겹침 False, tol 경계, 동일 bbox 처리.
- `serialize_nt`: `[['항목','금액'],['외래','1000']]` → `[[NT:항목|금액;외래|1000]]`; 전부 공백 → `''`; 셀 정제(줄병합/CJK 간격) 적용 확인.
- `_escape_cell_for_table`: `[[NT:` 포함 셀은 그대로, 일반 셀은 파이프 이스케이프; `table_to_md`가 마커 셀을 안 깨뜨림.
- `_clean_cell` vs `_cell_text` 분리가 기존 `_cell_text` 결과를 바꾸지 않음(회귀).

**통합 (`tests/pdf/test_nested_containment.py`, 신규) — fitz로 PDF 생성:**

- pymupdf(fitz, 기존 의존성)로 **괘선 있는 중첩 표**가 든 작은 PDF를 테스트 내에서 합성(바깥 표 + 한 셀 안에 작은 표, 둘 다 테두리 draw + 텍스트 insert).
- `md_converter.pdf.parse(data)` → 부모 셀에 `[[NT:...]]` 생성 확인, sub-table이 standalone으로 중복 안 됨 확인.
- `MdConverter.convert(data, suffix='.pdf', llm=dummy)` 또는 parse 후 `extract_nested_tables` → `→ 표 1` + `**[표 1]**` 분리 확인. (LLM 더미, 다이어그램/이미지 없는 PDF라 호출 0회.)

**회귀:** 기존 `tests/pdf/test_table_utils.py`, `tests/pdf/test_diagram_utils.py` 전부 통과.

## 검증 (수동)

- clic `01_image` PDF 재변환 → `→ 표 N` 참조 + `**[표 N]**` 분리 표 등장, HWPX 출력과 대조. `02_table` PDF 회귀(과분리/누락 없는지) 확인.

## 영향 받는 파일 요약

| 파일 | 변경 |
| --- | --- |
| `src/md_converter/pdf/_table_utils.py` | `_clean_cell` 분리, `serialize_nt`, `_escape_cell_for_table`, `bbox_in_cell` 추가; `table_to_md`가 마커 보존 이스케이프 사용 |
| `src/md_converter/pdf/_pdf.py` | `resolve_nested(page, tables)` 추가 + `_page_items_ordered`에서 포함 표 소비/제외 |
| `src/md_converter/nested_tables.py` | 변경 없음 (PDF가 같은 마커를 공급) |
| `tests/pdf/` | 단위 + fitz 합성 통합 테스트 |
