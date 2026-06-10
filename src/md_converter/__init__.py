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

from ._common import ImageItem
from .hwp import parse_hwp5 as _hwp5_parse, parse_hwpx as _hwpx_parse
from .llm import LlmConfig, drawing_to_mermaid, restructure_nested_tables
from .pdf import parse as _pdf_parse
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
        llm:    LlmConfig (required) — used for nested-table restructuring
                and drawing → Mermaid conversion.
        images: Where to put extracted images.
                S3Config    — upload to S3/MinIO, embed as s3:// URL.
                LocalImages — write to a local directory, embed as a file path.
                None (default) — drop images.
    """

    def __init__(
        self,
        *,
        llm: LlmConfig,
        images: S3Config | LocalImages | None = None,
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
        elif s == ".pdf":
            md, image_items = _pdf_parse(data)
        else:
            raise ValueError(f"Unsupported format: {ext!r} (expected '.hwp', '.hwpx', or '.pdf')")

        md = self._process_images(md, image_items)
        md = self._process_drawings(md)
        md = restructure_nested_tables(md, self._llm)

        return md

    # ── drawing handling ──────────────────────────────────────────────────────

    def _process_drawings(self, md: str) -> str:
        """Convert ```hwp-drawing blocks.

        Single-label drawing (section header / standalone box): emit as plain text.
        Multi-label drawing (potential flowchart): attempt Mermaid via LLM, fall back to plain text.
        """
        pattern = re.compile(r"```hwp-drawing\n(.*?)```", re.DOTALL)
        if not pattern.search(md):
            return md

        def _replace(m: re.Match) -> str:
            drawing_text = m.group(1).strip()
            labels = [l for l in drawing_text.splitlines() if l.strip()]
            if len(labels) <= 1:
                # Single label = decorative text box / section banner → plain text
                return drawing_text
            # Multiple labels = possible diagram → try Mermaid
            mermaid = drawing_to_mermaid(drawing_text, self._llm)
            if mermaid:
                sys.stderr.write(f"  drawing → mermaid ({len(labels)} labels)\n")
                return f"```mermaid\n{mermaid}\n```"
            return drawing_text

        return pattern.sub(_replace, md)

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
