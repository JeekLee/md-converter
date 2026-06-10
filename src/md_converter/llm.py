"""LLM-based nested table restructuring.

Mirrors rhwp's restructure_nested_tables() in src/main.rs.
Calls any OpenAI-compatible /chat/completions endpoint.

stdlib only — no external HTTP client required.
"""
from __future__ import annotations

import json
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
