"""Tests for parallel scanned-page OCR (_ocr_pages)."""
from __future__ import annotations

import time

from md_converter.pdf._pdf import _ocr_pages
from md_converter.pdf._ocr import is_scanned_page


class _FakePage:
    width = 100
    height = 100

    def __init__(self, chars, images):
        self.chars = chars
        self.images = images

    def extract_text(self, **kwargs):
        raise AssertionError("is_scanned_page should not build a text map")


def test_is_scanned_page_uses_chars_without_extract_text():
    chars = [{"text": "가"} for _ in range(30)]
    page = _FakePage(chars=chars, images=[{"x0": 0, "x1": 100, "y0": 0, "y1": 100}])
    assert is_scanned_page(page) is False


def test_is_scanned_page_detects_full_page_image_without_text():
    page = _FakePage(chars=[], images=[{"x0": 0, "x1": 90, "y0": 0, "y1": 90}])
    assert is_scanned_page(page) is True


def test_ocr_pages_deterministic_parallel_vs_sequential():
    scanned = [(0, b"p0"), (1, b"p1"), (2, b"p2")]
    fake = lambda png, idx: f"TEXT{idx}"
    seq = _ocr_pages(scanned, b"", None, 1, ocr_fn=fake)
    par = _ocr_pages(scanned, b"", None, 4, ocr_fn=fake)
    assert seq == par == {0: "TEXT0", 1: "TEXT1", 2: "TEXT2"}


def test_ocr_pages_order_independent():
    # later pages finish first; mapping must stay keyed by page_idx
    scanned = [(0, b""), (1, b""), (2, b""), (3, b"")]

    def fake(png, idx):
        time.sleep(0.02 * (4 - idx))
        return f"T{idx}"

    res = _ocr_pages(scanned, b"", None, 4, ocr_fn=fake)
    assert res == {0: "T0", 1: "T1", 2: "T2", 3: "T3"}


def test_ocr_pages_failure_isolated():
    def fake(png, idx):
        if idx == 1:
            raise RuntimeError("boom")
        return f"T{idx}"

    res = _ocr_pages([(0, b""), (1, b""), (2, b"")], b"", None, 4, ocr_fn=fake)
    assert res == {0: "T0", 1: "", 2: "T2"}


def test_ocr_pages_failure_isolated_sequential():
    def fake(png, idx):
        if idx == 0:
            raise RuntimeError("boom")
        return "ok"

    res = _ocr_pages([(0, b""), (1, b"")], b"", None, 1, ocr_fn=fake)
    assert res == {0: "", 1: "ok"}


def test_ocr_pages_empty():
    assert _ocr_pages([], b"", None, 4) == {}


def test_ocr_pages_single_uses_sequential():
    res = _ocr_pages([(0, b"x")], b"", None, 4, ocr_fn=lambda png, idx: "ONE")
    assert res == {0: "ONE"}


def _make_scanned_pdf(n_pages: int) -> bytes:
    """A PDF whose pages are full-page images with no text layer (scanned-like)."""
    import pytest
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for _ in range(n_pages):
        page = doc.new_page(width=300, height=400)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400))
        pix.clear_with(240)
        page.insert_image(fitz.Rect(0, 0, 300, 400), pixmap=pix)
    return doc.tobytes()


def test_parse_scanned_pdf_order_and_parallel_equiv(monkeypatch):
    import io
    import pytest
    pytest.importorskip("fitz")
    import pdfplumber
    from md_converter.pdf import parse
    import md_converter.pdf._pdf as pdfmod
    from md_converter.pdf._ocr import is_scanned_page

    data = _make_scanned_pdf(2)

    # only meaningful if pdfplumber agrees both pages are scanned
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if not all(is_scanned_page(p) for p in pdf.pages):
            pytest.skip("fitz-built PDF not detected as scanned by pdfplumber")

    # mock the actual OCR; return page-specific text so we can check ordering
    monkeypatch.setattr(pdfmod, "_ocr_one", lambda png, idx, data, llm: f"OCRPAGE{idx}")

    md4, _ = parse(data, llm=object(), max_ocr_workers=4)
    md1, _ = parse(data, llm=object(), max_ocr_workers=1)

    assert "OCRPAGE0" in md4 and "OCRPAGE1" in md4
    assert md4.index("OCRPAGE0") < md4.index("OCRPAGE1")   # page order preserved
    assert md4 == md1                                       # parallel == sequential output


def test_parse_scanned_pdf_without_llm_skips_prerender(monkeypatch):
    import io
    import pytest
    pytest.importorskip("fitz")
    import pdfplumber
    from md_converter.pdf import parse
    import md_converter.pdf._pdf as pdfmod
    from md_converter.pdf._ocr import is_scanned_page

    data = _make_scanned_pdf(1)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if not is_scanned_page(pdf.pages[0]):
            pytest.skip("fitz-built PDF not detected as scanned by pdfplumber")

    def fail_render(*args, **kwargs):
        raise AssertionError("scanned pages should not render without an LLM")

    monkeypatch.setattr(pdfmod, "render_bbox_to_png", fail_render)
    monkeypatch.setattr(pdfmod, "_ocr_one", lambda png, idx, data, llm: f"png={png!r};page={idx}")

    md, _ = parse(data, llm=None, max_ocr_workers=1)
    assert "png=b'';page=0" in md


def test_mdconverter_passes_ocr_workers(monkeypatch):
    import md_converter as mc
    from md_converter import MdConverter

    captured = {}

    def fake_pdf_parse(data, llm=None, max_ocr_workers=4):
        captured["max_ocr_workers"] = max_ocr_workers
        captured["llm"] = llm
        return "ok", []

    monkeypatch.setattr(mc, "_pdf_parse", fake_pdf_parse)
    conv = MdConverter(ocr_workers=7)
    out = conv.convert(b"%PDF-1.4 fake", suffix=".pdf")
    assert out == "ok"
    assert captured["max_ocr_workers"] == 7
    assert captured["llm"] is None


def test_parse_mixed_text_and_scanned_pages_order(monkeypatch):
    # text page, scanned page, text page → scanned OCR must land between them
    import io
    import pytest
    fitz = pytest.importorskip("fitz")
    import pdfplumber
    from md_converter.pdf import parse
    import md_converter.pdf._pdf as pdfmod
    from md_converter.pdf._ocr import is_scanned_page

    doc = fitz.open()
    p0 = doc.new_page(width=300, height=400)
    p0.insert_text((40, 60), "ALPHA " * 12, fontsize=12)
    p1 = doc.new_page(width=300, height=400)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400))
    pix.clear_with(240)
    p1.insert_image(fitz.Rect(0, 0, 300, 400), pixmap=pix)
    p2 = doc.new_page(width=300, height=400)
    p2.insert_text((40, 60), "OMEGA " * 12, fontsize=12)
    data = doc.tobytes()

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = pdf.pages
        if is_scanned_page(pages[0]) or not is_scanned_page(pages[1]) or is_scanned_page(pages[2]):
            pytest.skip("fitz pages not classified as text/scanned/text")

    monkeypatch.setattr(pdfmod, "_ocr_one", lambda png, idx, data, llm: "MIDOCR")
    md, _ = parse(data, llm=object(), max_ocr_workers=4)

    assert "ALPHA" in md and "MIDOCR" in md and "OMEGA" in md
    assert md.index("ALPHA") < md.index("MIDOCR") < md.index("OMEGA")
