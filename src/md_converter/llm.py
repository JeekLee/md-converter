"""LLM-based nested table restructuring.

Mirrors rhwp's restructure_nested_tables() in src/main.rs.
Calls any OpenAI-compatible /chat/completions endpoint.

stdlib only — no external HTTP client required.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class LlmConfig:
    url: str        # base URL, e.g. "http://localhost:10080/v1"
    api_key: str
    model: str      # e.g. "qwen3-vl-30b-a3b"


_PROMPT_TEMPLATE = """\
아래는 한글 문서 표 셀 내부에 중첩된 표의 데이터입니다.
데이터 형식: ';'는 행 구분, '|'는 같은 행 안의 셀 구분입니다.
이 구조와 내용을 파악하여 사람이 읽기 가장 자연스러운 텍스트로 변환하세요.
들여쓰기, 기호(-, ·, 번호 등)를 내용에 맞게 자유롭게 활용하세요.

제약:
- 줄 구분은 " <br> " 사용 (마크다운 표 셀 내부이므로 실제 줄바꿈 금지)
- 마크다운 표(|) 문법 사용 금지
- 변환된 내용만 출력, 설명 없이

중첩 표 데이터:
{content}"""


def _call_llm(content: str, cfg: LlmConfig) -> str:
    prompt = _PROMPT_TEMPLATE.format(content=content)
    body = json.dumps({
        "model": cfg.model,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    endpoint = f"{cfg.url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())

    text: str = result["choices"][0]["message"]["content"].strip()
    # Constraints matching rhwp: escape pipes, replace newlines with <br>
    return text.replace("|", "\\|").replace("\r", "").replace("\n", " <br> ")


_DRAWING_PROMPT = """\
아래는 한글 문서(HWP) 도형 개체 안에 있던 텍스트 레이블입니다.
도형·연결선·상자·화살표 등으로 구성된 다이어그램의 텍스트입니다.
내용을 분석하여 가장 적합한 Mermaid 다이어그램으로 변환해 주세요.

지침:
- graph TD, flowchart LR, sequenceDiagram 등 가장 적합한 유형 선택
- 변환이 불가능하면 graph TD 안에 텍스트를 노드로 배치
- Mermaid 코드만 출력, 설명 없이 (```mermaid 래퍼 없이)

도형 텍스트:
{content}"""


def drawing_to_mermaid(text: str, cfg: LlmConfig) -> str | None:
    """Convert drawing text labels to a Mermaid diagram via LLM.

    Returns Mermaid source on success, None on failure.
    """
    body = json.dumps({
        "model": cfg.model,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": _DRAWING_PROMPT.format(content=text)}],
    }).encode()
    endpoint = f"{cfg.url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
        mermaid: str = result["choices"][0]["message"]["content"].strip()
        mermaid = re.sub(r"^```(?:mermaid)?\s*", "", mermaid)
        mermaid = re.sub(r"\s*```\s*$", "", mermaid)
        return mermaid.strip() or None
    except Exception as exc:
        sys.stderr.write(f"  drawing → mermaid failed: {exc}\n")
        return None


_DIAGRAM_VISION_PROMPT = """\
이 다이어그램 이미지를 Mermaid 코드로 변환해 주세요.

지침:
- graph TD, flowchart LR, sequenceDiagram 등 가장 적합한 유형 선택
- 변환이 불가능하면 graph TD 안에 텍스트를 노드로 배치
- Mermaid 코드만 출력, 설명 없이 (```mermaid 래퍼 없이)"""


def vision_to_mermaid(png_bytes: bytes, cfg: LlmConfig) -> str | None:
    """다이어그램 PNG 이미지를 vision LLM으로 Mermaid 코드로 변환한다."""
    import base64
    b64 = base64.b64encode(png_bytes).decode()
    body = json.dumps({
        "model": cfg.model,
        "temperature": 0.0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": _DIAGRAM_VISION_PROMPT},
            ],
        }],
    }).encode()
    endpoint = f"{cfg.url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())
        mermaid: str = result["choices"][0]["message"]["content"].strip()
        mermaid = re.sub(r"^```(?:mermaid)?\s*", "", mermaid)
        mermaid = re.sub(r"\s*```\s*$", "", mermaid)
        return mermaid.strip() or None
    except Exception as exc:
        sys.stderr.write(f"  vision → mermaid failed: {exc}\n")
        return None


def restructure_nested_tables(markdown: str, cfg: LlmConfig) -> str:
    """Replace [[NT:...]] markers with LLM-restructured text.

    On LLM failure the flat [[NT:...]] content is kept (silent fallback).
    """
    if "[[NT:" not in markdown:
        return markdown

    result: list[str] = []
    remaining = markdown

    while "[[NT:" in remaining:
        start = remaining.find("[[NT:")
        result.append(remaining[:start])
        after_open = remaining[start + 5:]
        end = after_open.find("]]")
        if end == -1:
            result.append(remaining[start:])
            remaining = ""
            break
        content = after_open[:end]
        try:
            restructured = _call_llm(content, cfg)
            sys.stderr.write(
                f"  nested table restructured: {len(content)} → {len(restructured)} chars\n"
            )
            result.append(restructured)
        except Exception as exc:
            sys.stderr.write(f"  nested table LLM failed, keeping flat text: {exc}\n")
            result.append(content)
        remaining = after_open[end + 2:]

    result.append(remaining)
    return "".join(result)
