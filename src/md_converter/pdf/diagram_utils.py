"""PDF 페이지에서 다이어그램 영역 감지 + pymupdf 렌더링."""
from __future__ import annotations


def detect_diagram_bboxes(
    page,
    table_bboxes: list[tuple[float, float, float, float]],
) -> list[tuple[float, tuple[float, float, float, float]]]:
    """표와 겹치지 않는 rect 클러스터를 다이어그램 후보로 감지한다.

    반환: (y_pos, (x0, top, x1, bottom)) 리스트
    """
    rects: list[tuple[float, float, float, float]] = []
    for r in page.rects:
        rx0, rtop, rx1, rbottom = r["x0"], r["top"], r["x1"], r["bottom"]
        if (rx1 - rx0) < 5 or (rbottom - rtop) < 5:
            continue
        if any(
            rx0 < tx1 and rx1 > tx0 and rtop < tbottom and rbottom > ttop
            for tx0, ttop, tx1, tbottom in table_bboxes
        ):
            continue
        rects.append((rx0, rtop, rx1, rbottom))

    if not rects:
        return []

    rects.sort(key=lambda r: r[1])

    clusters: list[list[tuple[float, float, float, float]]] = [[rects[0]]]
    for r in rects[1:]:
        prev_bottom = max(prev[3] for prev in clusters[-1])
        if r[1] - prev_bottom <= 20:
            clusters[-1].append(r)
        else:
            clusters.append([r])

    results: list[tuple[float, tuple[float, float, float, float]]] = []
    for cluster in clusters:
        if len(cluster) < 3:
            continue

        x0     = max(0.0,         min(r[0] for r in cluster) - 10)
        top    = max(0.0,         min(r[1] for r in cluster) - 10)
        x1     = min(page.width,  max(r[2] for r in cluster) + 10)
        bottom = min(page.height, max(r[3] for r in cluster) + 10)

        crop = page.crop((x0, top, x1, bottom))
        text = crop.extract_text() or ""
        area = (x1 - x0) * (bottom - top)
        if area > 0 and len(text) / area > 0.1:
            continue

        results.append((top, (x0, top, x1, bottom)))

    return results


def render_bbox_to_png(
    pdf_bytes: bytes,
    page_idx: int,
    bbox: tuple[float, float, float, float],
) -> bytes:
    """pymupdf로 PDF 페이지의 bbox 영역을 PNG bytes로 렌더링한다."""
    try:
        import fitz
    except ImportError as exc:
        raise ImportError(
            "PDF 다이어그램 렌더링은 pymupdf가 필요합니다. "
            "pip install 'md-converter[pdf]'"
        ) from exc

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    fitz_page = doc.load_page(page_idx)
    clip = fitz.Rect(*bbox)
    mat = fitz.Matrix(2.0, 2.0)
    pix = fitz_page.get_pixmap(matrix=mat, clip=clip)
    return pix.tobytes("png")
