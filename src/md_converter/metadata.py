"""Crawler-oriented conversion metadata helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DocumentProfile:
    kind: str
    page_count: int | None = None
    text_page_count: int | None = None
    scanned_page_count: int | None = None
    needs_ocr: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "page_count": self.page_count,
            "text_page_count": self.text_page_count,
            "scanned_page_count": self.scanned_page_count,
            "needs_ocr": self.needs_ocr,
        }


@dataclass(frozen=True)
class MarkdownMetrics:
    chars: int
    lines: int
    tables: int
    table_rows: int
    table_issues: int
    remaining_image_tokens: int
    remaining_nested_tokens: int
    unbalanced_fences: int

    def to_dict(self) -> dict[str, int]:
        return {
            "chars": self.chars,
            "lines": self.lines,
            "tables": self.tables,
            "table_rows": self.table_rows,
            "table_issues": self.table_issues,
            "remaining_image_tokens": self.remaining_image_tokens,
            "remaining_nested_tokens": self.remaining_nested_tokens,
            "unbalanced_fences": self.unbalanced_fences,
        }


@dataclass(frozen=True)
class ConversionResult:
    markdown: str
    suffix: str
    bytes: int
    sha256: str
    runtime_s: float
    metrics: MarkdownMetrics
    quality_warnings: list[dict[str, Any]]
    profile: DocumentProfile
    llm_used: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "suffix": self.suffix,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "runtime_s": self.runtime_s,
            "metrics": self.metrics.to_dict(),
            "quality_warnings": list(self.quality_warnings),
            "profile": self.profile.to_dict(),
            "llm_used": self.llm_used,
            "error": self.error,
        }


def table_counts(md: str) -> tuple[int, int, int]:
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


def markdown_metrics(md: str) -> MarkdownMetrics:
    tables, table_rows, table_issues = table_counts(md)
    return MarkdownMetrics(
        chars=len(md),
        lines=md.count("\n") + (1 if md else 0),
        tables=tables,
        table_rows=table_rows,
        table_issues=table_issues,
        remaining_image_tokens=md.count("[[RHWP_IMAGE:"),
        remaining_nested_tokens=md.count("[[NT:") + md.count("[[NT64:"),
        unbalanced_fences=md.count("```") % 2,
    )


def quality_warnings(md: str) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    postal_code = re.compile(r"\b우\s+(\d+)\b")
    incomplete_date = re.compile(r"\b\d{4}\.\s*\d{1,2}\.(?!\s*\d)")
    suspicious_doc_no = re.compile(r"[가-힣A-Za-z]+-\d*[A-Za-z]\d*")
    email_like = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\b")
    suspicious_admin_locations = ("(여진동)",)

    def add_warning(warning_type: str, line_no: int, excerpt: str) -> None:
        warnings.append(
            {
                "type": warning_type,
                "line": line_no,
                "excerpt": excerpt,
            }
        )

    for line_no, line in enumerate(md.splitlines(), start=1):
        excerpt = line.strip()
        for match in postal_code.finditer(line):
            if len(match.group(1)) < 5:
                add_warning("postal_code_width", line_no, excerpt)
        if incomplete_date.search(line):
            add_warning("date_incomplete", line_no, excerpt)
        if suspicious_doc_no.search(line):
            add_warning("document_number_suspicious", line_no, excerpt)
        for match in email_like.finditer(line):
            domain = match.group(0).split("@", 1)[1]
            if "." not in domain:
                add_warning("email_suspicious", line_no, excerpt)
                break
        if any(location in line for location in suspicious_admin_locations):
            add_warning("admin_location_suspicious", line_no, excerpt)
        if "�" in line or "□" in line:
            add_warning("replacement_glyph", line_no, excerpt)
    return warnings


def profile_for_suffix(suffix: str) -> DocumentProfile:
    return DocumentProfile(kind=suffix.lower().lstrip("."))
