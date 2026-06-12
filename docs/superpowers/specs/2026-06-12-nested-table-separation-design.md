# 중첩 표 분리(Nested Table Separation) 설계

**작성일:** 2026-06-12

## 배경 / 문제

HWP/HWPX 문서의 표 셀 안에는 또 다른 표(중첩 표)가 들어 있는 경우가 많다. 현재 동작은 다음과 같다.

- **HWPX** (`hwp/hwpx/_table_utils.py`): `_cell_text()`가 셀 내부 중첩 표를 `[[NT:r0c0|r0c1;r1c0|r1c1]]` 마커로 평탄화한다. 이후 `MdConverter.convert()`가 `restructure_nested_tables(md, llm)`를 호출해 셀마다 LLM에 보내 `<br>`로 구분된 자연어 텍스트로 재구성한다.
- **HWP5** (`hwp/hwp5/_parser.py`): 레코드 상태머신이 테이블 레벨을 **하나만** 추적한다. 셀 안에서 또 다른 TABLE 컨트롤을 만나면 바깥 표 상태(`table_rows` 등)를 덮어써 버린다. → 중첩 표를 감지하지 못할 뿐 아니라, **바깥 표의 누적 행이 유실되는 잠재 버그**가 있다.

**문제점:**

1. **성능 병목.** 01_image HWPX 샘플은 중첩 표 셀이 31개 → LLM 호출 31회(각 ~8초) → 변환에 약 261초가 걸렸다.
2. **구조 손실.** 중첩 표를 `<br>` 자연어로 풀면 원래의 행/열 구조가 사라진다.
3. **HWP5 미지원 + 버그.** HWP5는 중첩 표 자체를 다루지 못하고 바깥 표를 손상시킨다.

## 목표

중첩 표를 **LLM 호출 없이** 별도의 GFM 표로 분리하고, 부모 셀에는 사람이 읽을 수 있는 참조(`→ 표 N`)만 남긴다.

- 중첩 표용 LLM 경로(`[[NT:]]` → `restructure_nested_tables`)를 완전히 제거한다.
- 분리된 표는 **부모 표 블록 바로 뒤**에 배치해 읽기 맥락을 가까이 유지한다.
- HWPX와 HWP5 모두에서 동작한다.

### 비목표 (YAGNI)

- 중첩 2단계 이상(표 안의 표 안의 표)을 각각 분리하지 않는다. 1단계 중첩만 별도 표로 분리하고, 더 깊은 중첩은 평탄 텍스트로 둔다 (현재 HWPX `_cell_plain_text` 동작과 동일).
- `convert()`의 반환 타입은 바꾸지 않는다. 여전히 단일 `str`을 반환하며, 분리된 표는 같은 문서 안에 인라인으로 삽입된다.
- 일반(비중첩) 표의 출력 형태는 바꾸지 않는다.

## 출력 형태 (확정)

부모 표 셀에는 `→ 표 N` 참조만 남기고, 실제 중첩 표는 부모 표 블록 바로 뒤에 `**[표 N]**` 헤더와 함께 둔다.

입력(개념):

```
| 구분 | 세부내용 |
| 본인부담 | <중첩 표> |
| 수가 | 5,000원 |
```

출력:

```
| 구분 | 세부내용 |
| --- | --- |
| 본인부담 | → 표 1 |
| 수가 | 5,000원 |

**[표 1]**

| 항목 | 금액 |
| --- | --- |
| 외래 | 1,000원 |
| 입원 | 2,000원 |
```

### 규칙

- **번호:** 문서 전체 전역 카운터. 등장 순서대로 `표 1, 표 2, …`.
- **셀 참조:** `→ 표 N`.
- **독립 표 헤더:** `**[표 N]**`.
- **한 부모 표에 중첩 표가 여러 개:** 만난 순서대로 번호를 부여하고, 부모 표 블록 뒤에 그 순서대로 삽입한다.
- **빈 중첩 표:** 내용이 비면 참조도 표도 만들지 않고 마커만 제거한다.

## 공유 중간 마커 포맷 (변경 없음)

```
[[NT:r0c0|r0c1;r1c0|r1c1]]
```

- `;` = 행 구분, `|` = 같은 행 안의 셀 구분.
- HWPX `_cell_text()`가 이미 이 형식을 생성한다. HWP5도 이 형식을 내보내도록 만든다.
- 이 마커는 파서(생성)와 파이프라인(소비)을 분리하는 인터페이스 역할을 한다.

## 아키텍처

```
파서 (HWPX / HWP5)              파이프라인 (MdConverter.convert)
─────────────────              ──────────────────────────────
중첩 표 → [[NT:...]] 마커  ──▶   extract_nested_tables(md)
                                  · 전역 번호 부여
                                  · 셀 마커 → "→ 표 N"
                                  · [[NT:]] → 독립 GFM 표
                                  · 부모 표 블록 뒤에 삽입
```

