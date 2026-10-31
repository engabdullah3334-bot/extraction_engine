# `extraction_engine` — Structured Multi-Format Document Extraction

**`extraction_engine`** is a standalone Python document intelligence package that converts verified files (`PDF`, `DOCX`, `HTML`, `TXT`, `MD`) into a cleanly separated structural object (`ExtractedDocument`) with built-in OpenCV visual quality verification, three-layer Tesseract OCR, and multi-format exporters.

### Engineering Problem Solved
Standard text extractors dump documents into flat, unstructured strings—destroying heading hierarchy, scrambling multi-column tables, losing inline figures, and silently dropping content on hybrid digital/scanned PDF pages. Downstream chunkers, translators, and databases then have to guess where chapters begin or where tables end.

`ExtractionEngine` solves this by returning an `ExtractedDocument` that cleanly separates **headings** (`HeadingItem`), **paragraphs & list items** (`TextItem`), **tables** (`TableItem` with cell matrices and reconstructed Markdown), and **media** (`MediaItem`), while retaining exact reading-order `pages` and offering one-call export to `.json`, `.md`, and styled `.docx`.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Usage](#usage)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Testing](#testing)
- [License and Contributing](#license-and-contributing)

---

## Prerequisites

- **Operating System:** Windows, Linux, or macOS.
- **Python:** `>= 3.10`
- **Python Libraries (`requirements.txt`):**
  - `PyMuPDF >= 1.23.0` (`fitz` — PDF layout & vector/table parsing)
  - `python-docx >= 1.1.0` & `lxml >= 5.0.0` (DOCX/HTML parsing & styled DOCX export)
  - `Pillow >= 10.0.0`, `numpy >= 1.26.0`, `opencv-python >= 4.8.0` (visual coverage QA & region cropping)
  - `pytesseract >= 0.3.10` (OCR Python wrapper)
- **External System Binary *(Optional, for Scanned PDFs/Images)*:**
  - **Tesseract OCR** (`tesseract >= 5.0`) available on system `PATH` with `ara` and/or `eng` language packs. Digital PDFs, DOCX, HTML, TXT, and Markdown files extract cleanly even without Tesseract installed.

---

## Installation

### Option A: Within the Full Workspace

```powershell
git clone <repository-url> engines
cd engines
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r extraction_engine/requirements.txt
```

### Option B: Standalone Copy into Any External Project

```powershell
Copy-Item -Recurse .\extraction_engine C:\path\to\your_project\extraction_engine
pip install -r C:\path\to\your_project\extraction_engine\requirements.txt
```

---

## Usage

### 1. Python API Example

```python
from extraction_engine import ExtractionEngine

extractor = ExtractionEngine(
    enable_ocr=True,
    ocr_lang="ara",
    media_dir="./extracted_media",
)

doc = extractor.extract("safe_files/handbook.pdf")

print("Format   :", doc.metadata.source_format, "| Pages:", doc.metadata.total_pages)
print("Headings :", len(doc.headings))
print("Texts    :", len(doc.texts))
print("Tables   :", len(doc.tables))
print("Media    :", len(doc.media))

# Export structured representations
doc.to_json("output/handbook_structured.json")
doc.to_markdown("output/handbook.md")
doc.to_docx("output/handbook_reconstructed.docx")
```

### 2. Command-Line Interface & Calm Standalone Web UI

```powershell
# Extract a document and export both structured JSON and Markdown
python -m extraction_engine safe_files/handbook.pdf --out-json handbook.json --out-md handbook.md

# Extract digital layers only (skip OCR)
python -m extraction_engine safe_files/handbook.pdf --no-ocr --out-docx rebuilt.docx

# Launch the calm standalone Web UI in your browser
python -m extraction_engine --ui
```

### 3. Expected Input & Output Shapes

- **Input:** Verified file path (`str | Path | os.PathLike`, supporting `.pdf`, `.docx`, `.html`, `.htm`, `.txt`, `.md`).
- **Output (`ExtractedDocument.to_dict()`):**
  ```json
  {
    "metadata": {
      "file_name": "handbook.pdf",
      "source_path": "C:/safe_files/handbook.pdf",
      "source_format": "pdf",
      "total_pages": 4,
      "language": "eng",
      "author": null,
      "doc_date": null,
      "extracted_at": "2026-10-30T19:05:00"
    },
    "headings": [
      {"index": 1, "level": 1, "text": "Chapter 1: Overview", "page_number": 1, "section_path": "Chapter 1: Overview"}
    ],
    "texts": [
      {"index": 1, "role": "paragraph", "text": "Introductory paragraph.", "page_number": 1, "parent_heading": "Chapter 1: Overview", "is_ocr": false}
    ],
    "tables": [
      {"index": 1, "col_count": 2, "row_count": 2, "headers": ["Stage", "Output"], "rows": [["Stage", "Output"], ["Extract", "JSON"]], "markdown": "| Stage | Output |\n| --- | --- |\n| Extract | JSON |", "page_number": 2}
    ],
    "media": [],
    "pages": [ ... ]
  }
  ```

---

## Architecture

### Package File Structure

```text
extraction_engine/
├── __init__.py          # Exports ExtractionEngine, ExtractedDocument, HeadingItem, TextItem, TableItem, MediaItem
├── __main__.py          # Standalone CLI, --ui launcher, and --self-test runner
├── engine.py            # Format dispatcher and ExtractionEngine facade
├── schemas.py           # Dataclass contracts & build_structured_document() separator
├── pdf_extractor.py     # Digital/mixed/scanned PDF extraction & deferred OCR orchestration
├── visual_check.py      # OpenCV ink-coverage & missing-region visual QA detector
├── ocr_engine.py        # 3-layer Tesseract OCR pipeline & document type classifier
├── docx_html_txt.py     # Native DOCX, HTML, TXT, and Markdown structural parsers
├── exporters.py         # Styled RTL/LTR-aware DOCX & Markdown reconstruction
├── utils.py             # Text normalization, table Markdown formatter, & Arabic script detection
├── requirements.txt     # Extraction & OCR package dependencies
├── ui/
│   ├── __init__.py
│   └── app.py           # Zero-dependency calm standalone HTTP Web UI (port 8702)
└── README.md
```

### Internal Data Flow

```text
Safe File (.pdf / .docx / .html / .txt / .md)
  │
  ├─► [PDF Path]  classify_document() (DIGITAL / MIXED / SCANNED)
  │     └─► smart_extract_pdf() + OpenCV visual_check -> deferred region/page OCR (if enable_ocr=True)
  ├─► [DOCX/HTML] Style, heading, list, table, & embedded media extraction
  └─► [TXT/MD]    ATX heading (`#`), bullet list, & pipe-table structural parser
        │
        ▼
  Unified Reading-Order `pages`
        │
        ▼
  `build_structured_document()` ──► `ExtractedDocument` (`headings`, `texts`, `tables`, `media`, `pages`)
```

---

## Configuration

| Parameter / Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enable_ocr` | `bool` | `True` | Enables Tesseract OCR on scanned PDF pages and uncovered visual regions. |
| `ocr_lang` | `str` | `"ara"` | Default Tesseract language code (`"ara"`, `"eng"`, `"ara+eng"`). Auto-detects script on PDFs when omitted per-call. |
| `media_dir` | `str \| Path \| None` | `extraction_engine/extracted_media` | Output directory for extracted figures and cropped visual blocks. |
| `page_range` | `tuple[int, int] \| None` | `None` (all pages) | Inclusive `(start_page, end_page)` 1-based slice for PDF extraction. |
| `SUPPORTED_FORMATS` | `frozenset[str]` | `{".pdf", ".docx", ".html", ".htm", ".txt", ".md"}` | File extensions accepted by `ExtractionEngine.extract()`. |
| `--port` (UI CLI flag) | `int` | `8702` (auto-fallback) | Localhost port for `python extraction_engine/ui/app.py`. |

---

## API Reference

### `ExtractionEngine`

- **`ExtractionEngine(enable_ocr: bool = True, ocr_lang: str = "ara", media_dir: str | Path | None = None)`**
  Creates a reusable extraction facade instance.
- **`extract(file_path, enable_ocr=None, ocr_lang=None, media_dir=None, page_range=None, progress_callback=None) -> ExtractedDocument`**
  Parses `file_path` (`str | Path | os.PathLike`) and returns an `ExtractedDocument`. `progress_callback` receives `(completed_steps: int, total_steps: int)`.

### `ExtractedDocument`

- **Attributes:** `metadata: DocumentMetadata`, `headings: list[HeadingItem]`, `texts: list[TextItem]`, `tables: list[TableItem]`, `media: list[MediaItem]`, `pages: list[dict]`, `plain_text: str`.
- **`to_dict() -> dict[str, Any]`** — Returns JSON-serializable dictionary representation.
- **`to_json(output_path: str | Path | None = None, indent: int = 2) -> str`** — Serializes (and optionally writes) UTF-8 JSON.
- **`to_markdown(output_path: str | Path | None = None) -> str`** — Composes unified Markdown preserving headings, lists, and tables.
- **`to_docx(output_path: str | Path) -> Path`** — Generates a styled Word `.docx` document with automatic RTL/LTR paragraph alignment.
- **`ExtractedDocument.from_any(data: Any) -> ExtractedDocument`** — Reconstructs an `ExtractedDocument` from a `dict`, JSON string, or raw `pages` list.

---

## Testing

Run the standalone self-test suite, which verifies structured Markdown/HTML extraction, heading/text/table separation, JSON round-tripping (`ExtractedDocument.from_any`), and Markdown export:

```powershell
python -m extraction_engine --self-test
```

---

## License and Contributing

- **License:** MIT License.
- **Contributing:** Maintain zero imports from sibling `*_engine` packages, keep UI logic isolated inside `ui/app.py`, document all code in English, and verify `python -m extraction_engine --self-test` passes before opening a Pull Request.
