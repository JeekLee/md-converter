"""HWPX (ZIP+XML) parser and public API.

HWPX is a ZIP archive. Body text lives in Contents/section*.xml.
Document-level paragraph traversal (not per-page render trees), so
cross-page tables appear exactly once in the output.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

from .._common import ImageItem
from ..._diagram import graph_to_mermaid
from ._image_utils import extract_image, load_bin_data_map
from ._table_utils import table_to_md
from ._xml import _drawing_texts, _para_text, _q
from .diagram_utils import extract_diagram


def _section_names(z: zipfile.ZipFile) -> list[str]:
    """Contents/section*.xml paths sorted by numeric index."""
    names = [n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)]
    return sorted(names, key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[-1]).group()))


def parse(data: bytes) -> tuple[str, list[ImageItem]]:
    """Convert HWPX bytes to (markdown_with_placeholders, image_list).

    Image placeholders in markdown: [[RHWP_IMAGE:{idx}]]
    Tables → GFM. Nested tables → [[NT:...]] (for LLM restructuring).
    """
    parts: list[str] = []
    images: list[ImageItem] = []

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        bin_data_map = load_bin_data_map(z)

        for name in _section_names(z):
            with z.open(name) as f:
                root = ET.parse(f).getroot()

            for p in root.findall(_q("p")):
                # ── Table ─────────────────────────────────────────────────────
                tbl = p.find(f".//{_q('tbl')}")
                if tbl is not None:
                    md = table_to_md(tbl)
                    if md:
                        parts.append(md)
                    continue

                # ── Picture ───────────────────────────────────────────────────
                pic = p.find(f".//{_q('pic')}")
                if pic is not None:
                    token = extract_image(pic, z, bin_data_map, images)
                    if token:
                        parts.append(token)
                    continue

                # ── Drawing shapes ────────────────────────────────────────────
                diagram_graph = extract_diagram(p)
                if diagram_graph is not None:
                    mermaid = graph_to_mermaid(diagram_graph)
                    if mermaid:
                        parts.append(f"```mermaid\n{mermaid}\n```")
                    continue

                drawing_labels = _drawing_texts(p)
                if drawing_labels:
                    parts.append("```hwp-drawing\n" + "\n".join(drawing_labels) + "\n```")
                    continue

                # ── Plain text paragraph ──────────────────────────────────────
                text = _para_text(p).strip()
                if text:
                    parts.append(text)

    return "\n\n".join(parts), images


def convert(data: bytes) -> str:
    """Convert HWPX bytes to Markdown (text + tables only, images dropped).

    For full pipeline with image upload and LLM table restructuring,
    use md_converter.MdConverter instead.
    """
    md, _ = parse(data)
    md = re.sub(r"\[\[RHWP_IMAGE:\d+\]\]\n?\n?", "", md)
    return md.strip()
