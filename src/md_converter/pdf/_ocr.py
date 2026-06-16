"""OCR support for scanned PDF pages."""
from __future__ import annotations

import io


def is_scanned_page(plumber_page: object, min_text_chars: int = 30) -> bool:
    """Return True if the page has no usable text layer but has a full-page image."""
    text_chars = 0
    for char in getattr(plumber_page, "chars", []):
        if str(char.get("text", "")).strip():
            text_chars += 1
            if text_chars >= min_text_chars:
                return False

    pw, ph = plumber_page.width, plumber_page.height
    for img in plumber_page.images:
        img_w = float(img.get("x1", 0) - img.get("x0", 0))
        img_h = float(img.get("y1", 0) - img.get("y0", 0))
        if pw and ph and img_w / pw > 0.7 and img_h / ph > 0.7:
            return True
    return False


def ocr_page(pdf_data: bytes, page_idx: int, lang: str = "kor+eng") -> str:
    """OCR a scanned PDF page and return plain text.

    Strategy:
      1. Use pypdf to extract the embedded full-page image and OCR it directly
         (avoids rendering the PDF).
      2. Fall back to pdf2image (poppler) rendering if no embedded image found.

    Returns empty string if pytesseract is not installed.
    Requires: pytesseract + Tesseract binary (+ tesseract-ocr-kor for Korean).
    Optional: pypdf (fast path), pdf2image + poppler (fallback rendering).
    """
    try:
        import pytesseract  # noqa: F401 — check availability early
    except ImportError:
        return ""

    # Fast path: get the full-page scan image from pypdf
    text = _ocr_via_pypdf(pdf_data, page_idx, lang)
    if text:
        return text

    # Fallback: render the page via pdf2image (needs poppler)
    return _ocr_via_pdf2image(pdf_data, page_idx, lang)


def _ocr_via_pypdf(pdf_data: bytes, page_idx: int, lang: str) -> str:
    try:
        import pytesseract
        from pypdf import PdfReader
    except ImportError:
        return ""

    reader = PdfReader(io.BytesIO(pdf_data))
    if page_idx >= len(reader.pages):
        return ""

    page_imgs = [f for f in reader.pages[page_idx].images if f.image is not None]
    if not page_imgs:
        return ""

    largest = max(page_imgs, key=lambda f: f.image.width * f.image.height)
    try:
        return pytesseract.image_to_string(largest.image, lang=lang)
    except Exception:
        return ""


def _ocr_via_pdf2image(pdf_data: bytes, page_idx: int, lang: str) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError:
        return ""

    try:
        pil_pages = convert_from_bytes(
            pdf_data, first_page=page_idx + 1, last_page=page_idx + 1, dpi=200
        )
        if pil_pages:
            return pytesseract.image_to_string(pil_pages[0], lang=lang)
    except Exception:
        pass
    return ""
