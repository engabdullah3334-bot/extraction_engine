# -*- coding: utf-8 -*-
"""
extraction_engine/exporters.py
==============================
Converts unified `pages` layout into styled RTL/LTR DOCX documents.
Preserves full complex-script Arabic styling (`w:pBidi`, `w:rtl`, `w:szCs`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import has_arabic


def convert_pages_to_docx(
    pages_data: list[dict[str, Any]],
    output_docx_path: str | Path,
) -> Path:
    """Export unified page-block JSON into a formatted Word `.docx` document."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    def _set_paragraph_rtl(paragraph: Any) -> None:
        pPr = paragraph._p.get_or_add_pPr()
        pBidi = OxmlElement("w:pBidi")
        pPr.append(pBidi)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    def _apply_text_styling(
        run: Any, font_name: str, size_pt: float, text_content: str
    ) -> None:
        run.font.name = font_name
        run.font.size = Pt(size_pt)
        rPr = run._r.get_or_add_rPr()
        rFonts = rPr.get_or_add_rFonts()
        rFonts.set(qn("w:hint"), "cs")
        if has_arabic(text_content):
            rtl_el = OxmlElement("w:rtl")
            rPr.append(rtl_el)
            rFonts.set(qn("w:cs"), font_name)
            szCs = OxmlElement("w:szCs")
            szCs.set(qn("w:val"), str(int(size_pt * 2)))
            rPr.append(szCs)

    doc = Document()
    for section in doc.sections:
        section.page_width = Inches(8.27)
        section.page_height = Inches(11.69)
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    for page_idx, page_entry in enumerate(pages_data):
        for block in page_entry.get("blocks", []):
            btype = block.get("type", "text")
            alignment = block.get("alignment", "right")
            role = block.get("role", "body")

            if btype in ("full_page_image_or_index", "inline_cropped_image"):
                img_rel = block.get("image_path")
                if img_rel:
                    img_path = Path(img_rel)
                    if img_path.exists():
                        p = doc.add_paragraph()
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        w = Inches(5.5) if btype == "full_page_image_or_index" else Inches(4.0)
                        p.add_run().add_picture(str(img_path), width=w)
                continue

            if btype == "table":
                rows_data = block.get("rows", [])
                if rows_data:
                    col_count = int(
                        block.get("col_count") or max((len(r) for r in rows_data), default=1)
                    )
                    tbl = doc.add_table(rows=0, cols=max(1, col_count))
                    tbl.style = "Table Grid"
                    for row_cells in rows_data:
                        row = tbl.add_row()
                        for ci, cell_data in enumerate(row_cells):
                            if ci < len(row.cells):
                                cell = row.cells[ci]
                                txt = (
                                    cell_data.get("text", "")
                                    if isinstance(cell_data, dict)
                                    else str(cell_data)
                                )
                                cell.text = txt
                                for cp in cell.paragraphs:
                                    if has_arabic(txt):
                                        _set_paragraph_rtl(cp)
                                        cp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                continue

            spans = block.get("spans") or []
            if not spans and block.get("text"):
                spans = [{"text": str(block["text"]), "font": "Traditional Arabic", "size": 14.0}]
            if not spans:
                continue

            p = doc.add_paragraph()
            p.paragraph_format.line_spacing = 1.25
            p.paragraph_format.space_before = Pt(0)

            margin_bottom = float(block.get("margin_bottom_lines", 0) or 0)
            para_has_arabic = any(has_arabic(str(s.get("text", ""))) for s in spans)

            if para_has_arabic or alignment in ("right", "center"):
                _set_paragraph_rtl(p)

            if alignment == "center":
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif alignment == "left":
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            elif alignment == "justify" or (para_has_arabic and role == "body"):
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

            max_size = 14.0
            for span in spans:
                fs = float(span.get("size", 14.0) or 14.0)
                if role == "heading":
                    lvl = int(block.get("heading_level", 1) or 1)
                    fs = max(16.0, 24.0 - lvl * 2)
                max_size = max(max_size, fs)

            p.paragraph_format.space_after = (
                Pt(max(6.0, margin_bottom * max_size)) if margin_bottom > 0 else Pt(8.0)
            )

            for span in spans:
                text_content = str(span.get("text", ""))
                font_name = str(span.get("font") or "Traditional Arabic")
                is_heading = role == "heading"
                font_size = float(span.get("size", 14.0) or 14.0)
                if is_heading:
                    lvl = int(block.get("heading_level", 1) or 1)
                    font_size = max(16.0, 24.0 - lvl * 2)

                run = p.add_run(text_content)
                _apply_text_styling(run, font_name, font_size, text_content)
                if span.get("bold", False) or is_heading:
                    run.bold = True
                if span.get("italic", False):
                    run.italic = True

        if page_idx < len(pages_data) - 1:
            doc.add_page_break()

    out_path = Path(output_docx_path).resolve()
    doc.save(str(out_path))
    return out_path

