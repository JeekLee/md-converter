"""md-converter — HWP / HWPX to Markdown.

    from md_converter import MdConverter, S3Config, LocalImages, LlmConfig

    converter = MdConverter(
        images=LocalImages("images"),   # or S3Config(...), or None
        llm=LlmConfig(...),             # or None to skip nested-table restructuring
    )
    md = converter.convert("document.hwp")
    md = converter.convert("document.hwpx")
    md = converter.convert(raw_bytes, suffix=".hwp")
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .hwpx import ImageItem
from .hwpx import parse as _hwpx_parse
from .hwp5 import parse as _hwp5_parse
from .llm import LlmConfig, restructure_nested_tables
from .s3 import S3Config, put_object


@dataclass
class LocalImages:
    """Save extracted images to a local directory.

    Images are written to `{dir}/img{N:04d}.{ext}`.
    The markdown will embed them as `![image N]({dir}/img{N:04d}.{ext})`,
    so set `dir` relative to wherever you write the markdown file.
    """
    dir: str | Path


class MdConverter:
    """HWP / HWPX → Markdown converter.

    Args:
        images: Where to put extracted images.
                S3Config  — upload to S3/MinIO, embed as s3:// URL.
                LocalImages — write to a local directory, embed as a file path.
                None (default) — drop images.
        llm:    LlmConfig for LLM-based nested-table restructuring,
                or None (default) to skip.
    """

    def __init__(
        self,
        *,
        images: S3Config | LocalImages | None = None,
        llm: LlmConfig | None = None,
    ) -> None:
        self._images = images
        self._llm = llm

    def convert(
        self,
        source: str | Path | bytes,
        suffix: str | None = None,
    ) -> str:
        """Convert an HWP or HWPX document to Markdown.

        Args:
            source: File path (str or Path) or raw bytes.
            suffix: Required when source is bytes — ".hwp" or ".hwpx".

        Returns:
            Markdown string.
        """
        if isinstance(source, (str, Path)):
            path = Path(source)
            data = path.read_bytes()
            ext = path.suffix
        else:
            if suffix is None:
                raise ValueError("suffix is required when source is bytes")
            data = bytes(source)
            ext = suffix

        s = ext.lower()
        if s == ".hwpx":
            md, image_items = _hwpx_parse(data)
        elif s == ".hwp":
            md, image_items = _hwp5_parse(data)
        else:
            raise ValueError(f"Unsupported format: {ext!r} (expected '.hwp' or '.hwpx')")

        md = self._process_images(md, image_items)

        if self._llm is not None:
            md = restructure_nested_tables(md, self._llm)

        return md

    # ── image handling ────────────────────────────────────────────────────────

    def _process_images(self, md: str, image_items: list[ImageItem]) -> str:
        if not image_items:
            return md
        if self._images is None:
            return re.sub(r"\[\[RHWP_IMAGE:\d+\]\]\n?\n?", "", md).strip()
        if isinstance(self._images, S3Config):
            return self._upload_to_s3(md, image_items)
        if isinstance(self._images, LocalImages):
            return self._save_locally(md, image_items)
        return md

    def _upload_to_s3(self, md: str, image_items: list[ImageItem]) -> str:
        for img in image_items:
            token = f"[[RHWP_IMAGE:{img.idx}]]"
            if token not in md:
                continue
            filename = f"img{img.idx:04d}.{img.ext}"
            try:
                url = put_object(self._images, filename, img.data, img.mime)
                sys.stderr.write(f"  image uploaded: {url}\n")
                link = f"![image {img.idx}]({url})"
            except Exception as exc:
                sys.stderr.write(f"  S3 upload failed ({filename}): {exc}\n")
                link = ""
            md = md.replace(token, link)
        md = re.sub(r"\[\[RHWP_IMAGE:\d+\]\]\n?\n?", "", md)
        return md.strip()

    def _save_locally(self, md: str, image_items: list[ImageItem]) -> str:
        dest_dir = Path(self._images.dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for img in image_items:
            token = f"[[RHWP_IMAGE:{img.idx}]]"
            if token not in md:
                continue
            filename = f"img{img.idx:04d}.{img.ext}"
            dest = dest_dir / filename
            try:
                dest.write_bytes(img.data)
                sys.stderr.write(f"  image saved: {dest}\n")
                link = f"![image {img.idx}]({dest})"
            except Exception as exc:
                sys.stderr.write(f"  image save failed ({filename}): {exc}\n")
                link = ""
            md = md.replace(token, link)
        md = re.sub(r"\[\[RHWP_IMAGE:\d+\]\]\n?\n?", "", md)
        return md.strip()


__all__ = [
    "MdConverter",
    "LocalImages",
    "S3Config",
    "LlmConfig",
    "ImageItem",
]
