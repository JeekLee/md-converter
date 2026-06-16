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


def _warning(
    warning_type: str,
    severity: str,
    line: int,
    excerpt: str,
    reason: str,
):
    return {
        "type": warning_type,
        "severity": severity,
        "line": line,
        "excerpt": excerpt,
        "reason": reason,
    }


def test_quality_warnings_flag_short_postal_code():
    benchmark = _load_benchmark_module()
    warnings = benchmark._quality_warnings("주소: 세종특별자치시\n우 3013")

    assert warnings == [
        _warning(
            "postal_code_width",
            "medium",
            2,
            "우 3013",
            "postal code is not 5, 6, or legacy 3-3 digits",
        )
    ]


def test_quality_warnings_accept_five_or_six_digit_postal_code():
    benchmark = _load_benchmark_module()
    assert benchmark._quality_warnings("우 30113") == []
    assert benchmark._quality_warnings("우 110793") == []
    assert benchmark._quality_warnings("우 427-721 경기도 과천시 중앙동 1") == []


def test_quality_warnings_flag_replacement_glyphs():
    benchmark = _load_benchmark_module()
    warnings = benchmark._quality_warnings("청구방법 □ 확인\nOCR � 문자")

    assert [w["type"] for w in warnings] == ["replacement_glyph", "replacement_glyph"]
    assert [w["line"] for w in warnings] == [1, 2]


def test_quality_warnings_flag_incomplete_date():
    benchmark = _load_benchmark_module()
    warnings = benchmark._quality_warnings("시행일: 2022. 4. 부터")

    assert warnings == [
        _warning(
            "date_incomplete",
            "medium",
            1,
            "시행일: 2022. 4. 부터",
            "date appears to be missing the day",
        )
    ]


def test_quality_warnings_accept_complete_dates():
    benchmark = _load_benchmark_module()
    assert benchmark._quality_warnings("2022. 4. 6.\n2022-04-06") == []


def test_quality_warnings_flag_suspicious_document_number():
    benchmark = _load_benchmark_module()
    warnings = benchmark._quality_warnings("보험급여과-12O3")

    assert warnings == [
        _warning(
            "document_number_suspicious",
            "high",
            1,
            "보험급여과-12O3",
            "Korean document number contains letters in the numeric part",
        )
    ]


def test_quality_warnings_accept_english_hyphenated_citations():
    benchmark = _load_benchmark_module()
    text = "\n".join(
        [
            "veno-occlusive disease high risk",
            "Gastroenterology. 2003 May;124(5):1277-91",
            "McGraw-Hill. p311-312",
            "post-endoscopic retrograde cholangiopancreatography",
        ]
    )

    assert benchmark._quality_warnings(text) == []


def test_quality_warnings_flag_suspicious_email():
    benchmark = _load_benchmark_module()
    warnings = benchmark._quality_warnings("문의: shsong98@korea")

    assert warnings == [
        _warning(
            "email_suspicious",
            "medium",
            1,
            "문의: shsong98@korea",
            "email-like text has no dotted domain",
        )
    ]


def test_quality_warnings_flag_known_admin_location_confusion():
    benchmark = _load_benchmark_module()
    warnings = benchmark._quality_warnings("세종특별자치시 도움4로 13 (여진동)")

    assert warnings == [
        _warning(
            "admin_location_suspicious",
            "high",
            1,
            "세종특별자치시 도움4로 13 (여진동)",
            "known OCR confusion in administrative location",
        )
    ]


def test_quality_warnings_include_actionable_severity_and_reason():
    benchmark = _load_benchmark_module()
    warnings = benchmark._quality_warnings("우 3013\n보험급여과-12O3")

    assert [w["severity"] for w in warnings] == ["medium", "high"]
    assert [w["reason"] for w in warnings] == [
        "postal code is not 5, 6, or legacy 3-3 digits",
        "Korean document number contains letters in the numeric part",
    ]


def test_markdown_metrics_include_quality_warning_count():
    benchmark = _load_benchmark_module()
    metrics = benchmark._markdown_metrics("우 3013\n정상")

    assert metrics["quality_warning_count"] == 1
    assert metrics["quality_warnings"][0]["type"] == "postal_code_width"


def test_warning_counts_can_group_by_severity():
    benchmark = _load_benchmark_module()
    results = [
        {
            "quality_warnings": [
                {"type": "postal_code_width", "severity": "medium"},
                {"type": "admin_location_suspicious", "severity": "high"},
            ]
        },
        {"quality_warnings": [{"type": "replacement_glyph", "severity": "high"}]},
    ]

    assert benchmark._warning_counts(results, "severity") == {
        "medium": 1,
        "high": 2,
    }