### 컴포넌트

#### ① HWP5 파서: 중첩 표 감지 (테이블 컨텍스트 스택)

**파일:** `src/md_converter/hwp/hwp5/_parser.py`

현재 단일 테이블 상태 변수(`in_table`, `table_ctrl_lvl`, `table_col_count`, `table_rows`, `current_row`, `current_cell_parts`, `in_cell`, `current_row_addr`)를 **컨텍스트 스택**으로 교체한다. 각 컨텍스트는 위 필드를 하나의 단위로 묶는다(예: `dataclass TableCtx`).

레코드 루프 규칙 (각 `(tag_id, level, payload)`에 대해):

1. **닫기:** 스택이 비어있지 않고 `level <= 스택top.ctrl_lvl`인 동안 top을 pop & close 한다.
2. **열기:** `CTRL_HEADER` & `ctrl == _CTRL_TABLE`이면 `ctrl_lvl = level`인 새 컨텍스트를 push.
3. **라우팅:** `TABLE_BODY`(`level == top.ctrl_lvl+1`), `LIST_HEADER`(`level == top.ctrl_lvl+1`), `PARA_TEXT`(셀 안일 때)는 스택 **top** 컨텍스트로 보낸다.
4. **섹션 끝:** 스택에 남은 컨텍스트를 전부 close.

**close 시 분기 (depth = pop 직후 스택에 남은 부모 표 개수):**

- 부모 없음(최외곽, depth 0): GFM 표 문자열을 `parts`에 추가 (기존 동작).
- 부모가 1개(depth 1): 닫힌 표를 `[[NT:rows]]`로 직렬화해 **부모 컨텍스트의 현재 셀**(`current_cell_parts`)에 추가.
- 부모가 2개 이상(depth ≥ 2): 닫힌 표를 평탄 텍스트(예: 행을 공백/구분자로 이어붙임)로 부모 셀에 추가. (1단계만 분리하는 비목표 규칙)

`[[NT:]]` 직렬화 시 셀 텍스트는 누적된 셀 문자열을 그대로 쓴다. `;`/`|`는 행/셀 구분자이므로, 셀 값 안의 리터럴 `|`는 HWPX와 동일하게 처리한다(현행 동작 유지).

GSO(도형/이미지) 처리 로직은 표 상태와 독립이므로 그대로 둔다.

#### ② 새 파이프라인 단계: `extract_nested_tables`

**파일:** `src/md_converter/nested_tables.py` (신규, LLM 불필요, 순수 문자열 처리)

```python
def extract_nested_tables(md: str) -> str:
    """[[NT:...]] 마커를 분리된 GFM 표 + "→ 표 N" 참조로 변환한다."""
```

동작:

1. `md`를 `\n\n` 기준 블록 리스트로 분리.
2. 각 블록을 순회. 블록이 GFM 표이고 `[[NT:`를 포함하면:
   - 그 블록 안의 각 `[[NT:...]]`에 전역 카운터로 번호 N 부여.
   - 셀 안의 마커를 `→ 표 N`으로 치환.
   - `[[NT:]]` 내용을 파싱(`;`→행, `|`→셀)해 독립 GFM 표 블록 생성: `**[표 N]**\n\n` + 표.
   - 생성한 독립 표 블록(들)을 현재 블록 **바로 뒤**에 순서대로 삽입.
3. 전역 카운터는 문서 전체에 걸쳐 단조 증가.
4. 독립 표 렌더 시 셀 값의 `|`는 GFM용으로 이스케이프. 빈 표(`[[NT:]]` 내용이 사실상 비어있음)는 참조/표 없이 마커만 제거.

`[[NT:...]]` 파싱은 기존 `restructure_nested_tables`의 마커 스캔 로직(`[[NT:` 시작, 다음 `]]`까지)을 재사용/이식한다.

#### ③ `MdConverter.convert()` 배선

**파일:** `src/md_converter/__init__.py`

`convert()` 마지막 단계의

```python
md = restructure_nested_tables(md, self._llm)
```

를

```python
md = extract_nested_tables(md)
```

로 교체한다. `from .nested_tables import extract_nested_tables` import 추가, `restructure_nested_tables` import 제거.

#### ④ `llm.py` 정리

**파일:** `src/md_converter/llm.py`

중첩 표용 LLM 코드를 제거: `restructure_nested_tables`, `_call_llm`, `_PROMPT_TEMPLATE`. drawing/vision 관련(`drawing_to_mermaid`, `vision_to_mermaid`, `vision_to_text`, 각 프롬프트)은 유지한다.

## 데이터 흐름 예시 (HWP5)

