from __future__ import annotations

import hashlib
import json


def test_convert_with_metadata_returns_crawler_friendly_result(monkeypatch):
    import md_converter as mc
    from md_converter import MdConverter

    def fake_hwp_parse(data: bytes):
        return "본문\n\n| A | B |\n| --- | --- |\n| 1 | 2 |", []

    monkeypatch.setattr(mc, "_hwp5_parse", fake_hwp_parse)

    data = b"fake hwp"
    result = MdConverter().convert_with_metadata(data, suffix=".hwp")

    assert result.markdown.startswith("본문")
    assert result.suffix == ".hwp"
    assert result.bytes == len(data)
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.runtime_s >= 0
    assert result.error is None
    assert result.llm_used is False
    assert result.metrics.chars == len(result.markdown)
    assert result.metrics.tables == 1
    assert result.metrics.table_issues == 0
    assert result.quality_warnings == []
    assert result.profile.kind == "hwp"
    assert result.profile.page_count is None


def test_conversion_result_to_dict_is_json_serializable(monkeypatch):
    import md_converter as mc
    from md_converter import MdConverter

    def fake_hwp_parse(data: bytes):
        return "우 3013\n\n| A | B |\n| --- | --- |\n| 1 | 2 |", []

    monkeypatch.setattr(mc, "_hwp5_parse", fake_hwp_parse)

    result = MdConverter().convert_with_metadata(b"fake hwp", suffix=".hwp")
    payload = result.to_dict()

    assert payload["markdown"] == result.markdown
    assert payload["suffix"] == ".hwp"
    assert payload["metrics"]["tables"] == 1
    assert payload["profile"]["kind"] == "hwp"
    assert payload["quality_warnings"][0]["type"] == "postal_code_width"
    assert payload["error"] is None
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_convert_with_metadata_includes_source_and_converter_metadata(monkeypatch):
    import md_converter as mc
    from md_converter import LlmConfig, MdConverter

    def fake_hwp_parse(data: bytes):
        return "본문", []

    monkeypatch.setattr(mc, "_hwp5_parse", fake_hwp_parse)

    converter = MdConverter(
        llm=LlmConfig(url="http://llm.test/v1", api_key="test", model="vision-model"),
        ocr_workers=2,
    )
    result = converter.convert_with_metadata(
        b"fake hwp",
        suffix=".hwp",
        source_id="post-123/attachment-2",
        source_url="https://example.test/notice/123",
        source_name="첨부파일.hwp",
    )
    payload = result.to_dict()

    assert payload["source"] == {
        "id": "post-123/attachment-2",
        "url": "https://example.test/notice/123",
        "name": "첨부파일.hwp",
    }
    assert payload["converter"] == {
        "ocr_workers": 2,
        "llm_enabled": True,
        "llm_model": "vision-model",
    }
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_convert_with_metadata_can_capture_errors():
    from md_converter import MdConverter

    result = MdConverter().convert_with_metadata(b"bad", suffix=".doc", raise_errors=False)

    assert result.markdown == ""
    assert result.suffix == ".doc"
    assert result.error is not None
    assert "Unsupported format" in result.error
    assert result.metrics.chars == 0
    assert result.quality_warnings == []

    payload = result.to_dict()
    assert payload["markdown"] == ""
    assert payload["error"] == result.error
    assert payload["metrics"]["chars"] == 0
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_convert_with_metadata_includes_quality_warnings(monkeypatch):
    import md_converter as mc
    from md_converter import MdConverter

    def fake_hwp_parse(data: bytes):
        return "우 3013 세종특별자치시 도움4로 13 (여진동)", []

    monkeypatch.setattr(mc, "_hwp5_parse", fake_hwp_parse)

    result = MdConverter().convert_with_metadata(b"fake hwp", suffix=".hwp")

    assert [w["type"] for w in result.quality_warnings] == [
        "postal_code_width",
        "admin_location_suspicious",
    ]


def test_profile_pdf_counts_text_and_scanned_pages():
    import pytest

    fitz = pytest.importorskip("fitz")
    from md_converter.pdf import profile_pdf

    doc = fitz.open()
    text_page = doc.new_page(width=300, height=400)
    text_page.insert_text((40, 60), "ALPHA " * 12, fontsize=12)
    scanned_page = doc.new_page(width=300, height=400)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400))
    pix.clear_with(240)
    scanned_page.insert_image(fitz.Rect(0, 0, 300, 400), pixmap=pix)

    profile = profile_pdf(doc.tobytes())

    assert profile.kind == "pdf"
    assert profile.page_count == 2
    assert profile.text_page_count == 1
    assert profile.scanned_page_count == 1
    assert profile.needs_ocr is True
