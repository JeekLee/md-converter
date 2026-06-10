# md-converter

HWP / HWPX → Markdown converter in pure Python.

Ports the document-level traversal logic from [rhwp](https://github.com/JeekLee/rhwp): paragraphs and tables are walked directly in document order, so cross-page tables appear exactly once in the output.

## Features

- **HWPX** parsing — pure stdlib, zero runtime dependencies
- **HWP5** (binary OLE) parsing — requires `olefile`
- Tables → GFM tables; multi-paragraph cells joined with `<br>`
- Nested tables → `[[NT:...]]` serialized for LLM restructuring
- Image extraction from HWPX (PNG / JPEG / GIF / BMP→PNG via Pillow)
- Image upload to S3 / MinIO via AWS Signature V4 (no boto3)
- LLM-based `[[NT:...]]` restructuring via any OpenAI-compatible endpoint

## Install

```bash
# HWPX only (zero deps)
pip install md-converter

# + HWP5 support
pip install "md-converter[hwp5]"

# + BMP→PNG image conversion
pip install "md-converter[images]"

# all extras
pip install "md-converter[hwp5,images]"
```

## Quick start

```python
from pathlib import Path
from md_converter import hwpx_to_md, hwp5_to_md

# text + tables only (images dropped)
md = hwpx_to_md(Path("document.hwpx").read_bytes())
md = hwp5_to_md(Path("document.hwp").read_bytes())   # requires olefile
```

## Full pipeline

`convert()` handles the complete flow: parse → image upload/save → LLM nested-table restructuring.

```python
from pathlib import Path
from md_converter import convert, S3Config, LlmConfig

data   = Path("document.hwpx").read_bytes()
suffix = ".hwpx"   # or ".hwp"

# --- Option A: upload images to MinIO / S3 ---
s3 = S3Config(
    endpoint   = "http://localhost:9000",
    bucket     = "my-bucket",
    access_key = "minioadmin",
    secret_key = "minioadmin",
    prefix     = "docs",          # optional key prefix
)
md = convert(data, suffix, s3_config=s3)
# images embedded as:  ![image 1](s3://my-bucket/docs/img0001.png)

# --- Option B: save images locally ---
md = convert(data, suffix, output_dir="./out")
# images saved to ./out/img0001.png and embedded as:  ![image 1](img0001.png)

# --- Option C: drop images (default) ---
md = convert(data, suffix)

# --- LLM restructuring for nested tables ---
llm = LlmConfig(
    url     = "http://localhost:10080/v1",   # OpenAI-compatible endpoint
    api_key = "sk-...",
    model   = "qwen3-vl-30b-a3b",
)
md = convert(data, suffix, s3_config=s3, llm_config=llm)
```

### S3Config

| Field | Type | Description |
| --- | --- | --- |
| `endpoint` | `str` | Base URL, e.g. `"http://localhost:9000"` |
| `bucket` | `str` | Bucket name |
| `access_key` | `str` | AWS / MinIO access key |
| `secret_key` | `str` | AWS / MinIO secret key |
| `prefix` | `str` | Optional key prefix (default `""`) |
| `region` | `str` | AWS region (default `"us-east-1"`) |

Signing uses AWS Signature Version 4 via stdlib `hmac` + `hashlib` — no boto3 required.

### LlmConfig

| Field | Type | Description |
| --- | --- | --- |
| `url` | `str` | Base URL of an OpenAI-compatible `/v1` endpoint |
| `api_key` | `str` | Bearer token |
| `model` | `str` | Model ID |

Cells containing nested tables are serialized as `[[NT:row1c1|c2;row2c1|c2]]`. When `llm_config` is provided, each marker is sent to the LLM and replaced with naturally formatted prose (using `<br>` for in-cell line breaks). On failure the flat marker text is kept as a fallback.

## Notes

- **Image support**: HWPX only. HWP5 image extraction is not yet implemented.
- **Dependencies**: `olefile` is needed only for HWP5; `Pillow` is needed only for BMP→PNG conversion. HWPX text+tables works with zero extra packages.
- **MinIO path-style**: the S3 client always uses path-style URLs (`/{bucket}/{key}`), which is required for MinIO.
