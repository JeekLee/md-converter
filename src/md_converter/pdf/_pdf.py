"""PDF → Markdown parser using pdfplumber (MIT)."""
from __future__ import annotations

import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from .._common import ImageItem
from .diagram_utils import detect_diagram_bboxes, render_bbox_to_png
from ._image_utils import extract_page_images, image_token
from ._ocr import is_scanned_page, ocr_page
from ._table_utils import (
    bbox_area,
    bbox_in_cell,
    bbox_near_equal,
    merge_overflow_tables,
    serialize_nt,
    table_to_md,
    _cell_text,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import pdfplumber
    from ..llm import LlmConfig

_PAGE_NUM_RE = re.compile(r"(?m)^\s*(?:-\s*)?\d+\s*(?:-\s*)?$")


def _strip_page_numbers(text: str) -> str:
    return _PAGE_NUM_RE.sub("", text)


def _crop_text(page, x0: float, top: float, x1: float, bottom: float) -> str:
    """Extract cleaned text from a sub-region of the page; '' on any failure."""
    if bottom - top <= 2 or x1 - x0 <= 2:
        return ""
    try:
        crop = page.crop((x0, top, x1, bottom))
        return (crop.extract_text(x_tolerance=3, y_tolerance=3) or "").strip()
    except Exception:
        return ""


def _rebuild_cell(page, cell_bbox, sub_tables) -> str:
    """Rebuild a parent cell as prefix + [[NT:...]] markers + suffix.

    The cell's text is split into vertical bands around each nested sub-table
    so surrounding text (above/below/between) is preserved, while each nested
    table becomes a marker. Text bands are pipe-escaped (_cell_text); markers
    are raw.
    """
    cx0, ct, cx1, cb = cell_bbox
    subs = sorted(sub_tables, key=lambda t: t.bbox[1])  # top-to-bottom
    parts: list[str] = []
    y = ct
    for sub in subs:
        st, sb = sub.bbox[1], sub.bbox[3]
        band = _crop_text(page, cx0, y, cx1, st)
        if band:
            parts.append(_cell_text(band))
        nt = serialize_nt(sub.extract())
        if nt:
            parts.append(nt)
        y = max(y, sb)
    tail = _crop_text(page, cx0, y, cx1, cb)
    if tail:
        parts.append(_cell_text(tail))
    return " ".join(p for p in parts if p)


def resolve_nested(page, tables):
    """Detect tables nested inside other tables' cells.

    Returns (suppressed, overrides):
      suppressed: set[int] — table indices contained in another table's cell
                  (excluded from standalone rendering).
      overrides:  dict[int, list[list[str]]] — for each top-level table that has
                  nested children, the rebuilt rows with each child-holding cell
                  replaced by prefix + [[NT:...]] + suffix.

    Only one nesting level becomes a marker: deeper tables are still suppressed
    and their content rides along as flattened text in the parent's extract().
    """
    n = len(tables)
    # sub_idx -> (parent_idx, row_idx, col_idx, cell_area) for the smallest containing cell
    contained: dict[int, tuple[int, int, int, float]] = {}
    for si in range(n):
        sub_bbox = tables[si].bbox
        best = None
        for pi in range(n):
            if pi == si:
                continue
            parent_bbox = tables[pi].bbox
            # a genuine parent is a strictly larger, distinct region; skip near-duplicate
            # regions (handled by merge dedup, not nesting) to avoid mutual suppression
            if bbox_near_equal(parent_bbox, sub_bbox) or bbox_area(parent_bbox) <= bbox_area(sub_bbox):
                continue
            for ri, row in enumerate(tables[pi].rows):
                for ci, cell in enumerate(row.cells):
                    if cell is None:
                        continue
                    if bbox_in_cell(sub_bbox, cell):
                        area = (cell[2] - cell[0]) * (cell[3] - cell[1])
                        if best is None or area < best[3]:
                            best = (pi, ri, ci, area)
        if best is not None:
            contained[si] = best

    suppressed = set(contained.keys())

    # group children by (parent, cell), only where the parent is itself top-level
    children: dict[int, dict[tuple[int, int], list[int]]] = {}
    for si, (pi, ri, ci, _area) in contained.items():
        if pi in suppressed:
            continue  # parent is itself nested → this sub flattens into parent's extract()
        children.setdefault(pi, {}).setdefault((ri, ci), []).append(si)

    overrides: dict[int, list[list[str]]] = {}
    for pi, cellmap in children.items():
        rows = [list(r) for r in tables[pi].extract()]
        for (ri, ci), sub_idxs in cellmap.items():
            if ri >= len(rows) or ci >= len(rows[ri]):
                continue
            cell_bbox = tables[pi].rows[ri].cells[ci]
            if cell_bbox is None:
                continue
            rows[ri][ci] = _rebuild_cell(page, cell_bbox, [tables[s] for s in sub_idxs])
        overrides[pi] = rows
    return suppressed, overrides


def _page_items_ordered(
    page: "pdfplumber.page.Page",
    tables: list,
    img_tokens: list[tuple[float, str]],
) -> list[tuple[float, str]]:
    """Return (top_y, content_str) pairs sorted by y position.

    content_str is either a GFM table, plain text, or an [[RHWP_IMAGE:N]] token.
    """
    segments: list[tuple[float, float, str]] = []  # (top_y, bottom_y, content)

    # Tables (resolving nested tables into [[NT:...]] markers first)
    suppressed, overrides = resolve_nested(page, tables)
    for ti, table in enumerate(tables):
        if ti in suppressed:
            continue
        rows = overrides[ti] if ti in overrides else table.extract()
        if rows:
            md = table_to_md(rows)
            if md:
                segments.append((table.bbox[1], table.bbox[3], md))

    # Image tokens at their y-position (bottom_y = top_y + 1 as placeholder)
    for top_y, token in img_tokens:
        segments.append((top_y, top_y + 1, token))

    # Text regions: gaps not covered by tables or images
    obstacle_bboxes = sorted(
        [(s[0], s[1]) for s in segments],
        key=lambda b: b[0],
    )
    page_h = page.height
    prev_bottom = 0.0
    for obs_top, obs_bottom in obstacle_bboxes:
        if obs_top > prev_bottom + 2:
            crop = page.crop((0, prev_bottom, page.width, obs_top))
            text = _strip_page_numbers(
                crop.extract_text(x_tolerance=3, y_tolerance=3) or ""
            ).strip()
            if text:
                segments.append((prev_bottom, obs_top, text))
        prev_bottom = max(prev_bottom, obs_bottom)
    if prev_bottom < page_h - 2:
        crop = page.crop((0, prev_bottom, page.width, page_h))
        text = _strip_page_numbers(
            crop.extract_text(x_tolerance=3, y_tolerance=3) or ""
        ).strip()
        if text:
            segments.append((prev_bottom, page_h, text))

    segments.sort(key=lambda s: s[0])
    return [(s[0], s[2]) for s in segments]


def _ocr_one(png: bytes, page_idx: int, data: bytes, llm: "LlmConfig | None") -> str:
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


def _ocr_pages(
    scanned: "list[tuple[int, bytes]]",
    data: bytes,
    llm: "LlmConfig | None",
    max_workers: int | None,
    ocr_fn: "Callable[[bytes, int], str] | None" = None,
) -> dict[int, str]:
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
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                results[idx] = fut.result()
            except Exception:
                results[idx] = ""
    return results


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

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        n_pages = len(pdf.pages)
        page_parts: list[list[str]] = [[] for _ in range(n_pages)]
        scanned: list[tuple[int, bytes]] = []
        pdf_reader = None

        for page_idx, page in enumerate(pdf.pages):

            # ── Scanned page: render now (main thread; fitz isn't thread-safe) ──
            if is_scanned_page(page):
                if llm is None:
                    scanned.append((page_idx, b""))
                else:
                    try:
                        png = render_bbox_to_png(data, page_idx, (0, 0, page.width, page.height))
                        scanned.append((page_idx, png))
                    except Exception as exc:
                        import sys
                        sys.stderr.write(f"  scanned render failed (page {page_idx}): {exc}\n")
                continue

            # ── Normal page: extract embedded images ──────────────────────────
            if page.images:
                try:
                    if pdf_reader is None:
                        from pypdf import PdfReader
                        pdf_reader = PdfReader(io.BytesIO(data))
                    img_items = extract_page_images(
                        data,
                        page_idx,
                        page,
                        start_idx=img_counter,
                        reader=pdf_reader,
                    )
                except ImportError:
                    img_items = []
            else:
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
