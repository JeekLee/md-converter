"""Extract embedded images from a PDF page using pypdf."""
from __future__ import annotations

import io

from .._common import IMAGE_TOKEN_FMT, ImageItem

# Images smaller than this area (in PDF pts²) are treated as decorative (icons, bullets).
_MIN_AREA_PT2 = 2500  # ~50×50 pt


def pil_to_item(pil_img: object, idx: int) -> ImageItem:
    """Convert a PIL image to an ImageItem, normalising to JPEG or PNG."""
    fmt = (getattr(pil_img, "format", None) or "PNG").upper()
    if fmt == "JPEG":
        ext, mime, save_fmt, mode = "jpg", "image/jpeg", "JPEG", "RGB"
    else:
        ext, mime, save_fmt, mode = "png", "image/png", "PNG", "RGBA"

    if getattr(pil_img, "mode", None) != mode:
        pil_img = pil_img.convert(mode)

    buf = io.BytesIO()
    pil_img.save(buf, format=save_fmt)
    return ImageItem(idx=idx, data=buf.getvalue(), mime=mime, ext=ext)


def image_token(idx: int) -> str:
    return IMAGE_TOKEN_FMT.format(idx=idx)


def extract_page_images(
    pdf_data: bytes,
    page_idx: int,
    plumber_page: object,
    start_idx: int = 1,
) -> list[tuple[float, ImageItem]]:
    """Extract non-trivial content images from a PDF page.

    Returns a list of (top_y_pt, ImageItem) sorted by top_y.
    Uses pypdf for image bytes, pdfplumber for position/size info.
    Skips full-page images (scanned pages) and tiny decorative images.

    Requires pypdf (``pip install 'md-converter[pdf]'``).
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError(
            "pypdf is required for PDF image extraction. "
            "Install with: pip install 'md-converter[pdf]'"
        ) from exc

    reader = PdfReader(io.BytesIO(pdf_data))
    if page_idx >= len(reader.pages):
        return []

    # Build name → PIL image map from pypdf.
    # Normalise: strip leading "/" and drop extension suffix (pypdf appends ".jpg" etc.)
    pypdf_by_name: dict[str, object] = {}
    for img_file in reader.pages[page_idx].images:
        if img_file.image is None:
            continue
        raw = img_file.name.lstrip("/")
        # Drop extension if present ("Im1.jpg" → "Im1")
        stem = raw.rsplit(".", 1)[0] if "." in raw else raw
        pypdf_by_name[stem] = img_file.image
        pypdf_by_name[raw] = img_file.image  # also keep full name as fallback

    page_w: float = plumber_page.width
    page_h: float = plumber_page.height

    results: list[tuple[float, ImageItem]] = []
    idx = start_idx
    for img_info in plumber_page.images:
        # pt dimensions
        img_w = float(img_info.get("x1", 0) - img_info.get("x0", 0))
        img_h = float(img_info.get("y1", 0) - img_info.get("y0", 0))

        # Skip: full-page scan placeholder or tiny decoration
        if img_w / page_w > 0.8 and img_h / page_h > 0.8:
            continue
        if img_w * img_h < _MIN_AREA_PT2:
            continue

        name = img_info.get("name", "").lstrip("/")
        pil_img = pypdf_by_name.get(name)
        if pil_img is None:
            continue

        item = pil_to_item(pil_img, idx)
        # pdfplumber 'top' is in top-left coordinate system
        top_y = float(img_info.get("top", 0))
        results.append((top_y, item))
        idx += 1

    results.sort(key=lambda t: t[0])
    return results
