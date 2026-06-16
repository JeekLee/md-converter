from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_benchmark_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_clic_minio.py"
    spec = importlib.util.spec_from_file_location("benchmark_clic_minio", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quality_warnings_flag_short_postal_code():
    benchmark = _load_benchmark_module()
    warnings = benchmark._quality_warnings("주소: 세종특별자치시\n우 3013")

    assert warnings == [
        {
            "type": "postal_code_width",
            "line": 2,
            "excerpt": "우 3013",
        }
    ]


def test_quality_warnings_accept_five_or_six_digit_postal_code():
    benchmark = _load_benchmark_module()
    assert benchmark._quality_warnings("우 30113") == []
    assert benchmark._quality_warnings("우 110793") == []


def test_quality_warnings_flag_replacement_glyphs():
    benchmark = _load_benchmark_module()
    warnings = benchmark._quality_warnings("청구방법 □ 확인\nOCR � 문자")

    assert [w["type"] for w in warnings] == ["replacement_glyph", "replacement_glyph"]
    assert [w["line"] for w in warnings] == [1, 2]


def test_markdown_metrics_include_quality_warning_count():
    benchmark = _load_benchmark_module()
    metrics = benchmark._markdown_metrics("우 3013\n정상")

    assert metrics["quality_warning_count"] == 1
    assert metrics["quality_warnings"][0]["type"] == "postal_code_width"
