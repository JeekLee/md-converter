"""HWPX low-level XML helpers: namespace, text extraction, drawing labels."""
from __future__ import annotations

from xml.etree import ElementTree as ET

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _q(local: str) -> str:
    return f"{{{_HP}}}{local}"


# Drawing shape element tags that are direct children of hp:p
_DRAWING_TAGS = frozenset(
    _q(t) for t in ("rect", "ellipse", "line", "arc", "polygon", "curve", "container")
)


def _run_text(run: ET.Element) -> str:
    """Text from a single hp:run, stripping HWP control chars (≤ U+001F)."""
    parts = []
    for t in run.findall(_q("t")):
        if t.text:
            parts.append("".join(c for c in t.text if c > "\x1f"))
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


def _drawing_texts(p: ET.Element) -> list[str]:
    """Text labels from drawing shapes (hp:rect, ellipse, …) in a paragraph.

    Drawing shapes carry text via hp:drawText > hp:subList > hp:p.
    Returns an empty list when the paragraph has no drawing shapes.
    """
    texts: list[str] = []
    for child in p:
        if child.tag not in _DRAWING_TAGS:
            continue
        for draw_text in child.findall(f".//{_q('drawText')}"):
            for sub in draw_text.findall(_q("subList")):
                for para in sub.findall(_q("p")):
                    t = _para_text(para).strip()
                    if t:
                        texts.append(t)
    return texts
