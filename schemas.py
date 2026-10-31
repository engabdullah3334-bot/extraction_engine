# -*- coding: utf-8 -*-
"""
extraction_engine/schemas.py
============================
Structured Document Data Contracts for `ExtractionEngine`.

Cleanly separates:
  - `headings` (`list[HeadingItem]`)
  - `texts`    (`list[TextItem]` — paragraphs & list items)
  - `tables`   (`list[TableItem]` — headers, rows, and Markdown)
  - `media`    (`list[MediaItem]` — extracted images/figures)
while also preserving unified reading-order `pages` for downstream chunkers
and exporters (`to_markdown()`, `to_docx()`, `to_json()`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .utils import compose_unified_markdown, extract_block_text, format_table_markdown


@dataclass
class DocumentMetadata:
    file_name: str
    source_path: str
    source_format: str
    total_pages: int
    language: str = "ara"
    author: str | None = None
    doc_date: str | None = None
    extracted_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HeadingItem:
    index: int
    level: int
    text: str
    page_number: int
    section_path: str = ""
    alignment: str = "right"
    bbox: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TextItem:
    index: int
    role: str  # "paragraph" | "list_item"
    text: str
    page_number: int
    parent_heading: str | None = None
    section_path: str = ""
    alignment: str = "right"
    is_ocr: bool = False
    bbox: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TableItem:
    index: int
    col_count: int
    row_count: int
    headers: list[str]
    rows: list[list[str]]
    markdown: str
    page_number: int
    parent_heading: str | None = None
    bbox: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MediaItem:
    index: int
    media_name: str
    media_path: str
    media_type: str
    page_number: int
    alt_text: str = ""
    bbox: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedDocument:
    """
    Primary structured output of `ExtractionEngine`.
    Separates `headings`, `texts`, `tables`, and `media` for effortless access,
    and retains `pages` in exact reading order.
    """

    metadata: DocumentMetadata
    headings: list[HeadingItem] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)
    tables: list[TableItem] = field(default_factory=list)
    media: list[MediaItem] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def plain_text(self) -> str:
        """Concatenate document content cleanly in reading order."""
        return compose_unified_markdown(self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "headings": [h.to_dict() for h in self.headings],
            "texts": [t.to_dict() for t in self.texts],
            "tables": [tb.to_dict() for tb in self.tables],
            "media": [m.to_dict() for m in self.media],
            "pages": self.pages,
        }

    def to_json(self, output_path: str | Path | None = None, indent: int = 2) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
        if output_path is not None:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(payload, encoding="utf-8")
        return payload

    def to_markdown(self, output_path: str | Path | None = None) -> str:
        md = compose_unified_markdown(self.pages)
        if output_path is not None:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
        return md

    def to_docx(self, output_path: str | Path) -> Path:
        from .exporters import convert_pages_to_docx

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        convert_pages_to_docx(self.pages, out)
        return out.resolve()

    @classmethod
    def from_any(cls, data: Any) -> "ExtractedDocument":
        if isinstance(data, cls):
            return data
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, list):
            # Raw `pages` list
            meta = DocumentMetadata(
                file_name="from_pages",
                source_path="",
                source_format="pages_json",
                total_pages=len(data),
            )
            return build_structured_document(data, meta)
        if isinstance(data, dict):
            raw_meta = data.get("metadata") or {}
            pages = data.get("pages") or []
            meta = DocumentMetadata(
                file_name=str(raw_meta.get("file_name") or "unnamed"),
                source_path=str(raw_meta.get("source_path") or ""),
                source_format=str(raw_meta.get("source_format") or "unknown"),
                total_pages=int(raw_meta.get("total_pages") or len(pages)),
                language=str(raw_meta.get("language") or "ara"),
                author=raw_meta.get("author"),
                doc_date=raw_meta.get("doc_date"),
            )
            if pages and not (data.get("headings") or data.get("texts") or data.get("tables")):
                return build_structured_document(pages, meta)
            return cls(
                metadata=meta,
                headings=[HeadingItem(**h) for h in (data.get("headings") or [])],
                texts=[TextItem(**t) for t in (data.get("texts") or [])],
                tables=[TableItem(**tb) for tb in (data.get("tables") or [])],
                media=[MediaItem(**m) for m in (data.get("media") or [])],
                pages=pages,
            )
        raise TypeError(f"Unsupported input type for ExtractedDocument: {type(data)!r}")


def build_structured_document(
    pages: list[dict[str, Any]],
    metadata: DocumentMetadata,
) -> ExtractedDocument:
    """
    Walk unified `pages` blocks and populate separated `headings`, `texts`,
    `tables`, and `media` collections with hierarchical chapter tracking.
    """
    headings: list[HeadingItem] = []
    texts: list[TextItem] = []
    tables: list[TableItem] = []
    media: list[MediaItem] = []

    heading_stack: dict[int, str] = {}
    current_heading: str | None = None

    def _breadcrumb() -> str:
        return " > ".join(
            heading_stack[k] for k in sorted(heading_stack) if heading_stack[k]
        )

    for page in pages:
        p_num = int(page.get("metadata", {}).get("page_number") or 1)
        for blk in page.get("blocks", []):
            b_type = blk.get("type", "text")
            role = blk.get("role", "body")
            bbox = list(blk.get("bbox") or [0.0, 0.0, 0.0, 0.0])
            align = str(blk.get("alignment") or "right")

            # 1. Media / Image blocks
            if b_type in ("inline_cropped_image", "full_page_image_or_index"):
                img_path = str(blk.get("image_path") or "")
                if img_path:
                    media.append(
                        MediaItem(
                            index=len(media) + 1,
                            media_name=Path(img_path).name,
                            media_path=img_path,
                            media_type=b_type,
                            page_number=p_num,
                            alt_text=str(blk.get("alt_text") or ""),
                            bbox=bbox,
                        )
                    )
                continue

            # 2. Table blocks
            if b_type == "table":
                raw_rows = blk.get("rows") or []
                clean_rows: list[list[str]] = []
                for r in raw_rows:
                    clean_rows.append(
                        [
                            (c.get("text", "") if isinstance(c, dict) else str(c or "")).strip()
                            for c in r
                        ]
                    )
                if clean_rows:
                    col_cnt = int(
                        blk.get("col_count") or max((len(r) for r in clean_rows), default=0)
                    )
                    headers = clean_rows[0] if clean_rows else []
                    tbl_md = format_table_markdown(clean_rows, col_cnt)
                    tables.append(
                        TableItem(
                            index=len(tables) + 1,
                            col_count=col_cnt,
                            row_count=len(clean_rows),
                            headers=headers,
                            rows=clean_rows,
                            markdown=tbl_md,
                            page_number=p_num,
                            parent_heading=current_heading,
                            bbox=bbox,
                        )
                    )
                continue

            # 3. Headings vs Paragraphs / Lists
            txt = extract_block_text(blk)
            if not txt:
                continue

            is_heading = (
                role == "heading"
                or b_type in ("heading", "header")
                or bool(blk.get("is_heading"))
            )
            if is_heading:
                lvl = max(1, min(int(blk.get("heading_level") or 2), 6))
                heading_stack[lvl] = txt
                for deeper in [k for k in heading_stack if k > lvl]:
                    del heading_stack[deeper]
                current_heading = txt
                headings.append(
                    HeadingItem(
                        index=len(headings) + 1,
                        level=lvl,
                        text=txt,
                        page_number=p_num,
                        section_path=_breadcrumb(),
                        alignment=align,
                        bbox=bbox,
                    )
                )
            else:
                norm_role = "list_item" if role == "list_item" else "paragraph"
                texts.append(
                    TextItem(
                        index=len(texts) + 1,
                        role=norm_role,
                        text=txt,
                        page_number=p_num,
                        parent_heading=current_heading,
                        section_path=_breadcrumb(),
                        alignment=align,
                        is_ocr=bool(blk.get("ocr", False)),
                        bbox=bbox,
                    )
                )

    metadata.total_pages = len(pages)
    return ExtractedDocument(
        metadata=metadata,
        headings=headings,
        texts=texts,
        tables=tables,
        media=media,
        pages=pages,
    )

