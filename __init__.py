# -*- coding: utf-8 -*-
"""
extraction_engine
=================
Standalone document extraction module separating texts, headings, tables, and media.

Usage:
    from extraction_engine import ExtractionEngine

    extractor = ExtractionEngine(enable_ocr=True, ocr_lang="ara")
    doc = extractor.extract("safe_zone/document.pdf")
"""

from .engine import ExtractionEngine
from .schemas import (
    DocumentMetadata,
    ExtractedDocument,
    HeadingItem,
    MediaItem,
    TableItem,
    TextItem,
    build_structured_document,
)
from .utils import SUPPORTED_FORMATS, compose_unified_markdown, has_arabic, normalize

__all__ = [
    "ExtractionEngine",
    "ExtractedDocument",
    "DocumentMetadata",
    "HeadingItem",
    "TextItem",
    "TableItem",
    "MediaItem",
    "build_structured_document",
    "SUPPORTED_FORMATS",
    "compose_unified_markdown",
    "has_arabic",
    "normalize",
]

