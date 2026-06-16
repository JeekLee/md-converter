"""Benchmark md-converter against paired documents in clic-minio.

The fixed dataset contains four source pairs, each with a PDF and either an
HWP or HWPX counterpart.  The script reports runtime medians and lightweight
Markdown quality counters so conversion changes can be compared consistently.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any

from md_converter import LlmConfig, MdConverter


DATASET: list[tuple[str, str, str]] = [
    ("20120330-2-0001", "pdf", "raw/20120330-2-0001/고시제2012-39호 관련 해석.pdf"),
    ("20120330-2-0001", "hwp", "raw/20120330-2-0001/붙임 질의응답.hwp"),
    (
        "20160829-2-0001",
        "pdf",
        "raw/20160829-2-0001/[본문] 이식형 결찰사를 이용한 전립선 결찰 관련 질의응답.pdf",
    ),
    (
        "20160829-2-0001",
        "hwp",
        "raw/20160829-2-0001/(제2016-169호) 이식형 결찰사를 이용한 전립선 결찰_관련 질의응답.hwp",
    ),
    (
        "20231226-1-0001",
        "pdf",
        "raw/20231226-1-0001/(행정해석) 자동차운영보험과-9328, 자동차보험진료수가 한방물리요법의 진료수가 및 산정기준 개정 알림.pdf",
    ),
    (
        "20231226-1-0001",
        "hwpx",
        "raw/20231226-1-0001/(행정해석) 자동차운영보험과-9328, 자동차보험진료수가 한방물리요법의 진료수가 및 산정기준 개정 신구조문대비표.hwpx",
    ),
    (
        "20220406-1-0001",
        "pdf",
        "raw/20220406-1-0001/[공문]코로나19 대면투약관리료 관련 요양급여 적용기준 및 청구방법 안내.pdf",
    ),
    (
        "20220406-1-0001",
        "hwpx",
        "raw/20220406-1-0001/코로나19 대면투약관리료 관련 요양급여 적용기준 및 청구방법 안내(최종).hwpx",
    ),
]


def _safe_name(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", key)


def _make_s3_client(endpoint: str, access_key: str, secret_key: str) -> Any:
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise SystemExit("benchmark_clic_minio.py requires boto3 and botocore") from exc

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise SystemExit(f"env file does not exist: {path}")
    for raw_line in path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _ensure_cached(
    s3: Any,
    *,
    bucket: str,
    cache_dir: Path,
    key: str,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / _safe_name(key)
    if not path.exists():
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        path.write_bytes(body)
    return path


def _table_counts(md: str) -> tuple[int, int, int]:
    issues = 0
    tables = 0
    rows = 0
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            i += 1
            continue

        block: list[str] = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            block.append(lines[i])
            i += 1

        if len(block) < 2:
            issues += 1
            continue

        tables += 1
        rows += len(block)
        pipe_counts = [max(0, line.count("|") - 1) for line in block if line.strip()]
        if len(set(pipe_counts)) > 1:
            issues += 1

    return tables, rows, issues


def _markdown_metrics(md: str) -> dict[str, int]:
    tables, table_rows, table_issues = _table_counts(md)
    return {
        "chars": len(md),
        "lines": md.count("\n") + (1 if md else 0),
        "tables": tables,
        "table_rows": table_rows,
        "table_issues": table_issues,
        "remaining_image_tokens": md.count("[[RHWP_IMAGE:"),
        "remaining_nested_tokens": md.count("[[NT:") + md.count("[[NT64:"),
        "unbalanced_fences": md.count("```") % 2,
    }


def _normalize_for_similarity(md: str) -> str:
    md = re.sub(r"```.*?```", " ", md, flags=re.S)
    md = re.sub(r"[^0-9A-Za-z가-힣]+", " ", md).lower()
    return re.sub(r"\s+", " ", md).strip()


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    s3 = _make_s3_client(args.endpoint, args.access_key, args.secret_key)
    llm = None
    if args.llm_url and args.llm_api_key and args.llm_model:
        llm = LlmConfig(url=args.llm_url, api_key=args.llm_api_key, model=args.llm_model)
    converter = MdConverter(llm=llm, ocr_workers=args.ocr_workers)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    by_doc: dict[str, dict[str, str]] = {}
    for doc_id, kind, key in DATASET:
        path = _ensure_cached(s3, bucket=args.bucket, cache_dir=args.cache_dir, key=key)
        suffix = "." + kind
        times: list[float] = []
        md = ""
        for _ in range(args.runs):
            started = time.perf_counter()
            md = converter.convert(path, suffix=suffix)
            times.append(time.perf_counter() - started)

        (output_dir / f"{doc_id}-{kind}.md").write_text(md, encoding="utf-8")
        results.append(
            {
                "doc_id": doc_id,
                "kind": kind,
                "key": key,
                "bytes": path.stat().st_size,
                "time_s_median": statistics.median(times),
                "time_s_runs": times,
                **_markdown_metrics(md),
            }
        )
        by_doc.setdefault(doc_id, {})[kind] = md

    pairs: list[dict[str, Any]] = []
    for doc_id, docs in by_doc.items():
        if "pdf" not in docs:
            continue
        other = "hwp" if "hwp" in docs else "hwpx"
        pdf_text = _normalize_for_similarity(docs["pdf"])[: args.similarity_chars]
        other_text = _normalize_for_similarity(docs[other])[: args.similarity_chars]
        pairs.append(
            {
                "doc_id": doc_id,
                "pair": f"pdf-{other}",
                "text_similarity_ratio": difflib.SequenceMatcher(None, pdf_text, other_text).ratio(),
            }
        )

    return {
        "dataset_size": len(DATASET),
        "llm_enabled": llm is not None,
        "llm_url": args.llm_url if llm is not None else None,
        "llm_model": args.llm_model if llm is not None else None,
        "pairs": pairs,
        "total_time_s_median_sum": sum(r["time_s_median"] for r in results),
        "by_kind_time_s_median_sum": {
            kind: sum(r["time_s_median"] for r in results if r["kind"] == kind)
            for kind in ("pdf", "hwp", "hwpx")
        },
        "quality_totals": {
            "chars": sum(r["chars"] for r in results),
            "tables": sum(r["tables"] for r in results),
            "table_issues": sum(r["table_issues"] for r in results),
            "remaining_tokens": sum(
                r["remaining_image_tokens"] + r["remaining_nested_tokens"]
                for r in results
            ),
            "unbalanced_fences": sum(r["unbalanced_fences"] for r in results),
        },
        "results": results,
        "output_dir": str(output_dir),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://localhost:10190")
    parser.add_argument("--bucket", default="clic")
    parser.add_argument("--access-key", default="minioadmin")
    parser.add_argument("--secret-key", default="minioadmin")
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/md-converter-clic-bench"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/md-converter-clic-bench/latest"),
    )
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--ocr-workers", type=int, default=1)
    parser.add_argument("--similarity-chars", type=int, default=20_000)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--llm-url", default=os.environ.get("LLM_BASE_URL"))
    parser.add_argument("--llm-api-key", default=os.environ.get("LLM_API_KEY"))
    parser.add_argument(
        "--llm-model",
        default=os.environ.get("VLM_MODEL") or os.environ.get("LLM_MODEL"),
    )
    args = parser.parse_args()
    if args.env_file is not None:
        env_values = _load_env_file(args.env_file)
        args.llm_url = args.llm_url or env_values.get("LLM_BASE_URL")
        args.llm_api_key = args.llm_api_key or env_values.get("LLM_API_KEY")
        args.llm_model = (
            args.llm_model
            or env_values.get("VLM_MODEL")
            or env_values.get("LLM_MODEL")
        )
    return args


def main() -> None:
    summary = run_benchmark(parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
