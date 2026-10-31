# -*- coding: utf-8 -*-
"""
extraction_engine/pdf_extractor.py
==================================
PDF Digital Layout + Native Table Detection + OpenCV Visual QA Fallback + Deferred OCR.
100% standalone — zero global config or SQLite imports.
"""

from __future__ import annotations

import gc
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .ocr_engine import is_scanned_page, ocr_pdf_page_to_blocks
from .utils import normalize


def _bboxes_overlap(b1: list[float], b2: list[float], min_ratio: float = 0.65) -> bool:
    """Return True if `b1` lies mostly inside `b2` (used to avoid duplicating table text)."""
    ix0 = max(b1[0], b2[0])
    iy0 = max(b1[1], b2[1])
    ix1 = min(b1[2], b2[2])
    iy1 = min(b1[3], b2[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter_area = (ix1 - ix0) * (iy1 - iy0)
    b1_area = max((b1[2] - b1[0]) * (b1[3] - b1[1]), 1e-6)
    return (inter_area / b1_area) >= min_ratio


def extract_page_json(page: Any, page_number: int) -> dict[str, Any]:
    """
    Extract digital text blocks, inferred headings, and structured PDF tables
    from a single `fitz.Page`.
    """
    _PUA_START = 0xE000
    _PUA_END = 0xF8FF
    _REPL = "\uFFFD"

    def _is_pua(t: str) -> bool:
        return bool(t) and sum(_PUA_START <= ord(c) <= _PUA_END for c in t) / len(t) > 0.5

    def _is_garbage(t: str) -> bool:
        return all(c == _REPL for c in t)

    width = page.rect.width
    height = page.rect.height
    result: dict[str, Any] = {
        "metadata": {
            "page_number": page_number,
            "width": round(width, 2),
            "height": round(height, 2),
        },
        "blocks": [],
    }

    # 1. Native PyMuPDF Table Detection (when available)
    table_blocks: list[dict[str, Any]] = []
    table_bboxes: list[list[float]] = []
    if hasattr(page, "find_tables"):
        try:
            tabs = page.find_tables()
            for t in getattr(tabs, "tables", []):
                extracted = t.extract()
                if not extracted or len(extracted) < 2:
                    continue
                rows_data: list[list[dict[str, Any]]] = []
                for r_idx, row in enumerate(extracted):
                    cells = [
                        {
                            "text": normalize(str(cell or "").strip()),
                            "is_header": (r_idx == 0),
                        }
                        for cell in row
                    ]
                    if any(c["text"] for c in cells):
                        rows_data.append(cells)
                if rows_data:
                    tb_bbox = [round(c, 4) for c in t.bbox]
                    table_bboxes.append(tb_bbox)
                    table_blocks.append(
                        {
                            "type": "table",
                            "bbox": tb_bbox,
                            "rows": rows_data,
                            "col_count": max((len(r) for r in rows_data), default=0),
                        }
                    )
        except Exception:
            pass

    # 2. Digital Text Blocks & Heading Inference
    page_dict = page.get_text("dict", flags=0)
    raw_blocks = [b for b in page_dict.get("blocks", []) if b.get("type") == 0]
    all_blocks = sorted(raw_blocks, key=lambda b: b.get("bbox", (0, 0, 0, 0))[1])
    n = len(all_blocks)

    all_page_sizes = [
        round(sp.get("size", 0.0), 1)
        for b in all_blocks
        for ln in b.get("lines", [])
        for sp in ln.get("spans", [])
        if sp.get("size", 0.0) > 0 and sp.get("text", "").strip()
    ]
    modal_size = Counter(all_page_sizes).most_common(1)[0][0] if all_page_sizes else 12.0

    combined_blocks: list[dict[str, Any]] = list(table_blocks)

    for i, blk in enumerate(all_blocks):
        bbox = [round(c, 4) for c in blk.get("bbox", (0, 0, 0, 0))]
        if any(_bboxes_overlap(bbox, tb_box) for tb_box in table_bboxes):
            continue

        x0, y0, x1, y1 = bbox
        ml, mr = x0, width - x1
        if abs(ml - mr) < width * 0.1:
            align = "center"
        elif mr < ml:
            align = "right"
        else:
            align = "left"

        spans: list[dict[str, Any]] = []
        for line in blk.get("lines", []):
            for span in line.get("spans", []):
                raw = span.get("text", "").strip()
                if not raw or _is_garbage(raw):
                    continue

                flags = span.get("flags", 0)
                font_lower = (span.get("font") or "").lower()
                is_bold = bool(flags & 16) or "bold" in font_lower
                is_italic = bool(flags & 2) or "italic" in font_lower

                span_entry: dict[str, Any] = {
                    "bbox": [round(c, 4) for c in span.get("bbox")],
                    "font": span.get("font", ""),
                    "size": round(span.get("size", 0.0), 4),
                    "color": span.get("color", 0),
                    "bold": is_bold,
                    "italic": is_italic,
                }
                if _is_pua(raw):
                    span_entry["text"] = "[PUA]"
                    span_entry["encoding_issue"] = True
                else:
                    span_entry["text"] = normalize(raw)
                spans.append(span_entry)

        if not spans:
            continue

        avg_sz = sum(s["size"] for s in spans) / len(spans)
        gap = 0.0
        if i < n - 1:
            ny0 = all_blocks[i + 1].get("bbox", (0, y1, 0, 0))[1]
            v = ny0 - y1
            if v > 0 and avg_sz > 0:
                gap = round(v / avg_sz, 4)

        full_blk_text = " ".join(s.get("text", "") for s in spans if s.get("text")).strip()
        word_cnt = len(full_blk_text.split())
        all_bold = all(s.get("bold", False) for s in spans)

        role = "body"
        heading_level = None
        if 0 < word_cnt <= 22:
            if modal_size > 0 and avg_sz >= modal_size * 1.30:
                role = "heading"
                heading_level = 1
            elif modal_size > 0 and avg_sz >= modal_size * 1.15:
                role = "heading"
                heading_level = 2
            elif (all_bold or align == "center") and word_cnt <= 14 and avg_sz >= modal_size:
                role = "heading"
                heading_level = 3

        blk_dict: dict[str, Any] = {
            "type": "text",
            "bbox": bbox,
            "alignment": align,
            "margin_bottom_lines": gap,
            "role": role,
            "spans": spans,
        }
        if heading_level is not None:
            blk_dict["heading_level"] = heading_level

        combined_blocks.append(blk_dict)

    combined_blocks.sort(key=lambda b: b.get("bbox", [0, 0, 0, 0])[1])
    result["blocks"] = combined_blocks
    return result


def verify_and_fallback_layout(
    page: Any,
    json_blocks: list[dict[str, Any]],
    file_stem: str,
    media_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Validate digital text coverage using OpenCV; crop missing/garbled visual
    regions to image files inside `media_dir` when needed.
    """
    try:
        import cv2
        import fitz
        import numpy as np
    except ImportError:
        # If OpenCV is not installed in a minimal environment, return digital blocks directly
        return json_blocks

    width = page.rect.width
    height = page.rect.height
    zoom = 150 / 72.0
    mat = fitz.Matrix(zoom, zoom)

    pix = page.get_pixmap(matrix=mat)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, binary_real = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)
    binary_json = np.zeros_like(binary_real)

    for block in json_blocks:
        bx0, by0, bx1, by1 = block.get("bbox", [0, 0, 0, 0])
        cv2.rectangle(
            binary_json,
            (int(bx0 * zoom), int(by0 * zoom)),
            (int(bx1 * zoom), int(by1 * zoom)),
            255,
            -1,
        )

    total_real_text = cv2.countNonZero(binary_real)
    mapped_text = cv2.bitwise_and(binary_real, binary_json)
    page_coverage = (
        cv2.countNonZero(mapped_text) / total_real_text if total_real_text > 0 else 1.0
    )

    def _is_block_failed(block: dict[str, Any]) -> bool:
        if block.get("type") == "table":
            return False
        for span in block.get("spans", []):
            txt = span.get("text", "")
            if "\uFFFD" in txt or span.get("encoding_issue", False):
                return True

        bx0, by0, bx1, by1 = block.get("bbox", [0, 0, 0, 0])
        x0_px, y0_px = max(0, int(bx0 * zoom)), max(0, int(by0 * zoom))
        x1_px = min(binary_real.shape[1], int(bx1 * zoom))
        y1_px = min(binary_real.shape[0], int(by1 * zoom))

        if x1_px > x0_px and y1_px > y0_px:
            block_real_mask = np.zeros_like(binary_real)
            cv2.rectangle(block_real_mask, (x0_px, y0_px), (x1_px, y1_px), 255, -1)
            block_text_pixels = cv2.bitwise_and(binary_real, block_real_mask)
            total_real_in_block = cv2.countNonZero(block_text_pixels)
            if total_real_in_block > 20:
                spans_mask = np.zeros_like(binary_real)
                for span in block.get("spans", []):
                    sx0, sy0, sx1, sy1 = span.get("bbox", [0, 0, 0, 0])
                    cv2.rectangle(
                        spans_mask,
                        (int(sx0 * zoom), int(sy0 * zoom)),
                        (int(sx1 * zoom), int(sy1 * zoom)),
                        255,
                        -1,
                    )
                overlap = cv2.bitwise_and(block_text_pixels, spans_mask)
                if (cv2.countNonZero(overlap) / total_real_in_block) < 0.85:
                    return True
        return False

    def _save_crop(x0: float, y0: float, x1: float, y1: float) -> str | None:
        if media_dir is None:
            return None
        rect = fitz.Rect(max(0, x0), max(0, y0), min(width, x1), min(height, y1))
        if rect.is_empty or rect.is_infinite:
            return None
        media_dir.mkdir(parents=True, exist_ok=True)
        crop_pix = page.get_pixmap(matrix=mat, clip=rect)
        filename = f"fallback_{file_stem}_{uuid.uuid4().hex[:8]}.png"
        out_path = media_dir / filename
        crop_pix.save(str(out_path))
        return str(out_path.resolve())

    text_blocks = [b for b in json_blocks if b.get("type") != "table"]
    garbled_count = sum(1 for b in text_blocks if _is_block_failed(b))
    garbled_ratio = garbled_count / len(text_blocks) if text_blocks else 0.0

    updated_blocks: list[dict[str, Any]] = []
    if (page_coverage < 0.85 or garbled_ratio > 0.5) and not any(
        b.get("type") == "table" for b in json_blocks
    ):
        img_path = _save_crop(0, 0, width, height)
        if img_path:
            updated_blocks.append(
                {
                    "type": "full_page_image_or_index",
                    "image_path": img_path,
                    "bbox": [0, 0, round(width, 4), round(height, 4)],
                }
            )
        else:
            updated_blocks.extend(json_blocks)
    else:
        for block in json_blocks:
            if _is_block_failed(block):
                img_path = _save_crop(*block.get("bbox", [0, 0, 0, 0]))
                if img_path:
                    updated_blocks.append(
                        {
                            "type": "inline_cropped_image",
                            "image_path": img_path,
                            "bbox": block["bbox"],
                        }
                    )
                else:
                    updated_blocks.append(block)
            else:
                updated_blocks.append(block)

        orphaned_mask = cv2.bitwise_and(binary_real, cv2.bitwise_not(binary_json))
        contours, _ = cv2.findContours(
            orphaned_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w > 15 and h > 15 and (w * h) > 300:
                img_path = _save_crop(x / zoom, y / zoom, (x + w) / zoom, (y + h) / zoom)
                if img_path:
                    updated_blocks.append(
                        {
                            "type": "inline_cropped_image",
                            "image_path": img_path,
                            "bbox": [
                                round(x / zoom, 4),
                                round(y / zoom, 4),
                                round((x + w) / zoom, 4),
                                round((y + h) / zoom, 4),
                            ],
                        }
                    )

    del img, gray, binary_real, binary_json, pix
    gc.collect()
    return updated_blocks


def smart_extract_pdf(
    doc: Any,
    file_path: Path,
    classification: dict[str, Any],
    enable_ocr: bool = True,
    lang: str = "ara",
    pg_start: int = 1,
    pg_end: int | None = None,
    media_dir: Path | None = None,
    progress_cb: Callable[[int, int, int, int], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract PDF pages intelligently across DIGITAL, SCANNED, and MIXED layouts."""
    if pg_end is None:
        pg_end = len(doc)

    pages_json: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    doc_type = classification["type"]
    file_stem = file_path.stem
    num = pg_end - pg_start + 1

    for step, pn in enumerate(range(pg_start, pg_end + 1)):
        page = doc[pn - 1]
        if progress_cb:
            progress_cb(step, num, pn, len(doc))

        page_is_scanned = is_scanned_page(page)
        if doc_type == "SCANNED" or page_is_scanned:
            if enable_ocr:
                try:
                    page_data = ocr_pdf_page_to_blocks(page, pn, lang=lang)
                except ImportError:
                    page_data = extract_page_json(page, pn)
            else:
                page_data = {
                    "metadata": {
                        "page_number": pn,
                        "width": round(page.rect.width, 2),
                        "height": round(page.rect.height, 2),
                        "source_format": "pdf_image_only",
                    },
                    "blocks": verify_and_fallback_layout(page, [], file_stem, media_dir),
                }
                if page_data["blocks"]:
                    deferred.append(
                        {
                            "page_number": pn,
                            "page_idx": pn - 1,
                            "block": page_data["blocks"][0],
                            "reason": "scanned_page",
                        }
                    )
        else:
            page_data = extract_page_json(page, pn)
            page_data["blocks"] = verify_and_fallback_layout(
                page, page_data["blocks"], file_stem, media_dir
            )

            new_blocks: list[dict[str, Any]] = []
            for blk in page_data["blocks"]:
                btype = blk.get("type", "")
                if btype in ("full_page_image_or_index", "inline_cropped_image"):
                    deferred.append(
                        {
                            "page_number": pn,
                            "page_idx": pn - 1,
                            "block": blk,
                            "reason": btype,
                        }
                    )
                    new_blocks.append(
                        {
                            "type": "deferred_ocr",
                            "image_path": blk.get("image_path", ""),
                            "bbox": blk.get("bbox", [0, 0, 0, 0]),
                            "page_number": pn,
                            "reason": btype,
                            "original_block": blk,
                        }
                    )
                else:
                    new_blocks.append(blk)
            page_data["blocks"] = new_blocks

        pages_json.append(page_data)
        gc.collect()

    return pages_json, deferred


def run_deferred_ocr(
    pages_json: list[dict[str, Any]],
    deferred: list[dict[str, Any]],
    doc: Any,
    lang: str = "ara",
    progress_cb: Callable[[int, int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Run regional or full-page OCR on deferred image blocks."""
    import fitz

    by_page: dict[int, list[dict[str, Any]]] = {}
    for d in deferred:
        by_page.setdefault(d["page_number"], []).append(d)

    total_pages_to_ocr = len(by_page)
    for step, (pn, _) in enumerate(sorted(by_page.items())):
        if progress_cb:
            progress_cb(step, total_pages_to_ocr, pn)

        page = doc[pn - 1]
        page_dict = next(
            (p for p in pages_json if p.get("metadata", {}).get("page_number") == pn),
            None,
        )
        if page_dict is None:
            continue

        new_blocks: list[dict[str, Any]] = []
        for blk in page_dict["blocks"]:
            if blk.get("type") == "deferred_ocr":
                original = blk.get("original_block", {})
                reason = blk.get("reason", "")
                try:
                    if reason == "scanned_page" or original.get("type") == "full_page_image_or_index":
                        ocr_res = ocr_pdf_page_to_blocks(page, pn, lang=lang)
                        new_blocks.extend(ocr_res["blocks"])
                    else:
                        bbox = original.get("bbox", [0, 0, 0, 0])
                        rect = fitz.Rect(*bbox)
                        ocr_res = ocr_pdf_page_to_blocks(page, pn, lang=lang, clip=rect)
                        new_blocks.extend(ocr_res["blocks"])
                except ImportError:
                    new_blocks.append(original)
            else:
                new_blocks.append(blk)

        page_dict["blocks"] = sorted(
            new_blocks, key=lambda b: b.get("bbox", [0, 0, 0, 0])[1]
        )
        gc.collect()

    return pages_json


def restore_deferred_blocks(pages_json: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Restore original cropped image blocks when OCR is disabled."""
    for page_dict in pages_json:
        new_blocks: list[dict[str, Any]] = []
        for blk in page_dict.get("blocks", []):
            if blk.get("type") == "deferred_ocr" and "original_block" in blk:
                new_blocks.append(blk["original_block"])
            else:
                new_blocks.append(blk)
        page_dict["blocks"] = new_blocks
    return pages_json

