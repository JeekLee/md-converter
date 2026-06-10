"""PDF → Markdown parser using pdfplumber (MIT)."""
from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

from .._common import ImageItem
from ._table_utils import merge_overflow_tables, table_to_md

if TYPE_CHECKING:
    import pdfplumber

# Matches standalone page number lines like "- 3 -" or "3"
_PAGE_NUM_RE = re.compile(r"(?m)^\s*(?:-\s*)?\d+\s*(?:-\s*)?$")


def _strip_page_numbers(text: str) -> str:
    return _PAGE_NUM_RE.sub("", text)


def _text_blocks(page: "pdfplumber.page.Page", table_bboxes: list[tuple]) -> list[str]:
    """Extract text from the page, skipping regions occupied by tables."""
    if not table_bboxes:
        raw = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
        return [raw] if raw.strip() else []

    blocks: list[str] = []
    page_height = page.height
    # Sort table bboxes by top y
    sorted_bboxes = sorted(table_bboxes, key=lambda b: b[1])

    prev_bottom = 0.0
    for bbox in sorted_bboxes:
        top = bbox[1]
        if top > prev_bottom + 2:
            region = page.crop((0, prev_bottom, page.width, top))
            text = region.extract_text(x_tolerance=3, y_tolerance=3) or ""
            if text.strip():
                blocks.append(text)
        prev_bottom = bbox[3]

    # Text after last table
    if prev_bottom < page_height - 2:
        region = page.crop((0, prev_bottom, page.width, page_height))
        text = region.extract_text(x_tolerance=3, y_tolerance=3) or ""
        if text.strip():
            blocks.append(text)

    return blocks


def parse(data: bytes) -> tuple[str, list[ImageItem]]:
    """Parse a PDF and return (markdown_string, image_items).

    Requires the 'pdfplumber' package (``pip install 'md-converter[pdf]'``).

    Tables are rendered as GFM, text paragraphs as plain text.
    Adjacent continuation / overflow / duplicate tables are merged.
    Images are not yet extracted (returns empty list).
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError(
            "pdfplumber is required for PDF conversion. "
            "Install with: pip install 'md-converter[pdf]'"
        ) from exc

    parts: list[str] = []

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            tables = page.find_tables()
            table_bboxes = [t.bbox for t in tables]

            # Interleave text and tables in y-order
            items: list[tuple[float, str]] = []  # (top_y, markdown_chunk)

            # Text blocks
            for text_block in _text_blocks(page, table_bboxes):
                cleaned = _strip_page_numbers(text_block).strip()
                if cleaned:
                    # Approximate y from first table or use 0 for text-only pages
                    items.append((0.0, cleaned))

            # Tables
            for table in tables:
                rows = table.extract()
                if not rows:
                    continue
                md_table = table_to_md(rows)
                if md_table:
                    items.append((table.bbox[1], md_table))

            # Sort by y position, text blocks use their bbox if available
            # Re-extract with position awareness
            items = _page_items_ordered(page, tables)
            for _, chunk in items:
                if chunk.strip():
                    parts.append(chunk.strip())

    md = "\n\n".join(parts)
    md = merge_overflow_tables(md)
    return md.strip(), []


def _page_items_ordered(
    page: "pdfplumber.page.Page",
    tables: list,
) -> list[tuple[float, str]]:
    """Return (y, text_or_table_md) pairs sorted by y position."""
    items: list[tuple[float, str]] = []
    table_bboxes = [t.bbox for t in tables]

    # Build a sorted list of (top_y, bottom_y, kind, payload)
    segments: list[tuple[float, float, str, object]] = []

    for table in tables:
        rows = table.extract()
        if rows:
            md = table_to_md(rows)
            if md:
                segments.append((table.bbox[1], table.bbox[3], "table", md))

    # Find text regions: gaps between tables (and before first / after last)
    sorted_bboxes = sorted(table_bboxes, key=lambda b: b[1])
    page_height = page.height

    regions: list[tuple[float, float]] = []
    prev_bottom = 0.0
    for bbox in sorted_bboxes:
        top = bbox[1]
        if top > prev_bottom + 2:
            regions.append((prev_bottom, top))
        prev_bottom = bbox[3]
    if prev_bottom < page_height - 2:
        regions.append((prev_bottom, page_height))

    for (region_top, region_bottom) in regions:
        region = page.crop((0, region_top, page.width, region_bottom))
        text = region.extract_text(x_tolerance=3, y_tolerance=3) or ""
        text = _strip_page_numbers(text).strip()
        if text:
            segments.append((region_top, region_bottom, "text", text))

    segments.sort(key=lambda s: s[0])
    return [(s[0], s[3]) for s in segments]  # type: ignore[return-value]
