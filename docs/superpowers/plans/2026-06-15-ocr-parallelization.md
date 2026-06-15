# 스캔 PDF OCR 병렬화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스캔 PDF의 페이지별 vision OCR을 스레드풀로 동시 실행해 변환 시간을 단축한다 (출력은 순차와 동일).

**Architecture:** fitz 렌더(스레드 비안전)는 메인 스레드에서 선처리하고, 병목인 `vision_to_text` HTTP 호출만 `ThreadPoolExecutor`로 병렬화한다. 결과를 `{page_idx: text}`로 모아 페이지 순서대로 조립한다. 동시성은 `MdConverter(ocr_workers=4)` → `parse(max_ocr_workers)`로 노출.

**Tech Stack:** Python 3.11+, `concurrent.futures` (stdlib), pdfplumber, pymupdf=fitz, pytest.

**Spec:** `docs/superpowers/specs/2026-06-15-ocr-parallelization-design.md`

**전제:** 작업 브랜치 `feat/ocr-parallel`. 테스트는 `.venv/bin/python -m pytest <path> -v` (bare python/pytest는 rtk 훅으로 실패).

---

## 배경 (구현자 필수 컨텍스트)

현재 `src/md_converter/pdf/_pdf.py`의 `parse()`는 페이지 루프 안에서 스캔 페이지를 만나면 그 자리에서 `render_bbox_to_png` → `vision_to_text` → 빈 결과면 `ocr_page`(pytesseract 폴백)을 **순차** 호출하고 결과를 `parts`에 추가한다. 페이지당 ~15초라 다중 페이지 스캔 PDF가 느리다.

OCR은 페이지 간 독립적이고 네트워크 I/O 바운드이므로 스레드로 병렬화 가능하다. 단, PyMuPDF(fitz)는 스레드 안전하지 않으므로 렌더는 메인 스레드에 둔다.

`parse()` 끝부분 현재 코드 (참고):
```python
            for _, chunk in _page_items_ordered(page, tables, img_tokens):
                if chunk.strip():
                    parts.append(chunk.strip())

    md = "\n\n".join(parts)
    md = merge_overflow_tables(md)
    return md.strip(), all_images
```

---

## Task 1: OCR 헬퍼 `_ocr_one` / `_ocr_pages`

순수하게 추가만 한다(아직 `parse()`는 옛 경로 사용). `ocr_fn` 주입으로 실 PDF 없이 단위 테스트.

**Files:**
- Modify: `src/md_converter/pdf/_pdf.py`
- Test: `tests/pdf/test_ocr_parallel.py` (신규)

- [ ] **Step 1: 실패 테스트 작성** — Create `tests/pdf/test_ocr_parallel.py`:

```python
"""Tests for parallel scanned-page OCR (_ocr_pages)."""
from __future__ import annotations

import time

from md_converter.pdf._pdf import _ocr_pages


def test_ocr_pages_deterministic_parallel_vs_sequential():
    scanned = [(0, b"p0"), (1, b"p1"), (2, b"p2")]
    fake = lambda png, idx: f"TEXT{idx}"
    seq = _ocr_pages(scanned, b"", None, 1, ocr_fn=fake)
    par = _ocr_pages(scanned, b"", None, 4, ocr_fn=fake)
    assert seq == par == {0: "TEXT0", 1: "TEXT1", 2: "TEXT2"}


def test_ocr_pages_order_independent():
    # later pages finish first; mapping must stay keyed by page_idx
    scanned = [(0, b""), (1, b""), (2, b""), (3, b"")]

    def fake(png, idx):
        time.sleep(0.02 * (4 - idx))
        return f"T{idx}"

    res = _ocr_pages(scanned, b"", None, 4, ocr_fn=fake)
    assert res == {0: "T0", 1: "T1", 2: "T2", 3: "T3"}


def test_ocr_pages_failure_isolated():
    def fake(png, idx):
        if idx == 1:
            raise RuntimeError("boom")
        return f"T{idx}"

    res = _ocr_pages([(0, b""), (1, b""), (2, b"")], b"", None, 4, ocr_fn=fake)
    assert res == {0: "T0", 1: "", 2: "T2"}


def test_ocr_pages_failure_isolated_sequential():
    def fake(png, idx):
        if idx == 0:
            raise RuntimeError("boom")
        return "ok"

    res = _ocr_pages([(0, b""), (1, b"")], b"", None, 1, ocr_fn=fake)
    assert res == {0: "", 1: "ok"}


def test_ocr_pages_empty():
    assert _ocr_pages([], b"", None, 4) == {}


def test_ocr_pages_single_uses_sequential():
    res = _ocr_pages([(0, b"x")], b"", None, 4, ocr_fn=lambda png, idx: "ONE")
    assert res == {0: "ONE"}
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/pdf/test_ocr_parallel.py -v`
Expected: FAIL with `ImportError: cannot import name '_ocr_pages' from 'md_converter.pdf._pdf'`.

