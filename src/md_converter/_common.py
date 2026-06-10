"""Shared types and constants across all format parsers."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Image placeholder token embedded in the intermediate markdown.
# Consumed by MdConverter._process_images() for all source formats.
IMAGE_TOKEN_FMT = "[[RHWP_IMAGE:{idx}]]"
IMAGE_TOKEN_RE = re.compile(r"\[\[RHWP_IMAGE:(\d+)\]\]\n?\n?")


@dataclass
class ImageItem:
    idx: int    # 1-based — matches IMAGE_TOKEN_FMT
    data: bytes
    mime: str
    ext: str
