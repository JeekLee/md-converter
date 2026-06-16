"""Fast PDF profiling for crawler scheduling."""
from __future__ import annotations

import io

from ..metadata import DocumentProfile
from ._ocr import is_scanned_page


def profile_pdf(data: bytes) -> DocumentProfile:
    """Return page/text/scanned counts without converting the PDF to Markdown."""
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError(
            "pdfplumber is required for PDF profiling. "
            "Install with: pip install 'md-converter[pdf]'"
        ) from exc

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        page_count = len(pdf.pages)
        scanned_page_count = 0
        for page in pdf.pages:
            if is_scanned_page(page):
                scanned_page_count += 1

    text_page_count = page_count - scanned_page_count
    return DocumentProfile(
        kind="pdf",
        page_count=page_count,
        text_page_count=text_page_count,
        scanned_page_count=scanned_page_count,
        needs_ocr=scanned_page_count > 0,
    )
