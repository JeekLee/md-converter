from md_converter.pdf._diagram_utils import detect_diagram_bboxes, render_bbox_to_png


class _MockCrop:
    def extract_text(self):
        return ""


class _MockPage:
    def __init__(self, rects, width=595.0, height=842.0):
        self.rects = rects
        self.width = width
        self.height = height

    def crop(self, bbox):
        return _MockCrop()


def _r(x0, top, x1, bottom):
    return {"x0": x0, "top": top, "x1": x1, "bottom": bottom}


def test_no_rects_returns_empty():
    page = _MockPage(rects=[])
    result = detect_diagram_bboxes(page, table_bboxes=[])
    assert result == []


def test_cluster_under_threshold_ignored():
    # 2 rects — below threshold of 3
    page = _MockPage(rects=[_r(10, 10, 100, 60), _r(10, 70, 100, 120)])
    result = detect_diagram_bboxes(page, table_bboxes=[])
    assert result == []


def test_cluster_at_threshold_detected():
    # 3 rects with y-proximity (gap < 20pt)
    page = _MockPage(rects=[
        _r(10, 10,  100, 60),
        _r(10, 70,  100, 120),
        _r(10, 130, 100, 180),
    ])
    result = detect_diagram_bboxes(page, table_bboxes=[])
    assert len(result) == 1
    y_pos, bbox = result[0]
    assert y_pos < 30   # cluster top with padding
    x0, top, x1, bottom = bbox
    assert x0 <= 10
    assert x1 >= 100


def test_table_overlapping_rects_excluded():
    page = _MockPage(rects=[
        _r(10, 10,  100, 60),
        _r(10, 70,  100, 120),
        _r(10, 130, 100, 180),
    ])
    table_bboxes = [(0.0, 0.0, 200.0, 200.0)]
    result = detect_diagram_bboxes(page, table_bboxes=table_bboxes)
    assert result == []


def test_render_bbox_to_png_requires_pymupdf(tmp_path):
    """pymupdf가 없으면 ImportError, 있으면 PNG bytes 반환."""
    try:
        import fitz  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("pymupdf not installed")
    minimal_pdf = (
        b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj "
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
        b"3 0 obj<</Type/Page/MediaBox[0 0 100 100]/Parent 2 0 R>>endobj "
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
    )
    result = render_bbox_to_png(minimal_pdf, page_idx=0, bbox=(0.0, 0.0, 100.0, 100.0))
    assert isinstance(result, bytes)
    assert result[:4] == b"\x89PNG"
