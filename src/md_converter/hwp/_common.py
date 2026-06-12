"""Shared utilities for HWP5 and HWPX parsers."""
from __future__ import annotations

import io
import re

from .._common import ImageItem

__all__ = ["ImageItem"]


def _detect_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] in (b"GIF8", b"GIF9"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    return "application/octet-stream"


def _mime_to_ext(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg",
            "image/gif": "gif", "image/bmp": "bmp"}.get(mime, "bin")


def _bmp_to_png(data: bytes) -> bytes | None:
    """Convert BMP bytes to PNG. Returns None if Pillow is not installed."""
    try:
        from PIL import Image  # type: ignore[import]
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _escape_cell(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("|", "\\|")).strip()
