"""Basic smoke tests for HWPX → Markdown conversion."""

from __future__ import annotations

import io
import zipfile
from textwrap import dedent

import pytest

from md_converter.hwp.hwpx import convert, parse


def _make_hwpx(section_xml: str) -> bytes:
    """Build a minimal HWPX ZIP with one section."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Contents/section0.xml", section_xml)
    return buf.getvalue()


HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HS = "http://www.hancom.co.kr/hwpml/2011/section"


def _sec(*paragraphs: str) -> str:
    inner = "\n".join(paragraphs)
    return f'<sec xmlns:hp="{HP}" xmlns:hs="{HS}">{inner}</sec>'


def _p(*runs: str) -> str:
    return f'<hp:p>{"".join(runs)}</hp:p>'


def _run(text: str) -> str:
    return f"<hp:run><hp:t>{text}</hp:t></hp:run>"


def _tbl(*rows: list[str]) -> str:
    tr_xml = ""
    for cells in rows:
        tcs = "".join(
            f"<hp:tc><hp:subList><hp:p><hp:run><hp:t>{c}</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            for c in cells
        )
        tr_xml += f"<hp:tr>{tcs}</hp:tr>"
    return f"<hp:p><hp:run><hp:tbl>{tr_xml}</hp:tbl></hp:run></hp:p>"


# ── tests ──────────────────────────────────────────────────────────────────


def test_plain_paragraphs():
    xml = _sec(_p(_run("첫 번째 문단")), _p(_run("두 번째 문단")))
    md = convert(_make_hwpx(xml))
    assert "첫 번째 문단" in md
    assert "두 번째 문단" in md


def test_empty_paragraphs_skipped():
    xml = _sec(_p(_run("")), _p(_run("내용")), _p(_run("  ")))
    md = convert(_make_hwpx(xml))
    assert md.strip() == "내용"


def test_table_basic():
    xml = _sec(_tbl(["항목", "설명"], ["사항1", "설명1"]))
    md = convert(_make_hwpx(xml))
    assert "| 항목 | 설명 |" in md
    assert "| --- |" in md
    assert "| 사항1 | 설명1 |" in md


def test_table_pipe_escaped_in_cell():
    xml = _sec(_tbl(["A|B", "C"]))
    md = convert(_make_hwpx(xml))
    assert "A\\|B" in md


def test_control_chars_filtered():
    # XML 1.0 prohibits raw U+0000-U+001F (except \t \n \r) in text content.
    # HWP5 PARA_TEXT has inline object placeholders in this range; HWPX uses
    # separate XML elements instead. Verify the filter is a no-op on clean text.
    xml = _sec(_p(_run("정상텍스트")))
    md = convert(_make_hwpx(xml))
    assert md.strip() == "정상텍스트"


def test_multiple_sections():
    sec0 = f'<sec xmlns:hp="{HP}">{_p(_run("섹션0"))}</sec>'
    sec1 = f'<sec xmlns:hp="{HP}">{_p(_run("섹션1"))}</sec>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Contents/section0.xml", sec0)
        z.writestr("Contents/section1.xml", sec1)
    md = convert(buf.getvalue())
    assert "섹션0" in md
    assert "섹션1" in md
    assert md.index("섹션0") < md.index("섹션1")


def _rect(text: str) -> str:
    """Minimal hp:rect with a drawText text box containing one paragraph."""
    return (
        f'<hp:rect>'
        f'<hp:drawText>'
        f'<hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList>'
        f'</hp:drawText>'
        f'</hp:rect>'
    )


def test_drawing_shapes_collected():
    """Paragraphs containing hp:rect shapes emit a ```hwp-drawing block."""
    xml = _sec(
        f'<hp:p>{_rect("시작")}</hp:p>',
        f'<hp:p>{_rect("처리")}{_rect("검토")}</hp:p>',
        _p(_run("일반 텍스트")),
    )
    md, _ = parse(_make_hwpx(xml))
    assert "```hwp-drawing" in md
    assert "시작" in md
    assert "처리" in md
    assert "검토" in md
    assert "일반 텍스트" in md


def test_drawing_without_text_ignored():
    """A shape with no drawText produces no hwp-drawing block."""
    xml = _sec(
        f'<hp:p><hp:rect></hp:rect></hp:p>',
        _p(_run("텍스트")),
    )
    md, _ = parse(_make_hwpx(xml))
    assert "hwp-drawing" not in md
    assert "텍스트" in md


