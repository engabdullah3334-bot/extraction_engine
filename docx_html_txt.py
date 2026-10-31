# -*- coding: utf-8 -*-
"""
extraction_engine/docx_html_txt.py
==================================
Format-specific extractors for DOCX, HTML/HTM, TXT, and Markdown (.md),
plus document-level metadata & language detection.
"""

from __future__ import annotations

import re
import shutil
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .ocr_engine import apply_ocr_to_image_blocks
from .utils import has_arabic, normalize


# ══════════════════════════════════════════════════════════════════════════════
# 1. DOCX Extractor
# ══════════════════════════════════════════════════════════════════════════════


def extract_docx_to_pages(
    docx_path: Path,
    enable_ocr: bool = False,
    lang: str = "ara",
    media_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Extract a `.docx` file into unified pages with headings, paragraphs, tables, and media."""
    from docx import Document as DocxDocument
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.table import Table as _Table
    from docx.text.paragraph import Paragraph as _Para
    from PIL import Image

    def _save_blob(blob: bytes) -> str | None:
        if media_dir is None:
            return None
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
            img = Image.open(BytesIO(blob))
            out_path = media_dir / f"img_docx_{uuid.uuid4().hex[:8]}.png"
            img.convert("RGB").save(str(out_path), "PNG")
            return str(out_path.resolve())
        except Exception:
            return None

    def _para_align(para: Any) -> str:
        al = para.alignment
        if al == WD_ALIGN_PARAGRAPH.CENTER:
            return "center"
        if al == WD_ALIGN_PARAGRAPH.RIGHT:
            return "right"
        if al == WD_ALIGN_PARAGRAPH.JUSTIFY:
            return "justify"
        return "left"

    def _para_to_blocks(para: Any, doc_part: Any) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for drawing in para._p.findall(".//" + qn("w:drawing")):
            blip = drawing.find(".//" + qn("a:blip"))
            if blip is None:
                continue
            r_embed = blip.get(qn("r:embed"))
            if r_embed is None:
                continue
            try:
                img_part = doc_part.related_parts[r_embed]
                saved_p = _save_blob(img_part.blob)
                if saved_p:
                    blocks.append(
                        {
                            "type": "inline_cropped_image",
                            "image_path": saved_p,
                            "bbox": [0, 0, 0, 0],
                            "source": "docx_inline_image",
                        }
                    )
            except (KeyError, AttributeError):
                pass

        text = para.text.strip()
        if not text:
            return blocks

        style_name = (para.style.name or "") if para.style else ""
        role = "body"
        heading_level = None
        base_size = 12.0

        low_style = style_name.lower()
        if low_style.startswith("heading") or low_style == "title":
            role = "heading"
            if low_style == "title":
                heading_level = 1
            else:
                try:
                    heading_level = int(style_name.split()[-1])
                except ValueError:
                    heading_level = 1
            base_size = max(11.0, 22.0 - (heading_level - 1) * 2.5)
        elif "list" in low_style or text.startswith(("•", "- ", "* ")):
            role = "list_item"

        spans: list[dict[str, Any]] = []
        for run in para.runs:
            rt = run.text
            if not rt:
                continue
            fsize = run.font.size.pt if (run.font.size is not None) else base_size
            spans.append(
                {
                    "text": normalize(rt),
                    "font": run.font.name or "Default",
                    "size": round(fsize, 2),
                    "color": 0,
                    "bold": bool(run.bold),
                    "italic": bool(run.italic),
                }
            )
        if not spans:
            spans = [{"text": normalize(text), "font": "Default", "size": base_size, "color": 0}]

        blk: dict[str, Any] = {
            "type": "text",
            "bbox": [0, 0, 0, 0],
            "alignment": _para_align(para),
            "margin_bottom_lines": 1.0,
            "role": role,
            "spans": spans,
        }
        if heading_level is not None:
            blk["heading_level"] = heading_level

        blocks.insert(0, blk)
        return blocks

    doc = DocxDocument(str(docx_path))
    doc_part = doc.part
    logical_page_size = 50
    pages: list[dict[str, Any]] = []
    current_blocks: list[dict[str, Any]] = []
    page_num = 1

    def _flush_page() -> None:
        nonlocal page_num
        if current_blocks:
            pages.append(
                {
                    "metadata": {
                        "page_number": page_num,
                        "width": 595.28,
                        "height": 841.89,
                        "source_format": "docx",
                    },
                    "blocks": list(current_blocks),
                }
            )
            current_blocks.clear()
            page_num += 1

    has_explicit_breaks = bool(doc.element.body.findall(".//" + qn("w:br")))

    for child in doc.element.body:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local == "p":
            for br in child.findall(".//" + qn("w:br")):
                if br.get(qn("w:type")) == "page":
                    _flush_page()
                    break
            para = _Para(child, doc)
            current_blocks.extend(_para_to_blocks(para, doc_part))
            if not has_explicit_breaks and len(current_blocks) >= logical_page_size:
                _flush_page()
        elif local == "tbl":
            try:
                tbl = _Table(child, doc)
                rows_data = []
                for r_idx, row in enumerate(tbl.rows):
                    cells = [
                        {"text": normalize(cell.text.strip()), "is_header": (r_idx == 0)}
                        for cell in row.cells
                    ]
                    if cells:
                        rows_data.append(cells)
                if rows_data:
                    current_blocks.append(
                        {
                            "type": "table",
                            "bbox": [0, 0, 0, 0],
                            "rows": rows_data,
                            "col_count": max((len(r) for r in rows_data), default=0),
                        }
                    )
            except Exception:
                pass
        elif local == "sectPr":
            _flush_page()

    _flush_page()
    if not pages:
        pages.append(
            {
                "metadata": {
                    "page_number": 1,
                    "width": 595.28,
                    "height": 841.89,
                    "source_format": "docx",
                },
                "blocks": [],
            }
        )

    if enable_ocr:
        apply_ocr_to_image_blocks(pages, lang=lang, media_dir=media_dir)

    return pages


# ══════════════════════════════════════════════════════════════════════════════
# 2. HTML / HTM Extractor
# ══════════════════════════════════════════════════════════════════════════════


def extract_html_to_pages(
    html_path: Path,
    enable_ocr: bool = False,
    lang: str = "ara",
    media_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Extract an HTML document into unified pages separating headings, paragraphs, lists, and tables."""
    from lxml import html as lxml_html

    raw = html_path.read_bytes()
    try:
        parser = lxml_html.HTMLParser(encoding="utf-8")
        tree = lxml_html.fromstring(raw, parser=parser)
    except Exception:
        tree = lxml_html.fromstring(raw)

    doc_meta: dict[str, str] = {}
    for meta in tree.xpath("//meta"):
        name = meta.get("name") or meta.get("property") or ""
        content = meta.get("content") or ""
        if name and content:
            doc_meta[name.lower()] = content
    title_el = tree.find(".//title")
    if title_el is not None and title_el.text:
        doc_meta["title"] = title_el.text.strip()

    def _align(elem: Any) -> str:
        style = (elem.get("style") or "").lower().replace(" ", "")
        dir_attr = (elem.get("dir") or "").lower()
        if "text-align:center" in style:
            return "center"
        if "text-align:right" in style or dir_attr == "rtl":
            return "right"
        return "left"

    def _txt(elem: Any) -> str:
        return normalize(" ".join(elem.itertext()).strip())

    def _copy_local_img(src: str) -> str:
        if src.startswith(("http://", "https://", "data:", "ftp://")):
            return src
        for cand in (html_path.parent / src, html_path.parent / unquote(src)):
            if cand.exists() and cand.is_file():
                if media_dir is not None:
                    media_dir.mkdir(parents=True, exist_ok=True)
                    dest = media_dir / f"img_html_{uuid.uuid4().hex[:8]}{cand.suffix or '.png'}"
                    shutil.copy2(str(cand), str(dest))
                    return str(dest.resolve())
                return str(cand.resolve())
        return src

    all_blocks: list[dict[str, Any]] = []
    skip_tags = {"script", "style", "head", "meta", "link", "noscript", "svg", "canvas", "iframe"}

    def _walk(elem: Any) -> None:
        if not isinstance(elem.tag, str):
            return
        tag = elem.tag.split("}")[-1].lower()
        if tag in skip_tags:
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            t = _txt(elem)
            if t:
                lvl = int(tag[1])
                all_blocks.append(
                    {
                        "type": "text",
                        "bbox": [0, 0, 0, 0],
                        "alignment": _align(elem),
                        "margin_bottom_lines": 1.5,
                        "role": "heading",
                        "heading_level": lvl,
                        "spans": [{"text": t, "font": "Default", "size": 22.0 - (lvl - 1) * 2.5, "bold": True}],
                    }
                )
            return

        if tag == "p":
            t = _txt(elem)
            if t:
                all_blocks.append(
                    {
                        "type": "text",
                        "bbox": [0, 0, 0, 0],
                        "alignment": _align(elem),
                        "margin_bottom_lines": 1.0,
                        "role": "body",
                        "spans": [{"text": t, "font": "Default", "size": 12.0}],
                    }
                )
            for ch in elem:
                if isinstance(ch.tag, str) and ch.tag.lower() == "img":
                    _walk(ch)
            return

        if tag == "li":
            t = _txt(elem)
            if t:
                all_blocks.append(
                    {
                        "type": "text",
                        "bbox": [0, 0, 0, 0],
                        "alignment": _align(elem),
                        "margin_bottom_lines": 0.5,
                        "role": "list_item",
                        "spans": [{"text": "• " + t, "font": "Default", "size": 12.0}],
                    }
                )
            return

        if tag == "table":
            rows_data: list[list[dict[str, Any]]] = []
            for tr in elem.xpath(".//tr"):
                cells = [
                    {
                        "text": _txt(td),
                        "is_header": td.tag.lower() == "th",
                    }
                    for td in tr.xpath("td|th")
                ]
                if cells:
                    rows_data.append(cells)
            if rows_data:
                all_blocks.append(
                    {
                        "type": "table",
                        "bbox": [0, 0, 0, 0],
                        "rows": rows_data,
                        "col_count": max(len(r) for r in rows_data),
                    }
                )
            return

        if tag == "img":
            src = elem.get("src") or ""
            alt = elem.get("alt") or ""
            if src:
                all_blocks.append(
                    {
                        "type": "inline_cropped_image",
                        "image_path": _copy_local_img(src),
                        "bbox": [0, 0, 0, 0],
                        "alt_text": alt,
                    }
                )
            return

        for child in elem:
            _walk(child)

    body = tree.find(".//body")
    root_node = body if body is not None else tree
    for child in root_node:
        _walk(child)

    logical_page_size = 120
    pages: list[dict[str, Any]] = []
    for i, start in enumerate(range(0, max(len(all_blocks), 1), logical_page_size)):
        chunk = all_blocks[start : start + logical_page_size]
        meta: dict[str, Any] = {
            "page_number": i + 1,
            "width": 793.7,
            "height": 1122.5,
            "source_format": "html",
        }
        if i == 0 and doc_meta:
            meta["doc_meta"] = doc_meta
        pages.append({"metadata": meta, "blocks": chunk})

    if enable_ocr:
        apply_ocr_to_image_blocks(pages, lang=lang, media_dir=media_dir)

    return pages


# ══════════════════════════════════════════════════════════════════════════════
# 3. Plain Text (.txt) & Markdown (.md) Extractor
# ══════════════════════════════════════════════════════════════════════════════

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_MD_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?(\s*\|\s*:?-{2,}:?)+\s*\|?$")


def extract_text_or_markdown_to_pages(file_path: Path) -> list[dict[str, Any]]:
    """
    Parse `.txt` or `.md` files into unified blocks separating Markdown headings,
    GFM tables, list items, and paragraphs!
    """
    raw_text = file_path.read_text(encoding="utf-8", errors="replace")
    lines = raw_text.splitlines()
    blocks: list[dict[str, Any]] = []

    i = 0
    n = len(lines)
    para_buf: list[str] = []

    def _flush_para() -> None:
        if not para_buf:
            return
        joined = normalize(" ".join(para_buf).strip())
        para_buf.clear()
        if joined:
            blocks.append(
                {
                    "type": "text",
                    "role": "body",
                    "alignment": "right" if has_arabic(joined) else "left",
                    "bbox": [0, 0, 0, 0],
                    "spans": [{"text": joined, "font": "Default", "size": 12.0}],
                }
            )

    while i < n:
        line = lines[i].strip()
        if not line:
            _flush_para()
            i += 1
            continue

        # Markdown Heading (# .. ######)
        m_head = _MD_HEADING_RE.match(line)
        if m_head:
            _flush_para()
            lvl = len(m_head.group(1))
            htxt = normalize(m_head.group(2).strip())
            blocks.append(
                {
                    "type": "text",
                    "role": "heading",
                    "heading_level": lvl,
                    "alignment": "right" if has_arabic(htxt) else "left",
                    "bbox": [0, 0, 0, 0],
                    "spans": [{"text": htxt, "font": "Default", "size": 22.0 - lvl * 2, "bold": True}],
                }
            )
            i += 1
            continue

        # Markdown Table
        if "|" in line and (i + 1 < n) and _MD_TABLE_SEP_RE.match(lines[i + 1].strip()):
            _flush_para()
            header_cells = [
                {"text": normalize(c.strip()), "is_header": True}
                for c in line.strip("|").split("|")
            ]
            rows_data: list[list[dict[str, Any]]] = [header_cells]
            i += 2  # skip header and separator
            while i < n and "|" in lines[i].strip():
                row_cells = [
                    {"text": normalize(c.strip()), "is_header": False}
                    for c in lines[i].strip().strip("|").split("|")
                ]
                rows_data.append(row_cells)
                i += 1
            blocks.append(
                {
                    "type": "table",
                    "bbox": [0, 0, 0, 0],
                    "rows": rows_data,
                    "col_count": max((len(r) for r in rows_data), default=0),
                }
            )
            continue

        # Bullet / Numbered List item
        if line.startswith(("- ", "* ", "• ")):
            _flush_para()
            ltxt = normalize(line)
            blocks.append(
                {
                    "type": "text",
                    "role": "list_item",
                    "alignment": "right" if has_arabic(ltxt) else "left",
                    "bbox": [0, 0, 0, 0],
                    "spans": [{"text": ltxt, "font": "Default", "size": 12.0}],
                }
            )
            i += 1
            continue

        para_buf.append(line)
        i += 1

    _flush_para()

    logical_page_size = 80
    fmt = file_path.suffix.lower().lstrip(".") or "txt"
    pages: list[dict[str, Any]] = []
    for p_idx, start in enumerate(range(0, max(len(blocks), 1), logical_page_size)):
        pages.append(
            {
                "metadata": {
                    "page_number": p_idx + 1,
                    "width": 595.28,
                    "height": 841.89,
                    "source_format": fmt,
                },
                "blocks": blocks[start : start + logical_page_size],
            }
        )
    return pages


# ══════════════════════════════════════════════════════════════════════════════
# 4. Document Metadata & Language Detector
# ══════════════════════════════════════════════════════════════════════════════


def detect_document_metadata(
    file_path: Path, pages_json: list[dict[str, Any]]
) -> dict[str, Any]:
    """Extract `author`, `doc_date`, and primary `language` (`ara`, `eng`, `ara+eng`)."""
    author: str | None = None
    doc_date: str | None = None
    ext = file_path.suffix.lower()

    try:
        if ext == ".pdf":
            import fitz

            doc = fitz.open(str(file_path))
            meta = doc.metadata or {}
            author = (meta.get("author") or "").strip() or None
            raw_date = (meta.get("creationDate") or meta.get("modDate") or "").strip()
            if raw_date.startswith("D:") and len(raw_date) >= 10:
                doc_date = f"{raw_date[2:6]}-{raw_date[6:8]}-{raw_date[8:10]}"
            elif raw_date:
                doc_date = raw_date[:32]
            doc.close()
        elif ext == ".docx":
            from docx import Document as DocxDocument

            doc = DocxDocument(str(file_path))
            props = doc.core_properties
            if props:
                author = (props.author or "").strip() or None
                if props.created:
                    doc_date = props.created.strftime("%Y-%m-%d")
                elif props.modified:
                    doc_date = props.modified.strftime("%Y-%m-%d")
        elif ext in (".html", ".htm") and pages_json:
            doc_meta = pages_json[0].get("metadata", {}).get("doc_meta", {})
            author = doc_meta.get("author") or doc_meta.get("article:author") or None
            doc_date = doc_meta.get("date") or doc_meta.get("article:published_time") or None
    except Exception:
        pass

    ara_chars = 0
    lat_chars = 0
    for pg in pages_json[:15]:
        for b in pg.get("blocks", []):
            for s in b.get("spans", []):
                for ch in s.get("text", ""):
                    if "\u0600" <= ch <= "\u06FF" or "\u0750" <= ch <= "\u08FF":
                        ara_chars += 1
                    elif "A" <= ch <= "Z" or "a" <= ch <= "z":
                        lat_chars += 1
            if b.get("type") == "table":
                for row in b.get("rows", []):
                    for cell in row:
                        ctxt = cell.get("text", "") if isinstance(cell, dict) else str(cell)
                        for ch in ctxt:
                            if "\u0600" <= ch <= "\u06FF":
                                ara_chars += 1
                            elif "A" <= ch <= "Z" or "a" <= ch <= "z":
                                lat_chars += 1

    total_alpha = ara_chars + lat_chars
    if total_alpha == 0:
        language = "ara" if has_arabic(file_path.name) else "eng"
    else:
        ara_ratio = ara_chars / total_alpha
        if ara_ratio >= 0.70:
            language = "ara"
        elif ara_ratio <= 0.15:
            language = "eng"
        else:
            language = "ara+eng"

    return {
        "author": author,
        "doc_date": doc_date,
        "language": language,
    }

