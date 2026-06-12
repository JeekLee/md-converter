"""LLM helpers: drawing → Mermaid, vision → Mermaid, vision → text (OCR).

Calls any OpenAI-compatible /chat/completions endpoint.

stdlib only — no external HTTP client required.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass


@dataclass
class LlmConfig:
    url: str        # base URL, e.g. "http://localhost:10080/v1"
    api_key: str
    model: str      # e.g. "qwen3-vl-30b-a3b"


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


_OCR_PROMPT = """\
이 이미지는 스캔된 문서 페이지입니다.
이미지에서 텍스트를 읽어 그대로 추출해 주세요.

지침:
- 원문의 줄바꿈과 문단 구조를 최대한 유지
- 표는 텍스트 형태로 읽어서 그대로 출력
- 텍스트 내용만 출력, 설명 없이"""


def vision_to_text(png_bytes: bytes, cfg: LlmConfig) -> str:
    """스캔 페이지 PNG 이미지를 vision LLM으로 텍스트로 변환한다."""
    import base64
    b64 = base64.b64encode(png_bytes).decode()
    body = json.dumps({
        "model": cfg.model,
        "temperature": 0.0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": _OCR_PROMPT},
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        return result["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        sys.stderr.write(f"  vision OCR failed: {exc}\n")
        return ""
