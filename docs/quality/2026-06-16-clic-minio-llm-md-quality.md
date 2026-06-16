# clic-minio LLM Markdown Quality Check

Date: 2026-06-16

Benchmark command:

```bash
uv run python scripts/benchmark_clic_minio.py \
  --env-file /home/jeek_lee/work/cryptolab/clic-poc/.env \
  --runs 1 \
  --ocr-workers 1 \
  --output-dir /tmp/md-converter-clic-bench/llm-envfile-runs1
```

LLM settings:

- Endpoint: `http://localhost:10080/v1`
- Model: `qwen3-vl-30b-a3b`
- Dataset: 4 PDF/HWP or PDF/HWPX pairs, 8 documents total

## Summary

| Metric | Value |
| --- | ---: |
| Total runtime | 60.07s |
| Total Markdown chars | 11,421 |
| Tables | 23 |
| Table structure issues | 0 |
| Remaining internal tokens | 0 |
| Unbalanced code fences | 0 |

The LLM path was only material for one scanned PDF:

- `20220406-1-0001-pdf.md`: 2 VLM OCR pages, 1,091 Markdown chars, 59.69s.
- All other documents had text/structure layers and completed without meaningful LLM work.

## Per Document

| Document | Format | Runtime | Markdown chars | Tables | Notes |
| --- | --- | ---: | ---: | ---: | --- |
| `20120330-2-0001` | PDF | 0.18s | 572 | 1 | Cover letter; contains a low-value empty signature-area table. |
| `20120330-2-0001` | HWP | 0.001s | 2,543 | 3 | Attachment/Q&A content extracted well; title emitted as one-cell table. |
| `20160829-2-0001` | PDF | 0.05s | 526 | 0 | Cover letter; no table issues. |
| `20160829-2-0001` | HWP | 0.0005s | 345 | 2 | Attachment/Q&A content extracted well; title emitted as one-cell table. |
| `20231226-1-0001` | PDF | 0.15s | 2,004 | 2 | Large PDF table is valid GFM but semantically flattened. |
| `20231226-1-0001` | HWPX | 0.003s | 1,079 | 1 | 신구조문 대비표 structure is better preserved than PDF. |
| `20220406-1-0001` | PDF | 59.69s | 1,091 | 0 | Scanned cover letter OCR. Mostly useful, but has confirmed OCR errors. |
| `20220406-1-0001` | HWPX | 0.004s | 3,261 | 14 | Attachment content and nested table separation preserved. |

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

6. Some non-content layout artifacts remain.
   `20120330-2-0001-pdf.md` contains an empty 2-column table from the signature/seal area. Several HWP/HWPX title boxes are represented as one-cell tables rather than headings.

## Recommended Next Changes

1. Add a post-processing filter for empty or low-density PDF tables.
   This should remove signature-area artifacts like `|  |  |` without affecting real content tables.

2. Improve title-like one-cell table rendering.
   One-cell tables with no data rows and short title text should become a paragraph or heading instead of GFM.

3. Add OCR quality checks for high-risk fields.
   Postal codes, dates, document numbers, and Korean administrative location names should be compared against simple regex/lexicon checks and flagged when suspicious.

4. Re-run LLM OCR with `ocr_workers=2`.
   The current LLM OCR check used `ocr_workers=1`; scanned PDF OCR is the runtime bottleneck and should benefit from parallel page processing if the endpoint tolerates concurrent requests.