- [ ] **Step 3: 헬퍼 구현** — In `src/md_converter/pdf/_pdf.py`, add the `ThreadPoolExecutor` import to the top-of-file imports (after `import re`):

```python
from concurrent.futures import ThreadPoolExecutor
```

Then add these two functions just above `def parse(`:

```python
def _ocr_one(png: bytes, page_idx: int, data: bytes, llm) -> str:
    """OCR a single pre-rendered scanned page: vision LLM, then pytesseract fallback."""
    import sys
    text = ""
    if llm is not None:
        try:
            from ..llm import vision_to_text
            text = vision_to_text(png, llm)
            if text:
                sys.stderr.write(f"  VLM OCR page {page_idx}: {len(text)} chars\n")
        except Exception as exc:
            sys.stderr.write(f"  VLM OCR failed (page {page_idx}): {exc}\n")
    if not text:
        text = ocr_page(data, page_idx)
    return text or ""


def _ocr_pages(scanned, data: bytes, llm, max_workers: int, ocr_fn=None) -> dict[int, str]:
    """OCR pre-rendered scanned pages, concurrently when max_workers >= 2.

    scanned: list[(page_idx, png_bytes)].
    ocr_fn:  injectable (png, page_idx) -> str for tests; default calls _ocr_one.
    Returns {page_idx: text}. Per-page failures are isolated to "".
    """
    if not scanned:
        return {}
    fn = ocr_fn or (lambda png, idx: _ocr_one(png, idx, data, llm))

    if max_workers is None or max_workers <= 1 or len(scanned) == 1:
        out: dict[int, str] = {}
        for idx, png in scanned:
            try:
                out[idx] = fn(png, idx)
            except Exception:
                out[idx] = ""
        return out

    workers = min(max_workers, len(scanned))
    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_to_idx = {ex.submit(fn, png, idx): idx for idx, png in scanned}
        for fut, idx in future_to_idx.items():
            try:
                results[idx] = fut.result()
            except Exception:
                results[idx] = ""
    return results
```

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/python -m pytest tests/pdf/test_ocr_parallel.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: 커밋**

```bash
git add src/md_converter/pdf/_pdf.py tests/pdf/test_ocr_parallel.py
git commit -m "feat(pdf): 스캔 페이지 OCR 헬퍼 _ocr_one/_ocr_pages (스레드풀, 실패 격리)"
```

---

## Task 2: `parse()` 리팩터 — 렌더 선처리 + 병렬 OCR + 순서 보존

**Files:**
- Modify: `src/md_converter/pdf/_pdf.py` (replace `parse`)
- Test: `tests/pdf/test_ocr_parallel.py` (통합 테스트 추가)

- [ ] **Step 1: 통합 테스트 작성** — Append to `tests/pdf/test_ocr_parallel.py`:

```python
def _make_scanned_pdf(n_pages: int) -> bytes:
    """A PDF whose pages are full-page images with no text layer (scanned-like)."""
    import pytest
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for _ in range(n_pages):
        page = doc.new_page(width=300, height=400)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400))
        pix.clear_with(240)
        page.insert_image(fitz.Rect(0, 0, 300, 400), pixmap=pix)
    return doc.tobytes()


def test_parse_scanned_pdf_order_and_parallel_equiv(monkeypatch):
    import io
    import pytest
    pytest.importorskip("fitz")
    import pdfplumber
    from md_converter.pdf import parse
    import md_converter.pdf._pdf as pdfmod
    from md_converter.pdf._ocr import is_scanned_page

    data = _make_scanned_pdf(2)

    # only meaningful if pdfplumber agrees both pages are scanned
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if not all(is_scanned_page(p) for p in pdf.pages):
            pytest.skip("fitz-built PDF not detected as scanned by pdfplumber")

    # mock the actual OCR; return page-specific text so we can check ordering
    monkeypatch.setattr(pdfmod, "_ocr_one", lambda png, idx, data, llm: f"OCRPAGE{idx}")

    md4, _ = parse(data, llm=object(), max_ocr_workers=4)
    md1, _ = parse(data, llm=object(), max_ocr_workers=1)

    assert "OCRPAGE0" in md4 and "OCRPAGE1" in md4
    assert md4.index("OCRPAGE0") < md4.index("OCRPAGE1")   # page order preserved
    assert md4 == md1                                       # parallel == sequential output
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/pdf/test_ocr_parallel.py::test_parse_scanned_pdf_order_and_parallel_equiv -v`
Expected: FAIL — `parse()` does not yet accept `max_ocr_workers` (TypeError: unexpected keyword argument).

