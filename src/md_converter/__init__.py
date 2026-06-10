"""md-converter — HWP / HWPX to Markdown.

Full pipeline (mirrors rhwp export-markdown):
  1. Parse document → markdown text + image list (with [[RHWP_IMAGE:N]] tokens)
  2. For each image: upload to S3/MinIO OR save locally → replace token with ![](url)
  3. Replace [[NT:...]] nested-table markers via LLM → natural prose

Public API
----------
convert(data, suffix, *, s3_config, llm_config, output_dir) -> str
hwpx_to_md(data)  → str   # text + tables only (images dropped)
hwp5_to_md(data)  → str   # text + tables only (images dropped)

Config dataclasses
------------------
from md_converter import S3Config, LlmConfig
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from .hwpx import ImageItem
from .hwpx import convert as _hwpx_text_only
from .hwpx import parse as _hwpx_parse
from .hwp5 import convert as _hwp5_text_only
from .hwp5 import parse as _hwp5_parse
from .llm import LlmConfig, restructure_nested_tables
from .s3 import S3Config, put_object


# ── simple text-only helpers ───────────────────────────────────────────────

def hwpx_to_md(data: bytes) -> str:
    """HWPX → Markdown (text + tables; images dropped)."""
    return _hwpx_text_only(data)


def hwp5_to_md(data: bytes) -> str:
    """HWP5 → Markdown (text + tables; images dropped).

    Requires: pip install "md-converter[hwp5]"
    """
    return _hwp5_text_only(data)


# ── full pipeline ──────────────────────────────────────────────────────────

def convert(
    data: bytes,
    suffix: str,
    *,
    s3_config: S3Config | None = None,
    llm_config: LlmConfig | None = None,
    output_dir: Path | str | None = None,
) -> str:
    """Convert HWP or HWPX bytes to Markdown.

    suffix: ".hwp" or ".hwpx" (leading dot, case-insensitive).

    Image handling (mutually exclusive, S3 takes priority):
    - s3_config set  → upload to MinIO/S3, embed s3://bucket/key URL
    - output_dir set → save image files locally, embed relative path
    - neither        → image tokens are dropped from output

    LLM handling:
    - llm_config set → [[NT:...]] nested-table markers are restructured via LLM
    - not set        → [[NT:...]] markers are kept as-is (flat fallback text)
    """
    s = suffix.lower()
    if s == ".hwpx":
        md, images = _hwpx_parse(data)
    elif s == ".hwp":
        md, images = _hwp5_parse(data)
    else:
        raise ValueError(f"Unsupported format: {suffix!r} (expected '.hwp' or '.hwpx')")

    md = _process_images(md, images, s3_config, output_dir)

    if llm_config is not None:
        md = restructure_nested_tables(md, llm_config)

    return md


def _process_images(
    md: str,
    images: list[ImageItem],
    s3_config: S3Config | None,
    output_dir: Path | str | None,
) -> str:
    if not images:
        return md

    out_path = Path(output_dir) if output_dir else None
    if out_path:
        out_path.mkdir(parents=True, exist_ok=True)

    for img in images:
        token = f"[[RHWP_IMAGE:{img.idx}]]"
        if token not in md:
            continue

        filename = f"img{img.idx:04d}.{img.ext}"

        if s3_config is not None:
            try:
                url = put_object(s3_config, filename, img.data, img.mime)
                sys.stderr.write(f"  image uploaded: {url}\n")
                link = f"![image {img.idx}]({url})"
            except Exception as exc:
                sys.stderr.write(f"  S3 upload failed ({filename}): {exc}\n")
                link = ""
        elif out_path is not None:
            dest = out_path / filename
            dest.write_bytes(img.data)
            link = f"![image {img.idx}]({filename})"
        else:
            link = ""

        md = md.replace(token, link)

    # Drop any remaining un-replaced tokens (no-op when link="" already done above)
    md = re.sub(r"\[\[RHWP_IMAGE:\d+\]\]\n?\n?", "", md)
    return md.strip()


__all__ = [
    "convert",
    "hwpx_to_md",
    "hwp5_to_md",
    "S3Config",
    "LlmConfig",
    "ImageItem",
]
