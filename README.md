# md-converter

HWP / HWPX → Markdown converter in pure Python.

Mirrors the document-level traversal logic from [rhwp](https://github.com/JeekLee/rhwp)
(`extract_document_markdown_with_images_native`): paragraphs and tables are walked
directly in document order, so cross-page tables appear exactly once.

## Features

- **HWPX** — pure stdlib, no external dependencies
- **HWP5** — requires `olefile` for OLE compound-file reading
- Tables → GFM tables; nested tables → `[[NT:r0c0|c1;r1c0|c1]]`
- HWP control characters (object placeholders ≤ U+001F) are stripped

## Install

```bash
# HWPX only (no deps)
pip install md-converter

# HWP5 support
pip install "md-converter[hwp5]"
```

## Usage

```python
from md_converter import convert

md = convert(Path("document.hwpx").read_bytes(), ".hwpx")
md = convert(Path("document.hwp").read_bytes(),  ".hwp")

# or per-format
from md_converter import hwpx_to_md, hwp5_to_md
md = hwpx_to_md(data)
```
