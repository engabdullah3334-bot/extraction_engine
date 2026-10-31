# -*- coding: utf-8 -*-
"""
extraction_engine/utils.py
==========================
Format-agnostic Arabic/Unicode text helpers and Markdown composers.
Zero external dependencies.
"""

from __future__ import annotations

import unicodedata
from typing import Any

SUPPORTED_FORMATS: frozenset[str] = frozenset(
    {".pdf", ".docx", ".html", ".htm", ".txt", ".md"}
)


def has_arabic(text: str) -> bool:
    """Return True if `text` contains Arabic or extended-Arabic codepoints."""
    return any(
        "\u0600" <= c <= "\u06FF"
        or "\u0750" <= c <= "\u077F"
        or "\u08A0" <= c <= "\u08FF"
        for c in text
    )


def normalize(text: str) -> str:
    """NFKC-normalize a string safely."""
    return unicodedata.normalize("NFKC", text) if text else ""


def extract_block_text(block: dict[str, Any]) -> str:
    """Extract clean plain text from a unified block dict."""
    if block.get("text"):
        return str(block["text"]).strip()
    spans = block.get("spans") or []
    if spans:
        return " ".join(str(s.get("text", "")) for s in spans if s.get("text")).strip()
    return ""


def format_table_markdown(rows: list[list[Any]], col_count: int | None = None) -> str:
    """Render structured table rows into standard GitHub-Flavored Markdown."""
    if not rows:
        return ""
    cols = col_count or max((len(r) for r in rows), default=0)
    if cols <= 0:
        return ""

    md_lines: list[str] = []
    for r_idx, row in enumerate(rows):
        cells = [
            (c.get("text", "") if isinstance(c, dict) else str(c or ""))
            .replace("\n", " ")
            .replace("|", "\\|")
            .strip()
            for c in row
        ]
        while len(cells) < cols:
            cells.append("")
        md_lines.append("| " + " | ".join(cells[:cols]) + " |")
        if r_idx == 0:
            md_lines.append("| " + " | ".join(["---"] * cols) + " |")
    return "\n".join(md_lines)


def compose_unified_markdown(pages_json: list[dict[str, Any]]) -> str:
    """Convert unified `pages` layout into a clean Markdown document."""
    md_parts: list[str] = []

    for page in pages_json:
        p_num = page.get("metadata", {}).get("page_number", 1)
        blocks = page.get("blocks", [])
        if not blocks:
            continue

        page_lines: list[str] = []
        for b in blocks:
            b_type = b.get("type", "text")
            role = b.get("role", "body")

            if b_type in ("inline_cropped_image", "full_page_image_or_index"):
                img_path = b.get("image_path", "")
                alt_text = b.get("alt_text") or f"Image on page {p_num}"
                if img_path:
                    page_lines.append(f"![{alt_text}]({img_path})")
                continue

            if b_type == "table":
                tbl_md = format_table_markdown(b.get("rows", []), b.get("col_count"))
                if tbl_md:
                    page_lines.append(tbl_md)
                continue

            text = extract_block_text(b)
            if not text:
                continue

            if role == "heading" or b_type in ("heading", "header") or b.get("is_heading"):
                lvl = max(1, min(int(b.get("heading_level") or 2), 6))
                page_lines.append(f"{'#' * lvl} {text}")
            elif role == "list_item":
                page_lines.append(text if text.startswith(("•", "-", "*")) else f"- {text}")
            else:
                page_lines.append(text)

        if page_lines:
            md_parts.append(f"<!-- Page {p_num} -->\n\n" + "\n\n".join(page_lines))

    return "\n\n---\n\n".join(md_parts)

