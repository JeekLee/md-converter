"""HWPX → Markdown converter.

HWPX is a ZIP archive. Body text lives in Contents/section*.xml.
This module mirrors rhwp's extract_document_markdown_with_images_native:
document-level paragraph traversal, not per-page render trees, so
cross-page tables appear exactly once in the output.

No external dependencies — stdlib only.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_OPF = "http://www.idpf.org/2007/opf/"


def _q(local: str) -> str:
    return f"{{{_HP}}}{local}"


# ── image item ─────────────────────────────────────────────────────────────

@dataclass
class ImageItem:
    idx: int        # 1-based — matches [[RHWP_IMAGE:{idx}]] token
    data: bytes
    mime: str       # "image/png", "image/jpeg", …
    ext: str        # "png", "jpg", …


# ── ZIP helpers ────────────────────────────────────────────────────────────

def _section_names(z: zipfile.ZipFile) -> list[str]:
    """Contents/section*.xml paths sorted by numeric index."""
    names = [n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)]
    return sorted(names, key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[-1]).group()))


def _load_bin_data_map(z: zipfile.ZipFile) -> dict[str, str]:
    """Parse Contents/content.hpf → {id_string: href} for BinData items.

    e.g. {"image1": "BinData/image1.png", "image2": "BinData/image2.jpg"}
    """
    if "Contents/content.hpf" not in z.namelist():
        return {}
    with z.open("Contents/content.hpf") as f:
        root = ET.parse(f).getroot()
    result: dict[str, str] = {}
    for item in root.iter(f"{{{_OPF}}}item"):
        href = item.get("href", "")
        item_id = item.get("id", "")
        if href.startswith("BinData/") and item_id:
            result[item_id] = href
    return result


# ── MIME detection + BMP→PNG ───────────────────────────────────────────────

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
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/bmp": "bmp",
    }.get(mime, "bin")


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


# ── text helpers ───────────────────────────────────────────────────────────

def _escape_cell(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("|", "\\|")).strip()


def _run_text(run: ET.Element) -> str:
    """Text from a single hp:run, filtering HWP control chars (≤ U+001F)."""
    parts = []
    for t in run.findall(_q("t")):
        if t.text:
            parts.append("".join(c for c in t.text if c > ""))
    return "".join(parts)


def _para_text(p: ET.Element) -> str:
    """Plain text from hp:p, skipping runs that carry table or picture controls."""
    parts = []
    for run in p.findall(_q("run")):
        if run.find(_q("tbl")) is not None:
            continue
        if run.find(_q("pic")) is not None:
            continue
        parts.append(_run_text(run))
    return "".join(parts)


# ── table helpers ──────────────────────────────────────────────────────────

def _cell_plain_text(tc: ET.Element) -> str:
    """Flat text from hp:tc — used inside [[NT:...]] inner cells."""
    sub = tc.find(_q("subList"))
    if sub is None:
        return ""
    parts = []
    for p in sub.findall(_q("p")):
        t = _para_text(p).strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def _cell_text(tc: ET.Element) -> str:
    """Text from hp:tc, expanding nested tables as [[NT:r0c0|c1;r1c0|c1]].

    Paragraphs (including NT markers) are joined with ' <br> ' to preserve
    multi-paragraph cell content in GFM tables.
    """
    sub = tc.find(_q("subList"))
    if sub is None:
        return ""
    parts = []
    for p in sub.findall(_q("p")):
        tbl = p.find(f".//{_q('tbl')}")
        if tbl is not None:
            rows = []
            for tr in tbl.findall(_q("tr")):
                row = "|".join(_cell_plain_text(tc2) for tc2 in tr.findall(_q("tc")))
                rows.append(row)
            parts.append("[[NT:" + ";".join(rows) + "]]")
        else:
            t = _para_text(p).strip()
            if t:
                parts.append(t)
    return " <br> ".join(parts)


def _escape_cell_for_table(s: str) -> str:
    """Escape | for GFM, but leave [[NT:...]] markers intact (LLM handles escaping later)."""
    if "[[NT:" in s:
        return s
    return _escape_cell(s)


def _table_to_md(tbl: ET.Element) -> str:
    """Convert hp:tbl to a GFM table string."""
    rows: list[list[str]] = []
    for tr in tbl.findall(_q("tr")):
        rows.append([_escape_cell_for_table(_cell_text(tc)) for tc in tr.findall(_q("tc"))])
    if not rows:
        return ""
    col_count = max(len(r) for r in rows)
    lines: list[str] = []
    for i, row in enumerate(rows):
        padded = row + [""] * (col_count - len(row))
        lines.append("| " + " | ".join(padded) + " |")
        if i == 0:
            lines.append("|" + "|".join(" --- " for _ in padded) + "|")
    return "\n".join(lines)


# ── main entry ─────────────────────────────────────────────────────────────

def parse(data: bytes) -> tuple[str, list[ImageItem]]:
    """Convert HWPX bytes to (markdown_with_placeholders, image_list).

    Image placeholders in markdown: [[RHWP_IMAGE:{idx}]]
    Tables → GFM. Nested tables → [[NT:...]] (for LLM restructuring).
    """
    parts: list[str] = []
    images: list[ImageItem] = []

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        bin_data_map = _load_bin_data_map(z)

        for name in _section_names(z):
            with z.open(name) as f:
                root = ET.parse(f).getroot()

            for p in root.findall(_q("p")):
                # Table control
                tbl = p.find(f".//{_q('tbl')}")
                if tbl is not None:
                    md = _table_to_md(tbl)
                    if md:
                        parts.append(md)
                    continue

                # Picture control
                pic = p.find(f".//{_q('pic')}")
                if pic is not None:
                    img_elem = pic.find(f".//{_q('img')}") or pic.find(f".//{_q('image')}")
                    if img_elem is not None:
                        ref = img_elem.get("binaryItemIDRef", "")
                        href = bin_data_map.get(ref, "")
                        if href and href in z.namelist():
                            raw = z.read(href)
                            mime = _detect_mime(raw)
                            # BMP → PNG
                            if mime == "image/bmp":
                                converted = _bmp_to_png(raw)
                                if converted:
                                    raw, mime = converted, "image/png"
                            ext = _mime_to_ext(mime)
                            idx = len(images) + 1
                            images.append(ImageItem(idx=idx, data=raw, mime=mime, ext=ext))
                            parts.append(f"[[RHWP_IMAGE:{idx}]]")
                    continue

                # Plain text paragraph
                text = _para_text(p).strip()
                if text:
                    parts.append(text)

    return "\n\n".join(parts), images


def convert(data: bytes) -> str:
    """Convert HWPX bytes to Markdown (text + tables only, images dropped).

    For full pipeline with image upload and LLM table restructuring,
    use md_converter.convert() instead.
    """
    md, _ = parse(data)
    # Drop any image placeholders when called directly
    md = re.sub(r"\[\[RHWP_IMAGE:\d+\]\]\n?\n?", "", md)
    return md.strip()
