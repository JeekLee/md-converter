# clic-minio LLM Markdown Quality Check

Date: 2026-06-16

Benchmark command:

```bash
uv run python scripts/benchmark_clic_minio.py \
  --env-file /home/jeek_lee/work/cryptolab/clic-poc/.env \
  --runs 1 \
  --ocr-workers 2 \
  --output-dir /tmp/md-converter-clic-bench/llm-workers2-final
```

LLM settings:

- Endpoint: `http://localhost:10080/v1`
- Model: `qwen3-vl-30b-a3b`
- Dataset: 4 PDF/HWP or PDF/HWPX pairs, 8 documents total

## Summary

| Metric | Value |
| --- | ---: |
| Total runtime | 23.32s |
| Total Markdown chars | 11,344 |
| Tables | 18 |
| Table structure issues | 0 |
| Remaining internal tokens | 0 |
| Unbalanced code fences | 0 |
| OCR quality warnings | 1 |

The LLM path was only material for one scanned PDF:

- `20220406-1-0001-pdf.md`: 2 VLM OCR pages, 1,084 Markdown chars, 23.02s with `ocr_workers=2`.
- All other documents had text/structure layers and completed without meaningful LLM work.

## OCR Worker Comparison

| Setting | Total runtime | Scanned PDF runtime | Scanned PDF chars | Quality warnings |
| --- | ---: | ---: | ---: | ---: |
| `ocr_workers=1` | 33.50s | 33.20s | 1,086 | 1 |
| `ocr_workers=2` | 23.32s | 23.02s | 1,084 | 1 |

`ocr_workers=2` reduced total runtime by about 30.4% and the scanned-PDF runtime
by about 30.7%. OCR output stayed within a 0.2% character delta, with the same
single `postal_code_width` warning.

## Per Document

| Document | Format | Runtime | Markdown chars | Tables | Notes |
| --- | --- | ---: | ---: | ---: | --- |
| `20120330-2-0001` | PDF | 0.10s | 550 | 0 | Cover letter; empty signature-area table is filtered. |
| `20120330-2-0001` | HWP | 0.001s | 2,531 | 2 | Attachment/Q&A content extracted well; title rendered as paragraph. |
| `20160829-2-0001` | PDF | 0.05s | 526 | 0 | Cover letter; no table issues. |
| `20160829-2-0001` | HWP | 0.0003s | 333 | 1 | Attachment/Q&A content extracted well; title rendered as paragraph. |
| `20231226-1-0001` | PDF | 0.15s | 2,004 | 2 | Large PDF table is valid GFM but semantically flattened. |
| `20231226-1-0001` | HWPX | 0.003s | 1,079 | 1 | 신구조문 대비표 structure is better preserved than PDF. |
| `20220406-1-0001` | PDF | 23.02s | 1,084 | 0 | Scanned cover letter OCR; one postal-code warning remains. |
| `20220406-1-0001` | HWPX | 0.006s | 3,237 | 12 | Attachment content and nested table separation preserved; title-like one-cell tables rendered as paragraphs. |

## Findings

1. The fixed "pairs" are not strict same-content pairs.
   The PDFs are mostly cover letters and the HWP/HWPX files are attachments. Low pair text similarity is therefore expected and should not be treated as a conversion failure by itself.

2. Markdown syntax is structurally clean.
   Across all 8 outputs, table pipe consistency, internal token cleanup, and code fence balance all pass.

3. LLM OCR recovers the scanned PDF instead of returning empty Markdown.
   `20220406-1-0001-pdf.md` changes from empty output in no-LLM mode to 1,091 chars with LLM OCR.

4. LLM OCR has confirmed small text errors.
   On `20220406-1-0001` page 2, the source image shows `우 30113` and `(어진동)`, while Markdown contains `우 3013` and `(여진동)`. This is a content-accuracy issue, not a Markdown syntax issue.

5. PDF table quality still lags HWPX for complex wide tables.
   `20231226-1-0001-pdf.md` keeps a valid GFM table, but multiple codes and long descriptions are packed into single cells. The HWPX counterpart preserves the comparison table more cleanly.

6. Non-content layout artifacts improved.
   Empty PDF signature-area tables are filtered, and single-cell title-like
   HWP/HWPX/PDF tables render as paragraphs. Multi-row one-column tables remain
   tables.

## Recommended Next Changes

1. Expand OCR quality checks for high-risk fields.
   Postal-code width checks now flag short OCR output. Dates, document numbers,
   and Korean administrative location names still need lightweight checks.

2. Improve scanned PDF OCR accuracy for small text.
   `20220406-1-0001-pdf.md` still reads `우 30113` as `우 3013` and
   `(어진동)` as `(여진동)`.

3. Improve PDF table semantics for complex wide tables.
   `20231226-1-0001-pdf.md` is valid GFM, but still less semantically clean than
   the HWPX counterpart.
