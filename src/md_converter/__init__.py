"""md-converter — HWP / HWPX to Markdown.

Full pipeline (mirrors rhwp export-markdown):
  1. Parse document → markdown text + image list (with [[RHWP_IMAGE:N]] tokens)
  2. For each image: upload to S3/MinIO → replace token with ![](s3://...)
  3. Replace [[NT:...]] nested-table markers via LLM → natural prose

Public API
----------
convert(data, suffix, s3_config, llm_config) -> str

Config dataclasses
------------------
from md_converter import S3Config, LlmConfig
"""
from __future__ import annotations

import re
import sys

from .hwpx import ImageItem
from .hwpx import parse as _hwpx_parse
from .hwp5 import parse as _hwp5_parse
from .llm import LlmConfig, restructure_nested_tables
from .s3 import S3Config, put_object


def convert(
    data: bytes,
    suffix: str,
    s3_config: S3Config,
    llm_config: LlmConfig,
) -> str:
    """Convert HWP or HWPX bytes to Markdown.

    suffix: ".hwp" or ".hwpx" (leading dot, case-insensitive).

    Images are uploaded to S3/MinIO and embedded as ![image N](s3://bucket/key).
    [[NT:...]] nested-table markers are restructured via LLM into natural prose.
    """
    s = suffix.lower()
    if s == ".hwpx":
        md, images = _hwpx_parse(data)
    elif s == ".hwp":
        md, images = _hwp5_parse(data)
    else:
        raise ValueError(f"Unsupported format: {suffix!r} (expected '.hwp' or '.hwpx')")

    md = _upload_images(md, images, s3_config)
    md = restructure_nested_tables(md, llm_config)
    return md


def _upload_images(md: str, images: list[ImageItem], s3_config: S3Config) -> str:
    for img in images:
        token = f"[[RHWP_IMAGE:{img.idx}]]"
        if token not in md:
            continue
        filename = f"img{img.idx:04d}.{img.ext}"
        try:
            url = put_object(s3_config, filename, img.data, img.mime)
            sys.stderr.write(f"  image uploaded: {url}\n")
            link = f"![image {img.idx}]({url})"
        except Exception as exc:
            sys.stderr.write(f"  S3 upload failed ({filename}): {exc}\n")
            link = ""
        md = md.replace(token, link)
    md = re.sub(r"\[\[RHWP_IMAGE:\d+\]\]\n?\n?", "", md)
    return md.strip()


__all__ = [
    "convert",
    "S3Config",
    "LlmConfig",
    "ImageItem",
]