def test_drawing_with_connection_emits_mermaid():
    """hp:connectLine이 있으면 hwp-drawing 대신 mermaid 블록을 emit한다."""
    xml = _sec(
        '<hp:p>'
        '<hp:rect id="1"><hp:drawText><hp:subList>'
        '<hp:p><hp:run><hp:t>시작</hp:t></hp:run></hp:p>'
        '</hp:subList></hp:drawText></hp:rect>'
        '<hp:rect id="2"><hp:drawText><hp:subList>'
        '<hp:p><hp:run><hp:t>종료</hp:t></hp:run></hp:p>'
        '</hp:subList></hp:drawText></hp:rect>'
        '<hp:connectLine startConnectShapeId="1" endConnectShapeId="2" endArrow="arrow"/>'
        '</hp:p>'
    )
    md, _ = parse(_make_hwpx(xml))
    assert "```mermaid" in md
    assert "```hwp-drawing" not in md
    assert "시작" in md
    assert "종료" in md


def test_drawing_without_connection_keeps_hwp_drawing():
    """hp:connectLine 없으면 기존 hwp-drawing 블록을 emit한다."""
    xml = _sec(
        f'<hp:p>{_rect("레이블A")}{_rect("레이블B")}</hp:p>',
    )
    md, _ = parse(_make_hwpx(xml))
    assert "```hwp-drawing" in md
    assert "```mermaid" not in md


def test_real_hira_hwpx(tmp_path):
    """Smoke test against a real HIRA HWPX file (skipped if not present)."""
    sample = tmp_path.parent.parent / "test_hira.hwpx"
    if not sample.exists():
        sample = __import__("pathlib").Path("/tmp/test_hira.hwpx")
    if not sample.exists():
        pytest.skip("no sample HWPX file found")
    md = convert(sample.read_bytes())
    assert len(md) > 100
    # The test document contains a 3-column table header
    assert "항  목" in md or "항목" in md


def test_nested_table_separated_via_converter():
    from md_converter import MdConverter, LlmConfig

    def _tc(text: str) -> str:
        return (
            "<hp:tc><hp:subList><hp:p><hp:run>"
            f"<hp:t>{text}</hp:t>"
            "</hp:run></hp:p></hp:subList></hp:tc>"
        )

    nested = (
        "<hp:tbl>"
        f"<hp:tr>{_tc('항목')}{_tc('금액')}</hp:tr>"
        f"<hp:tr>{_tc('외래')}{_tc('1000')}</hp:tr>"
        "</hp:tbl>"
    )
    nested_cell = (
        f"<hp:tc><hp:subList><hp:p><hp:run>{nested}</hp:run></hp:p></hp:subList></hp:tc>"
    )
    outer = (
        "<hp:p><hp:run><hp:tbl>"
        f"<hp:tr>{_tc('구분')}{_tc('세부내용')}</hp:tr>"
        f"<hp:tr>{_tc('본인부담')}{nested_cell}</hp:tr>"
        "</hp:tbl></hp:run></hp:p>"
    )
    xml = _sec(outer)

    # LLM은 중첩 표 처리에 더 이상 쓰이지 않으므로 더미 설정(미사용)으로 충분.
    converter = MdConverter(llm=LlmConfig(url="http://unused.invalid", api_key="x", model="x"))
    md = converter.convert(_make_hwpx(xml), suffix=".hwpx")

    assert "[[NT:" not in md
    assert "→ 표 1" in md
    assert "**[표 1]**" in md
    assert "| 항목 | 금액 |" in md
    assert "| 외래 | 1000 |" in md


def test_depth2_nested_table_flattened_via_converter():
    from md_converter import MdConverter, LlmConfig

    def _tc(text: str) -> str:
        return f"<hp:tc><hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList></hp:tc>"

    def _tc_tbl(inner_tbl: str) -> str:
        return f"<hp:tc><hp:subList><hp:p><hp:run>{inner_tbl}</hp:run></hp:p></hp:subList></hp:tc>"

    depth2 = f"<hp:tbl><hp:tr>{_tc('L2leaf')}</hp:tr></hp:tbl>"
    depth1 = f"<hp:tbl><hp:tr>{_tc('d1cell')}{_tc_tbl(depth2)}</hp:tr></hp:tbl>"
    outer = (
        "<hp:p><hp:run><hp:tbl>"
        f"<hp:tr>{_tc('구분')}{_tc_tbl(depth1)}</hp:tr>"
        "</hp:tbl></hp:run></hp:p>"
    )
    xml = _sec(outer)
    md = MdConverter(llm=LlmConfig(url="http://unused.invalid", api_key="x", model="x")).convert(
        _make_hwpx(xml), suffix=".hwpx"
    )
    # depth-2 leaf content must be flattened into the separated table, not dropped
    assert "L2leaf" in md
    assert "→ 표 1" in md
    assert "**[표 1]**" in md
