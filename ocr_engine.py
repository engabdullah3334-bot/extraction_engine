# -*- coding: utf-8 -*-
"""
extraction_engine/ocr_engine.py
===============================
Three-layer Tesseract OCR engine decoupled from global config/database state:
  Layer 1: `ocr_pil_image_to_blocks` (format-agnostic PIL -> unified blocks)
  Layer 2A: `ocr_pdf_page_to_blocks` (`fitz.Page` -> PIL -> Layer 1)
  Layer 2B: `ocr_image_file_to_blocks` (image path -> PIL -> Layer 1)
  Layer 3: `apply_ocr_to_image_blocks` (replaces inline image blocks with OCR text)
"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Callable

from .utils import has_arabic, normalize


def is_scanned_page(page: Any, min_chars: int = 30) -> bool:
    """Return True when a PDF page lacks a usable digital text layer."""
    text = page.get_text("text")
    return len(text.replace(" ", "").replace("\n", "")) < min_chars


def classify_document(doc: Any, sample_size: int = 15) -> dict[str, Any]:
    """Sample up to `sample_size` pages to classify PDF as DIGITAL, SCANNED, or MIXED."""
    total = len(doc)
    if total == 0:
        return {
            "type": "DIGITAL",
            "total_pages": 0,
            "scanned_pages": 0,
            "digital_pages": 0,
            "scanned_ratio": 0.0,
        }

    indices = (
        list(range(total))
        if total <= sample_size
        else sorted({int(i * total / sample_size) for i in range(sample_size)})
    )
    scanned = sum(1 for idx in indices if is_scanned_page(doc[idx]))
    ratio = scanned / len(indices)

    if ratio >= 0.85:
        doc_type = "SCANNED"
    elif ratio <= 0.15:
        doc_type = "DIGITAL"
    else:
        doc_type = "MIXED"

    return {
        "type": doc_type,
        "total_pages": total,
        "scanned_pages": round(ratio * total),
        "digital_pages": total - round(ratio * total),
        "scanned_ratio": round(ratio, 3),
    }


def ocr_pil_image_to_blocks(
    pil_img: Any,
    page_w: float,
    page_h: float,
    lang: str = "ara",
    dpi: int = 300,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> list[dict[str, Any]]:
    """Core Tesseract OCR adapter returning unified text blocks."""
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:
        raise ImportError(
            "pytesseract is required for OCR. Install with `pip install pytesseract`."
        ) from exc

    zoom = dpi / 72.0

    try:
        tsv = pytesseract.image_to_data(
            pil_img,
            lang=lang,
            config="--psm 3 --oem 1",
            output_type=Output.DICT,
        )
    except pytesseract.TesseractError as exc:
        if "ara" in lang and any(
            k in str(exc).lower() for k in ("lang", "data file", "ara")
        ):
            tsv = pytesseract.image_to_data(
                pil_img,
                lang="eng",
                config="--psm 3 --oem 1",
                output_type=Output.DICT,
            )
        else:
            raise

    n_words = len(tsv["text"])
    blk_dict: dict[tuple[int, int], dict[int, list[dict[str, Any]]]] = {}

    for idx in range(n_words):
        word = (tsv["text"][idx] or "").strip()
        conf = int(tsv["conf"][idx])
        if tsv["block_num"][idx] == 0 or conf == -1 or not word:
            continue

        px0, py0 = tsv["left"][idx], tsv["top"][idx]
        pw, ph = tsv["width"][idx], tsv["height"][idx]
        bx0 = round(offset_x + px0 / zoom, 4)
        by0 = round(offset_y + py0 / zoom, 4)
        bx1 = round(offset_x + (px0 + pw) / zoom, 4)
        by1 = round(offset_y + (py0 + ph) / zoom, 4)

        entry = {
            "text": normalize(word),
            "bbox": [bx0, by0, bx1, by1],
            "confidence": conf,
        }
        key = (tsv["block_num"][idx], tsv["par_num"][idx])
        blk_dict.setdefault(key, {}).setdefault(tsv["line_num"][idx], []).append(entry)

    blocks: list[dict[str, Any]] = []
    for blk_key in sorted(blk_dict):
        lines_data = blk_dict[blk_key]
        all_words = [w for ln in lines_data.values() for w in ln]
        if not all_words:
            continue

        bx0 = min(w["bbox"][0] for w in all_words)
        by0 = min(w["bbox"][1] for w in all_words)
        bx1 = max(w["bbox"][2] for w in all_words)
        by1 = max(w["bbox"][3] for w in all_words)

        ml, mr = bx0, page_w - bx1
        if abs(ml - mr) < page_w * 0.1:
            align = "center"
        elif mr < ml:
            align = "right"
        else:
            align = "left"

        all_confs: list[int] = []
        block_lines: list[str] = []
        for ln_num in sorted(lines_data):
            line_words = lines_data[ln_num]
            block_lines.append(" ".join(w["text"] for w in line_words))
            all_confs.extend(w["confidence"] for w in line_words)

        full_text = " ".join(block_lines)
        avg_conf = round(sum(all_confs) / max(len(all_confs), 1), 1)

        blocks.append(
            {
                "type": "text",
                "role": "body",
                "bbox": [bx0, by0, bx1, by1],
                "alignment": align,
                "margin_bottom_lines": 0.0,
                "ocr": True,
                "spans": [
                    {
                        "text": full_text,
                        "font": "Traditional Arabic" if has_arabic(full_text) else "Default",
                        "size": 14.0,
                        "color": 0,
                        "confidence": avg_conf,
                    }
                ],
            }
        )

    blocks.sort(key=lambda b: b["bbox"][1])
    for i, blk in enumerate(blocks[:-1]):
        gap = blocks[i + 1]["bbox"][1] - blk["bbox"][3]
        avg_sz = blk["spans"][0]["size"] if blk["spans"] else 14.0
        blk["margin_bottom_lines"] = round(gap / avg_sz, 4) if avg_sz > 0 else 0.0

    return blocks


def ocr_pdf_page_to_blocks(
    page: Any,
    page_number: int,
    lang: str = "ara",
    dpi: int = 300,
    clip: Any = None,
) -> dict[str, Any]:
    """Rasterize a `fitz.Page` (or region) to grayscale PIL image and OCR it."""
    import fitz
    import numpy as np
    from PIL import Image

    width = page.rect.width
    height = page.rect.height
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    pix = (
        page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, clip=clip)
        if clip is not None
        else page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    )
    img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    pil_img = Image.fromarray(img_np)

    offset_x = clip.x0 if clip is not None else 0.0
    offset_y = clip.y0 if clip is not None else 0.0

    blocks = ocr_pil_image_to_blocks(
        pil_img,
        width,
        height,
        lang=lang,
        dpi=dpi,
        offset_x=offset_x,
        offset_y=offset_y,
    )

    all_confs = [s["confidence"] for b in blocks for s in b.get("spans", [])]
    avg_conf = round(sum(all_confs) / max(len(all_confs), 1), 1) if all_confs else 0.0

    del pix, img_np, pil_img
    gc.collect()

    return {
        "metadata": {
            "page_number": page_number,
            "width": round(width, 2),
            "height": round(height, 2),
            "source_format": "pdf_ocr",
            "ocr_lang": lang,
            "ocr_confidence_avg": avg_conf,
        },
        "blocks": blocks,
    }


def ocr_image_file_to_blocks(
    image_path: str | Path,
    lang: str = "ara",
    dpi: int = 300,
) -> list[dict[str, Any]]:
    """Open an image file on disk and extract OCR text blocks."""
    from PIL import Image

    pil_img = Image.open(str(image_path)).convert("L")
    w_px, h_px = pil_img.size
    pt_w = w_px / (dpi / 72.0)
    pt_h = h_px / (dpi / 72.0)

    blocks = ocr_pil_image_to_blocks(pil_img, pt_w, pt_h, lang=lang, dpi=dpi)
    del pil_img
    gc.collect()
    return blocks


def apply_ocr_to_image_blocks(
    pages: list[dict[str, Any]],
    lang: str = "ara",
    media_dir: Path | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """Replace `inline_cropped_image` blocks with OCR text blocks where OCR succeeds."""
    total = sum(
        1
        for pg in pages
        for b in pg.get("blocks", [])
        if b.get("type") == "inline_cropped_image" and b.get("image_path")
    )
    done = 0

    for page_dict in pages:
        new_blocks: list[dict[str, Any]] = []
        for blk in page_dict.get("blocks", []):
            if blk.get("type") == "inline_cropped_image" and blk.get("image_path"):
                raw_p = Path(blk["image_path"])
                img_path = raw_p if raw_p.exists() else (
                    (media_dir / raw_p.name) if media_dir else raw_p
                )
                if img_path.exists():
                    if progress_cb:
                        progress_cb(done, total, img_path.name)
                    try:
                        ocr_blocks = ocr_image_file_to_blocks(img_path, lang=lang)
                    except Exception:
                        ocr_blocks = []
                    done += 1
                    if ocr_blocks:
                        new_blocks.extend(ocr_blocks)
                        continue
            new_blocks.append(blk)
        page_dict["blocks"] = new_blocks

    return pages