- [ ] **Step 3: `parse()` 교체** — In `src/md_converter/pdf/_pdf.py`, replace the **entire** `parse` function with:

```python
def parse(data: bytes, llm: "LlmConfig | None" = None, max_ocr_workers: int = 4) -> tuple[str, list[ImageItem]]:
    """Parse a PDF and return (markdown_string, image_items).

    Requires ``pdfplumber`` and ``pypdf`` (``pip install 'md-converter[pdf]'``).

    - Text PDFs: text + tables extracted; embedded non-trivial images returned
      as ImageItem list with [[RHWP_IMAGE:N]] tokens.
    - Scanned PDFs (no text layer): each page is rendered (main thread) and OCR'd
      via the vision LLM; OCR runs concurrently across pages when max_ocr_workers
      >= 2 (pytesseract fallback when no LLM result). Output order is by page.

    Tables are rendered as GFM and adjacent continuation / overflow / duplicate
    tables are merged post-extraction.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError(
            "pdfplumber is required for PDF conversion. "
            "Install with: pip install 'md-converter[pdf]'"
        ) from exc

    all_images: list[ImageItem] = []
    img_counter = 1
    parts: list[str] = []

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        n_pages = len(pdf.pages)
        page_parts: list[list[str]] = [[] for _ in range(n_pages)]
        scanned: list[tuple[int, bytes]] = []

        for page_idx, page in enumerate(pdf.pages):

            # ── Scanned page: render now (main thread; fitz isn't thread-safe) ──
            if is_scanned_page(page):
                try:
                    png = render_bbox_to_png(data, page_idx, (0, 0, page.width, page.height))
                    scanned.append((page_idx, png))
                except Exception as exc:
                    import sys
                    sys.stderr.write(f"  scanned render failed (page {page_idx}): {exc}\n")
                continue

            # ── Normal page: extract embedded images ──────────────────────────
            try:
                img_items = extract_page_images(data, page_idx, page, start_idx=img_counter)
            except ImportError:
                img_items = []

            img_tokens: list[tuple[float, str]] = []
            for top_y, item in img_items:
                img_tokens.append((top_y, image_token(item.idx)))
                all_images.append(item)
                img_counter += 1

            # ── Interleave text / tables / images in y-order ──────────────────
            tables = page.find_tables()

            # ── 다이어그램 영역 감지 + 렌더링 ─────────────────────────────
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
                item = ImageItem(
                    idx=img_counter,
                    data=png_bytes,
                    mime="image/png",
                    ext="png",
                    is_diagram=True,
                )
                all_images.append(item)
                img_tokens.append((diag_y, image_token(img_counter)))
                img_counter += 1

            for _, chunk in _page_items_ordered(page, tables, img_tokens):
                if chunk.strip():
                    page_parts[page_idx].append(chunk.strip())

        # ── Scanned-page OCR (HTTP-bound) — concurrent across pages ───────────
        ocr_by_idx = _ocr_pages(scanned, data, llm, max_ocr_workers)
        for page_idx, text in ocr_by_idx.items():
            if text.strip():
                page_parts[page_idx].append(text.strip())

        parts = [chunk for pp in page_parts for chunk in pp]

    md = "\n\n".join(parts)
    md = merge_overflow_tables(md)
    return md.strip(), all_images
```