```
CTRL_HEADER(tbl) @L      → push ctx0 (ctrl_lvl=L)
 TABLE_BODY @L+1         → ctx0
 LIST_HEADER @L+1        → ctx0 새 셀
  PARA_TEXT @L+2         → ctx0 현재 셀
  CTRL_HEADER(tbl) @L+2  → push ctx1 (ctrl_lvl=L+2)   [중첩 시작]
   TABLE_BODY @L+3       → ctx1
   LIST_HEADER @L+3      → ctx1 새 셀
    PARA_TEXT @L+4       → ctx1 현재 셀
 LIST_HEADER @L+1        → close ctx1 (depth 1 → [[NT:]]를 ctx0 셀에), 이어서 ctx0 새 셀
...
(섹션 끝)                → close ctx0 (depth 0 → GFM을 parts에)
```

이후 파이프라인 `extract_nested_tables`가 `parts`에서 합쳐진 마크다운의 `[[NT:]]`를 분리 표 + 참조로 변환.

## 엣지 케이스

- **셀에 텍스트 + 중첩 표 혼재:** `| 본인부담 <br> [[NT:...]] |` → `| 본인부담 <br> → 표 N |` (마커만 치환).
- **중첩 표가 표 밖(일반 문단)에 단독:** 발생하지 않음 — `[[NT:]]`는 항상 셀 텍스트 안에서만 생성됨.
- **2단계 이상 중첩:** 1단계만 `[[NT:]]`로 분리, 더 깊은 것은 평탄 텍스트. (HWP5 close 분기 depth ≥ 2)
- **빈 중첩 표:** 마커만 제거.
- **`[[NT:` 짝 `]]` 누락:** 기존 스캔 로직처럼 안전하게 원문 보존하고 종료.

## 테스트 계획

**단위 — `extract_nested_tables`** (`tests/`):

- 표 셀의 단일 `[[NT:]]` → `→ 표 1` 치환 + 부모 블록 뒤 `**[표 1]**` 표 삽입.
- 한 부모 표에 중첩 표 2개 → `표 1`, `표 2` 순서 부여 및 순서대로 삽입.
- 여러 부모 표에 걸친 전역 카운터 증가.
- 셀 값 `|` 이스케이프.
- 빈 `[[NT:]]` → 마커 제거, 표 미생성.
- `]]` 누락 → 원문 보존.

**HWPX 회귀:** 중첩 표 있는 기존 샘플 → 파서가 `[[NT:]]` 생성(불변), 파이프라인 통과 후 분리 표 + 참조 출력. LLM 호출 0회.

**HWP5 파서:** 중첩 표 포함 픽스처 → 스택이 `[[NT:]]` 생성, 바깥 표 행 유실 없음(버그 수정 검증). 깊은 중첩은 평탄화.

## 검증 (수동)

- **01_image HWPX 재변환:** 중첩 표 LLM 호출 31회 → 0회, 변환시간 261초 → 수 초. 출력에 `→ 표 N` 참조와 `**[표 N]**` 분리 표 존재.
- **HWP5:** 중첩 표를 포함한 샘플 문서를 코퍼스(`clic` 버킷)에서 탐색해 분리 동작 확인.

## 영향 받는 파일 요약

| 파일 | 변경 |
| --- | --- |
| `src/md_converter/hwp/hwp5/_parser.py` | 테이블 단일 상태 → 컨텍스트 스택, 중첩 표 → `[[NT:]]` |
| `src/md_converter/nested_tables.py` | 신규: `extract_nested_tables()` |
| `src/md_converter/__init__.py` | `restructure_nested_tables` → `extract_nested_tables` 배선 |
| `src/md_converter/llm.py` | 중첩 표용 LLM 코드 제거 (drawing/vision 유지) |
| `src/md_converter/hwp/hwpx/_table_utils.py` | 변경 없음 (이미 `[[NT:]]` 생성) |
| `tests/` | 단위 + 회귀 + HWP5 픽스처 |

## 구현 후 보정 (2026-06-12)

구현/검증 중 발견해 반영한 두 가지 명확화:

- **HWPX depth-2 평탄화 패리티.** 본 문서 비목표 항목은 "더 깊은 중첩은 평탄 텍스트로 두며 현재 HWPX _cell_plain_text 동작과 동일"이라고 적었으나, 실제 _cell_plain_text는 이중 중첩 표를 내려가지 않고 버리고 있었다(HWP5는 평탄화해 보존). 의도(평탄화 보존)에 맞춰 hwp/hwpx/_table_utils.py의 _cell_plain_text가 셀 내부의 더 깊은 tbl을 재귀적으로 평탄화하도록 수정했다. 이제 두 백엔드 모두 depth-2+ 내용을 보존한다.
- **extract_nested_tables 블록 게이팅.** 본문은 "블록이 GFM 표이고 [[NT:를 포함하면"이라고 적었으나, 구현은 마커를 포함한 모든 블록을 처리한다(파서는 셀 안에서만 마커를 생성하므로 무해하며 더 일반적). 의도된 동작이다.
