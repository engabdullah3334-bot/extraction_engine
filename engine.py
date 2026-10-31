# -*- coding: utf-8 -*-
"""
extraction_engine/engine.py
===========================
`ExtractionEngine` — Standalone Document Extraction Facade.

Input:
    Safe file path (`str | Path | SecurityResult`).
Output:
    `ExtractedDocument` structured object cleanly separating:
      - `doc.texts`    (paragraphs & list items)
      - `doc.headings` (hierarchical section headings)
      - `doc.tables`   (structured headers, rows, & Markdown)
      - `doc.media`    (extracted images/figures)
      - `doc.pages`    (unified reading-order page layout)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .docx_html_txt import (
    detect_document_metadata,
    extract_docx_to_pages,
    extract_html_to_pages,
    extract_text_or_markdown_to_pages,
)
from .ocr_engine import classify_document
from .pdf_extractor import (
    restore_deferred_blocks,
    run_deferred_ocr,
    smart_extract_pdf,
)
from .schemas import DocumentMetadata, ExtractedDocument, build_structured_document
from .utils import SUPPORTED_FORMATS, has_arabic


class ExtractionEngine:
    """
    Standalone Document Extraction Engine.

    Usage:
        from extraction_engine import ExtractionEngine

        extractor = ExtractionEngine(enable_ocr=True, ocr_lang="ara")
        doc = extractor.extract(safe_file_path)
        print(len(doc.headings), len(doc.texts), len(doc.tables))
    """

    def __init__(
        self,
        enable_ocr: bool = True,
        ocr_lang: str = "ara",
        media_dir: str | Path | None = None,
    ) -> None:
        self.enable_ocr = enable_ocr
        self.ocr_lang = ocr_lang
        default_media = Path(__file__).resolve().parent / "extracted_media"
        self.media_dir = Path(media_dir).resolve() if media_dir else default_media.resolve()

    def extract(
        self,
        file_path: str | Path | os.PathLike[str],
        enable_ocr: bool | None = None,
        ocr_lang: str | None = None,
        media_dir: str | Path | None = None,
        page_range: tuple[int, int] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> ExtractedDocument:
        """
        Extract any supported document (`.pdf`, `.docx`, `.html`, `.htm`, `.txt`, `.md`)
        into a structured `ExtractedDocument` object.
        """
        path = Path(os.fspath(file_path)).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Target file does not exist: '{path}'")

        ext = path.suffix.lower()
        if ext not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported format '{ext}'. Supported formats: {sorted(SUPPORTED_FORMATS)}"
            )

        use_ocr = self.enable_ocr if enable_ocr is None else enable_ocr
        lang = ocr_lang or self.ocr_lang
        active_media_dir = Path(media_dir).resolve() if media_dir else self.media_dir

        if ext == ".pdf":
            import fitz

            doc = fitz.open(str(path))
            try:
                if not ocr_lang:
                    sample_ara = has_arabic(path.name) or any(
                        has_arabic(doc[p].get_text("text"))
                        for p in range(min(5, len(doc)))
                    )
                    lang = "ara" if sample_ara else "eng"

                classification = classify_document(doc, sample_size=15)
                pg_start = page_range[0] if page_range else 1
                pg_end = page_range[1] if page_range else len(doc)

                def _pdf_cb(step: int, total: int, _pn: int, _doc_len: int) -> None:
                    if progress_callback:
                        progress_callback(step + 1, total)

                pages, deferred = smart_extract_pdf(
                    doc,
                    path,
                    classification,
                    enable_ocr=use_ocr,
                    lang=lang,
                    pg_start=pg_start,
                    pg_end=pg_end,
                    media_dir=active_media_dir,
                    progress_cb=_pdf_cb,
                )
                if deferred and use_ocr:
                    pages = run_deferred_ocr(pages, deferred, doc, lang=lang)
                elif deferred:
                    pages = restore_deferred_blocks(pages)
            finally:
                doc.close()

        elif ext == ".docx":
            pages = extract_docx_to_pages(
                path, enable_ocr=use_ocr, lang=lang, media_dir=active_media_dir
            )
            if progress_callback:
                progress_callback(len(pages), len(pages))

        elif ext in (".html", ".htm"):
            pages = extract_html_to_pages(
                path, enable_ocr=use_ocr, lang=lang, media_dir=active_media_dir
            )
            if progress_callback:
                progress_callback(len(pages), len(pages))

        else:  # .txt, .md
            pages = extract_text_or_markdown_to_pages(path)
            if progress_callback:
                progress_callback(len(pages), len(pages))

        detected = detect_document_metadata(path, pages)
        doc_meta = DocumentMetadata(
            file_name=path.name,
            source_path=str(path),
            source_format=ext.lstrip("."),
            total_pages=len(pages),
            language=detected["language"],
            author=detected["author"],
            doc_date=detected["doc_date"],
        )
        return build_structured_document(pages, doc_meta)