- [ ] **Step 4: 통합 테스트 통과 확인** — Run: `.venv/bin/python -m pytest tests/pdf/test_ocr_parallel.py -v`
Expected: PASS (the integration test passes, or SKIPs if pdfplumber doesn't see the fitz pages as scanned).

- [ ] **Step 5: PDF 회귀 확인** — Run: `.venv/bin/python -m pytest tests/pdf/ -v`
Expected: PASS (text-PDF output unchanged — `page_parts` flattened in page order equals the old `parts`).

- [ ] **Step 6: 커밋**

```bash
git add src/md_converter/pdf/_pdf.py tests/pdf/test_ocr_parallel.py
git commit -m "feat(pdf): parse() 스캔 OCR 병렬화 — 렌더 선처리 + max_ocr_workers + 페이지 순서 보존"
```

---

## Task 3: `MdConverter(ocr_workers=4)` 노출

**Files:**
- Modify: `src/md_converter/__init__.py`
- Test: `tests/hwp/` 아님 — `tests/pdf/test_ocr_parallel.py` (배선 테스트 추가)

- [ ] **Step 1: 배선 테스트 작성** — Append to `tests/pdf/test_ocr_parallel.py`:

```python
def test_mdconverter_passes_ocr_workers(monkeypatch):
    import md_converter as mc
    from md_converter import MdConverter, LlmConfig

    captured = {}

    def fake_pdf_parse(data, llm=None, max_ocr_workers=4):
        captured["max_ocr_workers"] = max_ocr_workers
        return "ok", []

    monkeypatch.setattr(mc, "_pdf_parse", fake_pdf_parse)
    conv = MdConverter(llm=LlmConfig(url="x", api_key="x", model="x"), ocr_workers=7)
    out = conv.convert(b"%PDF-1.4 fake", suffix=".pdf")
    assert out == "ok"
    assert captured["max_ocr_workers"] == 7
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/pdf/test_ocr_parallel.py::test_mdconverter_passes_ocr_workers -v`
Expected: FAIL — `MdConverter.__init__` has no `ocr_workers` argument (TypeError).

- [ ] **Step 3: `MdConverter` 수정** — In `src/md_converter/__init__.py`, change the `__init__` signature and body. Replace:

```python
    def __init__(
        self,
        *,
        llm: LlmConfig,
        images: S3Config | LocalImages | None = None,
    ) -> None:
        self._images = images
        self._llm = llm
```

with:

```python
    def __init__(
        self,
        *,
        llm: LlmConfig,
        images: S3Config | LocalImages | None = None,
        ocr_workers: int = 4,
    ) -> None:
        self._images = images
        self._llm = llm
        self._ocr_workers = ocr_workers
```

Then change the PDF dispatch line. Replace:

```python
        elif s == ".pdf":
            md, image_items = _pdf_parse(data, llm=self._llm)
```

with:

```python
        elif s == ".pdf":
            md, image_items = _pdf_parse(data, llm=self._llm, max_ocr_workers=self._ocr_workers)
```

Also update the `MdConverter` class docstring `Args:` block to document `ocr_workers`: add a line after the `images:` description:

```python
        ocr_workers: max concurrent scanned-PDF OCR calls (default 4; <=1 = sequential).
```

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/python -m pytest tests/pdf/test_ocr_parallel.py -v`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add src/md_converter/__init__.py tests/pdf/test_ocr_parallel.py
git commit -m "feat: MdConverter(ocr_workers=4) — 스캔 PDF OCR 동시성 노출"
```

---

## Task 4: 전체 회귀 + 수동 검증

**Files:** 없음 (검증 전용)

- [ ] **Step 1: 전체 스위트** — Run: `.venv/bin/python -m pytest -v`
Expected: PASS (신규 OCR 테스트 포함 전체 통과; 실파일 의존 테스트는 샘플 유무에 따라 pass/skip).

- [ ] **Step 2: (수동) 실문서 가속 실측** — clic `03_diagram` PDF(스캔 3페이지)를 `MdConverter(ocr_workers=1)` vs `ocr_workers=4`로 변환해 wall-clock 비교. 동일 출력 + 시간 단축이면 서버 배칭 지원. 가속이 미미하면 서버가 요청을 직렬 처리하는 것(클라이언트 병렬화는 정상, 서버 큐잉) — 스펙의 한계 노트대로 기록.

---

## Self-Review (작성자 점검 결과)

**1. Spec coverage:**
- API `MdConverter(ocr_workers=4)` + `parse(max_ocr_workers)` → Task 3, Task 2.
- 렌더 메인 스레드 / OCR HTTP만 병렬 → Task 2 `parse` (scanned 렌더 선처리) + Task 1 `_ocr_pages`(ThreadPoolExecutor).
- `_ocr_one`(VLM→폴백) → Task 1.
- 순서 보존(page_parts 인덱스 조립) → Task 2.
- 에러 격리(워커별 try/except, 양 경로) → Task 1 + 테스트.
- `max_ocr_workers<=1` 순차 → Task 1 분기 + 테스트.
- 테스트: `_ocr_pages` 결정성/순서/실패(Task 1), fitz 스캔 PDF 통합·순차==병렬(Task 2), 배선(Task 3), 회귀/수동(Task 4).

**2. Placeholder scan:** TBD/TODO 없음. 모든 코드 스텝에 완전한 코드. 통합 테스트는 pdfplumber 미감지 시 명시적 skip(모호한 "adjust" 없음).

**3. Type consistency:** `_ocr_one(png, page_idx, data, llm) -> str`, `_ocr_pages(scanned, data, llm, max_workers, ocr_fn=None) -> dict[int,str]`, `parse(data, llm=None, max_ocr_workers=4)`, `MdConverter(..., ocr_workers=4)` → `_pdf_parse(data, llm=, max_ocr_workers=)` 일관. 통합 테스트의 monkeypatch 대상 `md_converter.pdf._pdf._ocr_one` 시그니처가 Task 1 정의와 일치. `scanned`는 `list[(int, bytes)]`로 통일.
