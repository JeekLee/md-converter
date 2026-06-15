# 스캔 PDF OCR 병렬화 설계

**작성일:** 2026-06-15

## 배경 / 문제

스캔 PDF(텍스트 레이어 없음)는 페이지마다 vision LLM OCR(`vision_to_text`)을 호출한다. 현재 `pdf/_pdf.py`의 `parse()`는 페이지 루프 안에서 이를 **인라인·순차** 호출한다(페이지당 ~15초). 3페이지 스캔 문서 변환이 ~47초 걸렸다. 페이지 수에 선형으로 늘어 다중 페이지 스캔 PDF가 느리다.

OCR 호출들은 페이지 간 **독립적**이므로 동시 실행할 수 있다. 호출은 네트워크 I/O 바운드라 스레드만으로 충분하다.

## 목표

스캔 페이지 VLM OCR을 동시 실행해 다중 페이지 스캔 PDF 변환 시간을 단축한다. **출력은 순차 실행과 byte-identical** (페이지 순서·내용 동일).

### 비목표 (YAGNI)

- 텍스트/표/이미지/다이어그램 처리는 병렬화하지 않는다 (이미 빠르거나 fitz 스레드 비안전).
- OCR 결과 캐싱·재시도 정책 변경 없음.
- 풀스캔 대용량 PDF의 메모리 스트리밍 최적화는 범위 밖(추후).

## 설계

### ① API

- `MdConverter(*, llm, images=None, ocr_workers: int = 4)` — 새 인자 추가, 저장.
- `MdConverter.convert()`의 PDF 분기: `_pdf_parse(data, llm=self._llm, max_ocr_workers=self._ocr_workers)`.
- `pdf.parse(data, llm=None, max_ocr_workers: int = 4)` — 새 인자.
- `max_ocr_workers <= 1`이면 순차(현행 동작 그대로). `>= 2`이면 스레드풀.

### ② 렌더는 메인 스레드, OCR HTTP만 병렬 (스레드 안전성)

PyMuPDF(fitz)는 스레드 안전하지 않으므로 `render_bbox_to_png`(fitz 사용)는 **메인 스레드에서 순차** 호출한다(렌더는 ms 단위로 빠름, 병목 아님). 진짜 병목인 `vision_to_text` HTTP 호출만 `concurrent.futures.ThreadPoolExecutor`로 동시 실행한다. 폴백 `ocr_page`(pypdf/pytesseract — 스레드 안전)는 워커 안에서 VLM 빈 응답 시 호출한다.

워커는 pdfplumber `page` 객체를 만지지 않는다(공유 가변 상태 없음) — `page_idx`, 미리 렌더한 `png` bytes, 원본 `data`, `llm`만 받는다.

### ③ 흐름 (parse 내부)

1. **페이지 루프 (메인 스레드):** 각 페이지에 대해
   - `is_scanned_page(page)`이면 → `render_bbox_to_png(data, page_idx, (0,0,w,h))`로 PNG 렌더, `scanned.append((page_idx, png))` 하고 **그 자리에 자리표시**(아래 4와 조립 순서 보존). 일반 페이지는 기존대로 즉시 처리해 `parts`에 추가.
2. **동시 OCR:** `ocr_by_idx = _ocr_pages(scanned, data, llm, max_ocr_workers)` → `{page_idx: text}`.
   - 내부: `max(1, min(max_ocr_workers, len(scanned)))` 워커로 `_ocr_one(png, page_idx, data, llm)`을 제출, 결과 수집. workers<=1 또는 스캔 1페이지면 순차 루프.
   - `_ocr_one`: `vision_to_text(png, llm)`; 빈 결과면 `ocr_page(data, page_idx)` 폴백; 예외는 잡아 `""` 반환(기존과 동일). VLM 성공 시 stderr 로그 유지.
3. **조립:** 최종 `parts`를 만들 때 스캔 페이지 위치에는 `ocr_by_idx[page_idx].strip()`(비어있지 않으면)을 넣는다 → **페이지 등장 순서** 유지.

**조립 순서 보존 방법:** 페이지 루프에서 `parts`에 직접 넣는 대신, 페이지별 결과를 `page_outputs: list[tuple[int, list[str]]]` 또는 자리표시 토큰으로 모아 OCR 완료 후 순서대로 평탄화한다. (스캔 페이지는 OCR 완료 전까지 결과를 모르므로 자리표시 후 채움.) 가장 단순한 구현: 페이지 인덱스별로 `parts` 조각 리스트를 모으고, 스캔 페이지는 OCR dict에서 채운 뒤 인덱스 순서로 이어붙인다.

### ④ 에러 처리

- 워커별 try/except로 한 페이지 OCR 실패가 다른 페이지/전체를 막지 않는다(실패 → `""`, 현행과 동일).
- `render_bbox_to_png` 실패(메인 스레드)도 try/except로 해당 페이지 OCR 스킵.

## 테스트 계획

**단위 (`tests/pdf/`):**
- `_ocr_pages` 결정성: 주입한 fake OCR 함수(예: `page_idx` 기반 고정 문자열, 약간의 지연)로 `max_ocr_workers=1` vs `4` 결과 dict가 **동일**.
- `_ocr_pages` 순서 무관성: 워커가 임의 순서로 끝나도 `{page_idx: text}`가 올바르게 매핑.
- 실패 페이지: fake OCR이 예외를 던지면 해당 idx는 `""`, 나머지는 정상.
- `max_ocr_workers<=1` → 순차 경로.

**통합 (`tests/pdf/`):**
- fitz로 **텍스트 없는 풀페이지 이미지 2페이지** PDF 생성(`is_scanned_page`=True) + `vision_to_text` 몽키패치(페이지별 고정 텍스트) → `parse(data, llm=dummy, max_ocr_workers=4)` 출력에 두 페이지 텍스트가 **순서대로** 등장, `max_ocr_workers=1`과 동일 출력.

**회귀:** 기존 `tests/pdf/` 전부 통과.

**수동 검증:** 실제 스캔 문서(clic 03_diagram PDF, 3페이지)로 `ocr_workers=1` vs `4` wall-clock 비교 → 가속 확인 = **LLM 서버 배칭 지원 여부 실측**. (배칭 미지원이면 가속이 작을 수 있음 — 클라이언트 병렬화는 정상 동작하나 서버가 큐잉.)

## 영향 받는 파일 요약

| 파일 | 변경 |
| --- | --- |
| `src/md_converter/pdf/_pdf.py` | 스캔 페이지 렌더 선처리 + `_ocr_pages`/`_ocr_one` 추가 + `parse(max_ocr_workers)` + 조립 순서 보존 |
| `src/md_converter/__init__.py` | `MdConverter(ocr_workers=4)` 추가 + `_pdf_parse`에 전달 |
| `tests/pdf/` | `_ocr_pages` 단위 + fitz 스캔 PDF 통합 |
