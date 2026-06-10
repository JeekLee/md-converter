"""GFM table utilities for PDF extraction — ported from md_table_merge.py."""
from __future__ import annotations

import re


# ── raw cell → GFM cell text ──────────────────────────────────────────────────

def _cell_text(cell: str | None) -> str:
    if cell is None:
        return ""
    # Remove CJK-adjacent spaces that pdfplumber inserts between chars
    text = re.sub(r"(?<=[　-鿿가-힣])\s+(?=[　-鿿가-힣])", "", cell)
    return text.replace("|", "\\|").replace("\n", " ").strip()


# ── GFM table rendering ───────────────────────────────────────────────────────

def table_to_md(rows: list[list[str | None]]) -> str:
    """Convert a pdfplumber row list to a GFM markdown table string."""
    if not rows:
        return ""
    col_count = max(len(r) for r in rows)
    norm = []
    for row in rows:
        cells = [_cell_text(c) for c in row]
        while len(cells) < col_count:
            cells.append("")
        norm.append(cells)

    header = "| " + " | ".join(norm[0]) + " |"
    sep = "| " + " | ".join("---" for _ in range(col_count)) + " |"
    body = ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join([header, sep] + body)


# ── merge-overflow helpers (operate on rendered GFM strings) ──────────────────

def _col_count(table_md: str) -> int:
    first = table_md.splitlines()[0] if table_md else ""
    if not first.strip():
        return 0
    return max(0, len(first.split("|")) - 2)


def _header_cells(table_md: str) -> list[str]:
    first = table_md.splitlines()[0] if table_md else ""
    return [c.strip() for c in first.split("|")[1:-1]]


def _clean_table_block(block: list[str]) -> list[str]:
    """Remove duplicate sub-header rows and merge wrapped cell lines."""
    if len(block) < 3:
        return list(block)
    sub_hdr = block[2].strip()
    result: list[str] = list(block[:3])
    for row in block[3:]:
        if row.strip() == sub_hdr:
            continue
        parts = row.split("|")
        if len(parts) >= 3 and not parts[1].strip() and result:
            prev = result[-1].split("|")
            if len(prev) == len(parts):
                merged_cells = [prev[0]]
                for pp, cp in zip(prev[1:-1], parts[1:-1]):
                    if cp.strip():
                        merged_cells.append(
                            (pp.rstrip() + " " + cp.strip()) if pp.strip() else cp.strip()
                        )
                    else:
                        merged_cells.append(pp)
                merged_cells.append(prev[-1])
                result[-1] = "|".join(merged_cells)
                continue
        result.append(row)
    return result


def merge_overflow_tables(md: str) -> str:
    """Merge adjacent overflow / continuation / duplicate GFM tables.

    - overflow:    B has more columns and the first N headers match A → append overflow values to A
    - exact_duplicate: identical header+data → drop B
    - continuation: same header, separated by blank/image lines → concatenate data rows
    - subtable:    B headers already appear inside A's last cell → drop B
    """
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            out.append(lines[i])
            i += 1
            continue

        block_a: list[str] = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            block_a.append(lines[i])
            i += 1

        gap: list[str] = []
        j = i
        while j < len(lines) and (
            not lines[j].strip() or lines[j].strip().startswith("![")
        ):
            gap.append(lines[j])
            j += 1

        if j < len(lines) and lines[j].lstrip().startswith("|"):
            block_b: list[str] = []
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                block_b.append(lines[j])
                j += 1

            na = _col_count("\n".join(block_a))
            nb = _col_count("\n".join(block_b))
            ha = _header_cells("\n".join(block_a))
            hb = _header_cells("\n".join(block_b))
            tb_data = [l for l in block_b[2:] if l.strip()]

            is_overflow = (
                nb > na and na >= 2 and
                len(ha) == na and len(hb) == nb and
                hb[:na] == ha and bool(ha) and
                all(c == ha[-1] for c in hb[na:]) and
                bool(tb_data) and
                all(
                    all(not c.strip() for c in row.split("|")[1:-1][:na - 1])
                    for row in tb_data
                )
            )

            a_data = [l.strip() for l in block_a[2:] if l.strip()]
            b_data = [l.strip() for l in block_b[2:] if l.strip()]
            is_exact_duplicate = (
                not is_overflow and
                nb == na and na >= 2 and
                ha == hb and bool(ha) and
                bool(a_data) and a_data == b_data
            )
            is_continuation = (
                not is_overflow and not is_exact_duplicate and
                nb == na and na >= 2 and
                ha == hb and bool(ha) and
                bool(gap) and bool(tb_data)
            )
            a_last_cell = block_a[-1].split("|")[1:-1][-1].strip() if block_a else ""
            is_subtable = (
                not is_overflow and not is_exact_duplicate and not is_continuation and
                nb > na and na >= 2 and
                bool(a_last_cell) and bool(hb) and
                sum(1 for h in hb[:3] if h and h in a_last_cell) >= 2
            )

            if is_overflow:
                overflow_vals = [
                    c.strip()
                    for row in tb_data
                    for c in row.split("|")[1:-1][na - 1:]
                    if c.strip()
                ]
                if overflow_vals:
                    cells = block_a[-1].split("|")
                    cells[-2] = cells[-2].rstrip() + " " + " ".join(overflow_vals)
                    block_a[-1] = "|".join(cells)
                out.extend(_clean_table_block(block_a))
                i = j
            elif is_exact_duplicate:
                out.extend(_clean_table_block(block_a))
                i = j
            elif is_continuation:
                merged = list(block_a) + list(block_b[2:])
                out.extend(_clean_table_block(merged))
                i = j
            elif is_subtable:
                out.extend(_clean_table_block(block_a))
                i = j
            else:
                out.extend(_clean_table_block(block_a))
                out.extend(gap)
                out.extend(_clean_table_block(block_b))
                i = j
        else:
            out.extend(_clean_table_block(block_a))
            out.extend(gap)
            i = j

    return "\n".join(out)
