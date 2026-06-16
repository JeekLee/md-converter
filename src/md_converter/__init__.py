"""md-converter — HWP / HWPX to Markdown.

    from md_converter import MdConverter, S3Config, LocalImages, LlmConfig

    converter = MdConverter(
        images=LocalImages("images"),   # or S3Config(...), or None
        llm=LlmConfig(...),             # optional — drawing/diagram → Mermaid, scanned-PDF OCR
    )
    md = converter.convert("document.hwp")
    md = converter.convert("document.hwpx")
    md = converter.convert(raw_bytes, suffix=".hwp")
"""
from __future__ import annotations

import re
import hashlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ._common import ImageItem
from .hwp import parse_hwp5 as _hwp5_parse, parse_hwpx as _hwpx_parse
from .llm import LlmConfig, drawing_to_mermaid, vision_to_mermaid
from .metadata import (
    ConversionResult,
    DocumentProfile,
    MarkdownMetrics,
    markdown_metrics,
    profile_for_suffix,
    quality_warnings,
)
from .nested_tables import extract_nested_tables
from .pdf import parse as _pdf_parse
from .pdf import profile_pdf
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
        llm:    Optional LlmConfig — drawing/diagram → Mermaid conversion
                and scanned-PDF OCR (vision). Nested tables do not use the LLM.
        images: Where to put extracted images.
                S3Config    — upload to S3/MinIO, embed as s3:// URL.
                LocalImages — write to a local directory, embed as a file path.
                None (default) — drop images.
        ocr_workers: max concurrent scanned-PDF OCR calls (default 4; <=1 = sequential).
    """

    def __init__(
        self,
        *,
        llm: LlmConfig | None = None,
        images: S3Config | LocalImages | None = None,
        ocr_workers: int = 4,
    ) -> None:
        self._images = images
        self._llm = llm
        self._ocr_workers = ocr_workers

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
        data, ext = self._read_source(source, suffix)
        return self._convert_data(data, ext)

    def convert_with_metadata(
        self,
        source: str | Path | bytes,
        suffix: str | None = None,
        *,
        raise_errors: bool = True,
    ) -> ConversionResult:
        data = b""
        ext = suffix or ""
        started = time.perf_counter()
        try:
            data, ext = self._read_source(source, suffix)
            profile = self.profile(data, ext)
            md = self._convert_data(data, ext)
            return ConversionResult(
                markdown=md,
                suffix=ext.lower(),
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                runtime_s=time.perf_counter() - started,
                metrics=markdown_metrics(md),
                quality_warnings=quality_warnings(md),
                profile=profile,
                llm_used=self._llm is not None and profile.needs_ocr,
            )
        except Exception as exc:
            if raise_errors:
                raise
            return ConversionResult(
                markdown="",
                suffix=ext.lower(),
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                runtime_s=time.perf_counter() - started,
                metrics=markdown_metrics(""),
                quality_warnings=[],
                profile=profile_for_suffix(ext),
                llm_used=False,
                error=str(exc),
            )

    def profile(
        self,
        source: str | Path | bytes,
        suffix: str | None = None,
    ) -> DocumentProfile:
        data, ext = self._read_source(source, suffix)
        s = ext.lower()
        if s == ".pdf":
            return profile_pdf(data)
        if s in {".hwp", ".hwpx"}:
            return profile_for_suffix(s)
        raise ValueError(f"Unsupported format: {ext!r} (expected '.hwp', '.hwpx', or '.pdf')")

    def _read_source(
        self,
        source: str | Path | bytes,
        suffix: str | None = None,
    ) -> tuple[bytes, str]:
        if isinstance(source, (str, Path)):
            path = Path(source)
            return path.read_bytes(), path.suffix
        if suffix is None:
            raise ValueError("suffix is required when source is bytes")
        return bytes(source), suffix

    def _convert_data(self, data: bytes, ext: str) -> str:
        s = ext.lower()
        if s == ".hwpx":
            md, image_items = _hwpx_parse(data)
        elif s == ".hwp":
            md, image_items = _hwp5_parse(data)
        elif s == ".pdf":
            md, image_items = _pdf_parse(data, llm=self._llm, max_ocr_workers=self._ocr_workers)
        else:
            raise ValueError(f"Unsupported format: {ext!r} (expected '.hwp', '.hwpx', or '.pdf')")

        md = self._process_diagram_images(md, image_items)
        md = self._process_images(md, image_items)
        md = self._process_drawings(md)
        md = extract_nested_tables(md)

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
            if len(labels) <= 1 or self._llm is None:
                # Single label = decorative text box / section banner → plain text
                return drawing_text
            # Multiple labels = possible diagram → try Mermaid
            mermaid = drawing_to_mermaid(drawing_text, self._llm)
            if mermaid:
                sys.stderr.write(f"  drawing → mermaid ({len(labels)} labels)\n")
                return f"```mermaid\n{mermaid}\n```"
            return drawing_text

        return pattern.sub(_replace, md)

    def _process_diagram_images(self, md: str, image_items: list[ImageItem]) -> str:
        """Try vision LLM on is_diagram=True images; replace token with mermaid block on success.

        Leaves token in place on failure so _process_images() handles it as a regular image.
        """
        if self._llm is None:
            return md
        for img in image_items:
            if not img.is_diagram:
                continue
            token = f"[[RHWP_IMAGE:{img.idx}]]"
            if token not in md:
                continue
            mermaid = vision_to_mermaid(img.data, self._llm)
            if mermaid:
                sys.stderr.write(f"  diagram image → mermaid (idx={img.idx})\n")
                md = md.replace(token, f"```mermaid\n{mermaid}\n```")
                img.is_diagram = False
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
    "ConversionResult",
    "DocumentProfile",
    "MarkdownMetrics",
]
