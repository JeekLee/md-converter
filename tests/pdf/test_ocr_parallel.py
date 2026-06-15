"""Tests for parallel scanned-page OCR (_ocr_pages)."""
from __future__ import annotations

import time

from md_converter.pdf._pdf import _ocr_pages


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
