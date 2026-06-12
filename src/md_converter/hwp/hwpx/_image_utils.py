"""HWPX image extraction from BinData ZIP entries."""
from __future__ import annotations

import zipfile
from xml.etree import ElementTree as ET

from .._common import ImageItem, _bmp_to_png, _detect_mime, _mime_to_ext
from ._xml import _q

_OPF = "http://www.idpf.org/2007/opf/"


def load_bin_data_map(z: zipfile.ZipFile) -> dict[str, str]:
    """Contents/content.hpf → {id_string: href} for BinData items."""
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


def extract_image(
    pic: ET.Element,
    z: zipfile.ZipFile,
    bin_data_map: dict[str, str],
    images: list[ImageItem],
) -> str | None:
    """Extract image from hp:pic element. Returns [[RHWP_IMAGE:N]] token or None."""
    img_elem = pic.find(f".//{_q('img')}") or pic.find(f".//{_q('image')}")
    if img_elem is None:
        return None
    ref = img_elem.get("binaryItemIDRef", "")
    href = bin_data_map.get(ref, "")
    if not href or href not in z.namelist():
        return None
    raw = z.read(href)
    mime = _detect_mime(raw)
    if mime == "image/bmp":
        converted = _bmp_to_png(raw)
        if converted:
            raw, mime = converted, "image/png"
    ext = _mime_to_ext(mime)
    idx = len(images) + 1
    images.append(ImageItem(idx=idx, data=raw, mime=mime, ext=ext))
    return f"[[RHWP_IMAGE:{idx}]]"
