"""Shared types for HWP5 and HWPX parsers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ImageItem:
    idx: int    # 1-based — matches [[RHWP_IMAGE:{idx}]] token
    data: bytes
    mime: str
    ext: str
