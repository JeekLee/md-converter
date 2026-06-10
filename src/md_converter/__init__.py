"""md-converter — HWP / HWPX to Markdown.

Public API
----------
hwpx_to_md(data: bytes) -> str   # pure stdlib, no deps
hwp5_to_md(data: bytes) -> str   # requires: pip install md-converter[hwp5]
convert(data: bytes, suffix: str) -> str  # ".hwp" or ".hwpx"
"""

from .hwpx import convert as hwpx_to_md
from .hwp5 import convert as hwp5_to_md


def convert(data: bytes, suffix: str) -> str:
    """Convert HWP or HWPX bytes to Markdown.

    suffix: ".hwp" or ".hwpx" (leading dot, case-insensitive).
    """
    s = suffix.lower()
    if s == ".hwpx":
        return hwpx_to_md(data)
    if s == ".hwp":
        return hwp5_to_md(data)
    raise ValueError(f"Unsupported format: {suffix!r} (expected '.hwp' or '.hwpx')")


__all__ = ["hwpx_to_md", "hwp5_to_md", "convert"]
