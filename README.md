# md-converter

HWP / HWPX / PDF → Markdown converter in pure Python.

Ports the document-level traversal logic from [rhwp](https://github.com/JeekLee/rhwp): paragraphs and tables are walked directly in document order, so cross-page tables appear exactly once in the output.

## Features

- **HWPX** parsing — pure stdlib, zero runtime dependencies
- **HWP5** (binary OLE) parsing — requires `olefile`
- **PDF** parsing — text + tables via `pdfplumber`; scanned pages via vision OCR
- Tables → GFM; merged cells (colspan) handled via `rowAddr`
- **Nested tables** (a table inside a cell) → extracted as a separate GFM table with a `→ 표 N` reference left in the parent cell — **no LLM**, identical output across HWP / HWPX / PDF (see [Nested tables](#nested-tables))
- Drawing objects (text boxes, shapes) → plain text labels or Mermaid (see [Drawing objects](#drawing-objects))
- Scanned PDF pages → text via a vision LLM (OCR); PDF diagram regions → Mermaid
- Image extraction from HWP5 / HWPX / PDF (PNG / JPEG / GIF / BMP→PNG via Pillow)
- Image upload to S3 / MinIO via AWS Signature V4 (no boto3) or save to a local directory

Requires Python 3.11+.

## Install

```bash
# HWPX only (zero deps)
pip install md-converter

# + HWP5 support
pip install "md-converter[hwp5]"

# + PDF support (pdfplumber, pypdf, pymupdf, Pillow)
pip install "md-converter[pdf]"

# everything
pip install "md-converter[hwp5,images,pdf]"
```

Extras: `hwp5` (olefile), `images` (Pillow, for BMP→PNG), `pdf` (pdfplumber + pypdf + pymupdf + Pillow), `pdf-ocr` (adds `pytesseract` as a fallback OCR engine for scanned PDFs when no vision LLM is reachable).

## Usage

`llm` is optional. It is only *invoked* for diagram/drawing → Mermaid conversion and for scanned-PDF OCR; plain text, plain tables, images, and nested tables need no LLM.

```python
from md_converter import MdConverter, S3Config, LocalImages, LlmConfig, conversion_plan

converter = MdConverter(
    llm=LlmConfig(
        url="http://localhost:10080/v1",
        api_key="sk-...",
        model="qwen3-vl-30b-a3b",   # a vision model is needed for diagrams / scanned-PDF OCR
    ),
    images=LocalImages("output/images"),  # optional; default = drop images
)

md = converter.convert("document.hwp")            # file path
md = converter.convert("document.hwpx")
md = converter.convert("document.pdf")
md = converter.convert(raw_bytes, suffix=".pdf")  # bytes need an explicit suffix
```

`convert()` returns a single Markdown `str`. Supported suffixes: `.hwp`, `.hwpx`, `.pdf`.

For crawlers, use `profile()` before scheduling work or `convert_with_metadata()`
when storing conversion results:

```python
converter = MdConverter(
    llm=LlmConfig(url="http://localhost:10080/v1", api_key="sk-...", model="vision-model"),
    ocr_workers=2,
)

profile = converter.profile(raw_bytes, suffix=".pdf")
plan = conversion_plan(profile)
queue = plan["queue"]

result = converter.convert_with_metadata(
    raw_bytes,
    suffix=".pdf",
    raise_errors=False,
    source_id="post-123/attachment-2",
    source_url="https://example.go.kr/notice/123",
    source_name="첨부파일.pdf",
)
if result.error is None:
    metadata = result.to_dict()
    save_markdown(result.markdown, metadata=metadata)
    # Each quality warning includes type, severity, line, excerpt, and reason.
    if metadata["quality_warning_counts"]["by_severity"].get("high", 0):
        queue_for_review(metadata)
    if result.ocr_failed_pages:
        queue_for_review(metadata)
elif result.error_info and result.error_info.retryable:
    retry_later(result.to_dict())
else:
    save_failure(result.to_dict())
```

## Nested tables

When a table cell contains another table, the inner table is **extracted as a standalone GFM table** placed right after the parent table block, and the parent cell keeps a `→ 표 N` reference. This is pure string/structure processing — **no LLM call** — and produces the same output whether the source is HWP, HWPX, or PDF.

```markdown
| 구분     | 세부내용 |
| --- | --- |
| 본인부담 | → 표 1   |
| 수가     | 5,000원  |

**[표 1]**

| 항목 | 금액     |
| --- | --- |
| 외래 | 1,000원  |
| 입원 | 2,000원  |
```

Numbering is a single document-wide counter (`표 1`, `표 2`, …). One level of nesting becomes a separate table; tables nested two or more levels deep are flattened to text inside their parent. For PDF, this works for tables whose nesting is recoverable from ruling lines (pdfplumber detects the inner table); borderless aligned-text "tables" are not split.

## Image backends

### Local directory

```python
converter = MdConverter(llm=llm_cfg, images=LocalImages("images"))
```

Images are written to `images/img0001.png`, `images/img0002.jpg`, etc.
The markdown embeds them as `![image 1](images/img0001.png)`.
Set `dir` relative to wherever you intend to write the markdown file.

### S3 / MinIO

```python
converter = MdConverter(
    llm=llm_cfg,
    images=S3Config(
        endpoint   = "http://localhost:9000",
        bucket     = "my-bucket",
        access_key = "minioadmin",
        secret_key = "minioadmin",
        prefix     = "docs",        # optional key prefix
    ),
)
```

Images are uploaded and embedded as `![image 1](s3://bucket/key)`.
Signing uses AWS Signature Version 4 via stdlib `hmac` + `hashlib` — no boto3 required.

### Drop images (default)

```python
converter = MdConverter()  # images=None, llm=None
```

Image placeholders are removed from the output.

## Drawing objects

HWP/HWPX drawing objects (GSO in HWP5; `hp:rect`, `hp:ellipse`, `hp:line`, etc. in HWPX) are handled as follows:

- **Embedded image** — extracted as a normal image (see Image backends above).
- **Text box with a single label** — emitted as a plain text paragraph. This covers the common case of decorative section banners and standalone caption boxes.
- **Text box with multiple labels** (e.g. a group of shapes in one paragraph) — the LLM is called to produce a Mermaid diagram. On failure, the raw labels are kept as plain text.

For PDF, a cluster of vector rectangles not covered by a table is rendered to an image and sent to the vision LLM for Mermaid conversion.

### Known limitation

HWP renders each shape and each arrow as a separate drawing object. Connection lines carry no text, so the edge structure of a flowchart cannot be recovered from text extraction alone. Multi-label Mermaid conversion is therefore best-effort: the LLM sees only the label set, not which node connects to which.

## PDF notes

- **Text PDFs** (e.g. exported from HWP/HWPX): text, tables, and embedded images are extracted; tables render as GFM and adjacent overflow / continuation / duplicate tables are merged.
- **Scanned PDFs** (no text layer): each page image is sent to the vision LLM for OCR (`vision_to_text`). Install `[pdf-ocr]` to enable a `pytesseract` fallback when no vision LLM is reachable.
- **Nested tables** inside cells are separated like HWP/HWPX (see [Nested tables](#nested-tables)).

## Config reference

### `LlmConfig`

| Field | Type | Description |
| --- | --- | --- |
| `url` | `str` | Base URL of an OpenAI-compatible `/v1` endpoint |
| `api_key` | `str` | Bearer token |
| `model` | `str` | Model ID (use a vision-capable model for diagrams / scanned-PDF OCR) |

Used for: drawing/diagram → Mermaid conversion and scanned-PDF OCR. **Not** used for tables or nested tables. On LLM failure, the original flat content is kept as a fallback.

### `LocalImages`

| Field | Type | Description |
| --- | --- | --- |
| `dir` | `str \| Path` | Directory for saved image files |

### `S3Config`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `endpoint` | `str` | — | Base URL, e.g. `"http://localhost:9000"` |
| `bucket` | `str` | — | Bucket name |
| `access_key` | `str` | — | AWS / MinIO access key |
| `secret_key` | `str` | — | AWS / MinIO secret key |
| `prefix` | `str` | `""` | Optional key prefix |
| `region` | `str` | `"us-east-1"` | AWS region |

## Development benchmark

When `clic-minio` is available locally, run the fixed 4-pair / 8-document
benchmark:

```bash
uv run python scripts/benchmark_clic_minio.py
```

It reports conversion-time medians and Markdown quality counters, and writes
converted Markdown files under `/tmp/md-converter-clic-bench/latest`.

To include scanned-PDF OCR and diagram VLM calls, provide `LLM_BASE_URL`,
`LLM_API_KEY`, and `VLM_MODEL` via an env file or flags:

```bash
uv run python scripts/benchmark_clic_minio.py --env-file /path/to/.env --runs 1 --ocr-workers 2
```
