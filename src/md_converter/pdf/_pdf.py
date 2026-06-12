"""PDF → Markdown parser using pdfplumber (MIT)."""
from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

from .._common import ImageItem
from .diagram_utils import detect_diagram_bboxes, render_bbox_to_png
from ._image_utils import extract_page_images, image_token
from ._ocr import is_scanned_page, ocr_page
from ._table_utils import (
    bbox_in_cell,
    merge_overflow_tables,
    serialize_nt,
    table_to_md,
    _cell_text,
)

if TYPE_CHECKING:
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


def parse(data: bytes, llm: "LlmConfig | None" = None) -> tuple[str, list[ImageItem]]:
    """Parse a PDF and return (markdown_string, image_items).

    Requires ``pdfplumber`` and ``pypdf`` (``pip install 'md-converter[pdf]'``).

    - Text PDFs (generated from HWP/HWPX): text + tables extracted; embedded
      non-trivial images returned as ImageItem list with [[RHWP_IMAGE:N]] tokens.
    - Scanned PDFs (no text layer): OCR via pytesseract when available, otherwise
      the page is skipped with an empty string.

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
        for page_idx, page in enumerate(pdf.pages):

            # ── Scanned page: no text layer, full-page image ──────────────────
            if is_scanned_page(page):
                ocr_text = ""
                if llm is not None:
                    try:
                        from ..llm import vision_to_text
                        png = render_bbox_to_png(data, page_idx, (0, 0, page.width, page.height))
                        ocr_text = vision_to_text(png, llm)
                        if ocr_text:
                            import sys
                            sys.stderr.write(f"  VLM OCR page {page_idx}: {len(ocr_text)} chars\n")
                    except Exception as exc:
                        import sys
                        sys.stderr.write(f"  VLM OCR failed (page {page_idx}): {exc}\n")
                if not ocr_text:
                    ocr_text = ocr_page(data, page_idx)
                if ocr_text.strip():
                    parts.append(ocr_text.strip())
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
                    parts.append(chunk.strip())

    md = "\n\n".join(parts)
    md = merge_overflow_tables(md)
    return md.strip(), all_images
