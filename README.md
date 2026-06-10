# md-converter

HWP / HWPX → Markdown converter in pure Python.

Ports the document-level traversal logic from [rhwp](https://github.com/JeekLee/rhwp): paragraphs and tables are walked directly in document order, so cross-page tables appear exactly once in the output.

## Features

- **HWPX** parsing — pure stdlib, zero runtime dependencies
- **HWP5** (binary OLE) parsing — requires `olefile`
- Tables → GFM tables; multi-paragraph cells joined with `<br>`
- Nested tables → LLM restructuring via any OpenAI-compatible endpoint
- Image extraction from HWPX (PNG / JPEG / GIF / BMP→PNG via Pillow)
- Image upload to S3 / MinIO via AWS Signature V4 (no boto3)

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

```python
from pathlib import Path
from md_converter import convert, S3Config, LlmConfig

s3 = S3Config(
    endpoint   = "http://localhost:9000",
    bucket     = "my-bucket",
    access_key = "minioadmin",
    secret_key = "minioadmin",
    prefix     = "docs",        # optional key prefix
)

llm = LlmConfig(
    url     = "http://localhost:10080/v1",  # OpenAI-compatible endpoint
    api_key = "sk-...",
    model   = "qwen3-vl-30b-a3b",
)

data = Path("document.hwpx").read_bytes()  # or .hwp
md   = convert(data, ".hwpx", s3, llm)
```

`suffix` is `".hwpx"` or `".hwp"` (case-insensitive). Both formats go through the same entry point.

### What `convert()` does

1. **Parse** — extracts paragraphs, GFM tables, and image placeholders (`[[RHWP_IMAGE:N]]`) from the document
2. **Upload images** — each extracted image is PUT to S3/MinIO and the placeholder is replaced with `![image N](s3://bucket/key)`
3. **Restructure nested tables** — cells that contain a nested table are serialized as `[[NT:row1c1|c2;row2c1|c2]]` and sent to the LLM, which rewrites them as natural prose with `<br>` line breaks

### S3Config

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `endpoint` | `str` | — | Base URL, e.g. `"http://localhost:9000"` |
| `bucket` | `str` | — | Bucket name |
| `access_key` | `str` | — | AWS / MinIO access key |
| `secret_key` | `str` | — | AWS / MinIO secret key |
| `prefix` | `str` | `""` | Optional key prefix |
| `region` | `str` | `"us-east-1"` | AWS region |

Signing uses AWS Signature Version 4 via stdlib `hmac` + `hashlib` — no boto3 required. Always uses path-style URLs, which is required for MinIO.

### LlmConfig

| Field | Type | Description |
| --- | --- | --- |
| `url` | `str` | Base URL of an OpenAI-compatible `/v1` endpoint |
| `api_key` | `str` | Bearer token |
| `model` | `str` | Model ID |

On LLM failure the flat `[[NT:...]]` content is kept as a fallback.

## Notes

- **Image support**: HWPX only. HWP5 image extraction is not yet implemented.
- **Dependencies**: `olefile` for HWP5; `Pillow` for BMP→PNG. HWPX text+tables has zero extra dependencies.
