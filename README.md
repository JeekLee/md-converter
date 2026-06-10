# md-converter

HWP / HWPX → Markdown converter in pure Python.

Ports the document-level traversal logic from [rhwp](https://github.com/JeekLee/rhwp): paragraphs and tables are walked directly in document order, so cross-page tables appear exactly once in the output.

## Features

- **HWPX** parsing — pure stdlib, zero runtime dependencies
- **HWP5** (binary OLE) parsing — requires `olefile`
- Tables → GFM; merged cells (colspan) handled via `rowAddr`
- Nested tables → LLM restructuring via any OpenAI-compatible endpoint
- Drawing objects (text boxes, shapes) → plain text labels or Mermaid (see [Drawing objects](#drawing-objects))
- Image extraction from HWP5 and HWPX (PNG / JPEG / GIF / BMP→PNG via Pillow)
- Image upload to S3 / MinIO via AWS Signature V4 (no boto3)
- Image save to local directory

## Install

```bash
# HWPX only (zero deps)
pip install md-converter

# + HWP5 support
pip install "md-converter[hwp5]"

# + BMP→PNG image conversion
pip install "md-converter[images]"
```

## Usage

`llm` is a required argument — `MdConverter` raises `TypeError` if omitted.

```python
from md_converter import MdConverter, S3Config, LocalImages, LlmConfig

converter = MdConverter(
    llm=LlmConfig(
        url="http://localhost:10080/v1",
        api_key="sk-...",
        model="qwen3-vl-30b-a3b",
    ),
    images=LocalImages("output/images"),  # optional
)

md = converter.convert("document.hwp")        # file path
md = converter.convert("document.hwpx")
md = converter.convert(raw_bytes, suffix=".hwp")  # bytes
```

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
converter = MdConverter(llm=llm_cfg)  # images=None
```

Image placeholders are removed from the output.

## Drawing objects

HWP/HWPX drawing objects (GSO in HWP5; `hp:rect`, `hp:ellipse`, `hp:line`, etc. in HWPX) are handled as follows:

- **Embedded image** — extracted as a normal image (see Image backends above).
- **Text box with a single label** — emitted as a plain text paragraph. This covers the common case of decorative section banners and standalone caption boxes.
- **Text box with multiple labels** (e.g. a group of shapes in one paragraph) — LLM is called to produce a Mermaid diagram. On failure, the raw labels are kept as plain text.

### Known limitation

HWP renders each shape and each arrow as a separate drawing object. Connection lines carry no text, so the edge structure of a flowchart cannot be recovered from text extraction alone. Multi-label Mermaid conversion is therefore best-effort: the LLM sees only the label set, not which node connects to which.

## Config reference

### `LlmConfig`

| Field | Type | Description |
| --- | --- | --- |
| `url` | `str` | Base URL of an OpenAI-compatible `/v1` endpoint |
| `api_key` | `str` | Bearer token |
| `model` | `str` | Model ID |

Used for: nested-table restructuring, drawing → Mermaid conversion.
On LLM failure, the original flat content is kept as a fallback.

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
